from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterable

from .config import CONTEST_ID, SARUKU_DID
from .protocol import compact, request_id, submit
from .publisher import canonical_poem, weighted_length
from .state import StateStore


class PublicationDelivery(StrEnum):
    PLANNED = "PLANNED"
    SEND_INTENT_PERSISTED = "SEND_INTENT_PERSISTED"
    CONFIRMED = "CONFIRMED"
    DEFINITELY_NOT_SENT = "DEFINITELY_NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class SubmissionDelivery(StrEnum):
    SEND_INTENT_PERSISTED = "SEND_INTENT_PERSISTED"
    RECEIPT_ACCEPTED = "RECEIPT_ACCEPTED"
    RECEIPT_REJECTED = "RECEIPT_REJECTED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class PublicationIntegrity(StrEnum):
    MATCH = "PUBLICATION_INTEGRITY_MATCH"
    CONFLICT = "PUBLICATION_INTEGRITY_CONFLICT"
    UNRESOLVED = "PUBLICATION_INTEGRITY_UNRESOLVED"


@dataclass(frozen=True)
class FrozenPoemInput:
    game_id: str
    poem_room: str
    room_generation: int
    final_version: int
    final_state_hash: str
    final_contributor_did: str
    accepted_word_ledger: tuple[dict[str, Any], ...]
    authoritative_poem_sha256: str | None = None
    roster_fingerprint: str | None = None


@dataclass(frozen=True)
class FrozenReconciliation:
    ok: bool
    state_chain_status: str
    canonical_status: str
    canonical_poem: str | None
    poem_sha256: str | None
    failure_code: str | None


@dataclass(frozen=True)
class PublicationPart:
    publication_id: str
    game_id: str
    final_version: int
    poem_sha256: str
    part_index: int
    part_count: int
    exact_content: str
    content_sha256: str
    parent_part_index: int | None


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _canonical_from_ledger(source: FrozenPoemInput) -> FrozenReconciliation:
    ledger = sorted(source.accepted_word_ledger, key=lambda item: item.get("version", -1))
    if len(ledger) != source.final_version or [item.get("version") for item in ledger] != list(
        range(1, source.final_version + 1)
    ):
        return FrozenReconciliation(False, "INCOMPLETE", "INCOMPLETE", None, None,
                                    "FROZEN_POEM_RECONCILIATION_FAILED")
    final = ledger[-1] if ledger else {}
    if (
        final.get("state_hash") != source.final_state_hash
        or final.get("contributor_did") != source.final_contributor_did
        or final.get("complete") is not True
    ):
        return FrozenReconciliation(False, "CONFLICT", "INCOMPLETE", None, None,
                                    "FROZEN_POEM_RECONCILIATION_FAILED")
    lines = final.get("lines")
    if not isinstance(lines, list) or len(lines) != 14 or not all(
        isinstance(line, str) and line.strip() for line in lines
    ):
        return FrozenReconciliation(False, "MATCH", "INCOMPLETE", None, None,
                                    "FROZEN_POEM_RECONCILIATION_FAILED")
    poem = canonical_poem(lines)
    digest = hashlib.sha256(poem.encode("utf-8")).hexdigest()
    if source.authoritative_poem_sha256 is not None and source.authoritative_poem_sha256 != digest:
        return FrozenReconciliation(False, "MATCH", "INVALID", poem, digest,
                                    "FROZEN_POEM_RECONCILIATION_FAILED")
    return FrozenReconciliation(True, "MATCH", "COMPLETE", poem, digest, None)


def reconcile_frozen_poem(source: FrozenPoemInput) -> FrozenReconciliation:
    if not all((source.game_id, source.poem_room, source.final_state_hash,
                source.final_contributor_did)) or source.final_version <= 0:
        return FrozenReconciliation(False, "CONFLICT", "INVALID", None, None,
                                    "FROZEN_POEM_RECONCILIATION_FAILED")
    return _canonical_from_ledger(source)


def frozen_input_from_state(state: StateStore) -> FrozenPoemInput | None:
    game_id = state.active_team()
    poem_room = state.get("poem_room")
    setup = state.get("team_setup", {})
    final_version = state.get("poem_version")
    final_hash = state.get("poem_state_hash")
    final_contributor = state.get("final_contributor")
    generation = setup.get("room_generation") if isinstance(setup, dict) else None
    if not all((isinstance(game_id, str), isinstance(poem_room, str),
                isinstance(generation, int), isinstance(final_version, int),
                isinstance(final_hash, str), isinstance(final_contributor, str))):
        return None
    rows = state.db.execute(
        "SELECT payload FROM receipts WHERE room=? AND generation=? AND kind='word_accepted'",
        (poem_room, generation),
    ).fetchall()
    ledger = []
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("game_id") == game_id and payload.get("room_generation") == generation:
            ledger.append(payload)
    roster_fingerprint_value = None
    epoch_row = state.db.execute(
        "SELECT payload_json FROM formation_epochs WHERE game_id=? "
        "ORDER BY active DESC,updated_at DESC LIMIT 1", (game_id,),
    ).fetchone()
    if epoch_row is not None:
        try:
            epoch_payload = json.loads(epoch_row["payload_json"])
            roster_fingerprint_value = (
                epoch_payload.get("consent_roster_fingerprint")
                or epoch_payload.get("active_roster_fingerprint")
            )
        except (TypeError, json.JSONDecodeError):
            roster_fingerprint_value = None
    return FrozenPoemInput(
        game_id, poem_room, generation, final_version, final_hash, final_contributor,
        tuple(ledger),
        next((item.get("poem_sha256") for item in reversed(ledger)
              if isinstance(item.get("poem_sha256"), str)), None),
        roster_fingerprint_value if isinstance(roster_fingerprint_value, str) else None,
    )


def plan_publication(source: FrozenPoemInput, poem: str, poem_sha256: str,
                     limit: int = 280) -> list[PublicationPart]:
    attribution = f"{CONTEST_ID} · {source.game_id} · {source.final_contributor_did}"
    lines = poem.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else current + "\n" + line
        reserve = len("\n\n" + attribution) if line == lines[-1] else 0
        if current and weighted_length(candidate) + reserve > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
        if weighted_length(current) > limit:
            raise ValueError("one frozen poem line exceeds X limit")
    final = current + "\n\n" + attribution
    if weighted_length(final) > limit:
        chunks.append(current)
        final = attribution
    chunks.append(final)
    publication_id = hashlib.sha256(
        f"{source.game_id}:{source.final_version}:{poem_sha256}".encode()
    ).hexdigest()
    return [
        PublicationPart(
            publication_id, source.game_id, source.final_version, poem_sha256,
            index, len(chunks), content, hashlib.sha256(content.encode()).hexdigest(),
            index - 1 if index else None,
        )
        for index, content in enumerate(chunks)
    ]


def assess_publication_integrity(
    expected_parts: Iterable[dict[str, Any]], observed_posts: Iterable[dict[str, Any]] | None,
    expected_account_id: str, deadline: datetime,
    lookup_errors: Iterable[dict[str, Any]] | None = None,
) -> PublicationIntegrity:
    expected = list(expected_parts)
    if observed_posts is None:
        return PublicationIntegrity.UNRESOLVED
    observed = {item.get("id"): item for item in observed_posts if isinstance(item, dict)}
    errors_by_id: dict[str, list[dict[str, Any]]] = {}
    for error in lookup_errors or ():
        if not isinstance(error, dict):
            continue
        resource_id = error.get("resource_id") or error.get("value")
        if isinstance(resource_id, str):
            errors_by_id.setdefault(resource_id, []).append(error)
    unresolved = False
    for part in expected:
        post_id = part.get("x_post_id")
        item = observed.get(post_id)
        if item is None:
            matching_errors = errors_by_id.get(post_id, [])
            definitive = any(
                str(error.get("type", "")).rstrip("/").endswith("resource-not-found")
                or str(error.get("type", "")).rstrip("/").endswith("resource-deleted")
                or str(error.get("title", "")).lower() in {"not found", "deleted"}
                for error in matching_errors
            )
            if definitive:
                return PublicationIntegrity.CONFLICT
            unresolved = True
            continue
        try:
            created = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            unresolved = True
            continue
        if (
            item.get("author_id") != expected_account_id
            or item.get("text") != part.get("exact_content")
            or _utc(created) > _utc(deadline)
            or item.get("parent_post_id") != part.get("parent_post_id")
        ):
            return PublicationIntegrity.CONFLICT
        edit_history = item.get("edit_history_tweet_ids")
        if edit_history is not None and edit_history != [post_id]:
            return PublicationIntegrity.CONFLICT
    return PublicationIntegrity.UNRESOLVED if unresolved else PublicationIntegrity.MATCH


class EntryClosureStore:
    MAX_SUBMISSION_ATTEMPTS = 6

    def __init__(self, state: StateStore):
        self.state = state

    def save_reconciliation(self, source: FrozenPoemInput,
                            result: FrozenReconciliation, now: datetime) -> None:
        if not result.ok or result.canonical_poem is None or result.poem_sha256 is None:
            return
        stamp = _utc(now).isoformat()
        with self.state.db:
            self.state.db.execute(
                "INSERT INTO entry_closures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET state=excluded.state,"
                "failure_code=excluded.failure_code,updated_at=excluded.updated_at",
                (source.game_id, CONTEST_ID, source.poem_room, source.room_generation,
                 source.final_version, source.final_state_hash, source.final_contributor_did,
                 result.canonical_poem, result.poem_sha256, source.roster_fingerprint,
                 "FROZEN_POEM_RECONCILED", None, stamp, stamp),
            )
            self.state.db.execute(
                "INSERT INTO participant_commitments(contest_id,game_id,roster_fingerprint,state,updated_at) "
                "VALUES(?,?,?,'ACTIVE',?) ON CONFLICT(contest_id,game_id) DO NOTHING",
                (CONTEST_ID, source.game_id, source.roster_fingerprint, stamp),
            )

    def prepare_publication(self, source: FrozenPoemInput, result: FrozenReconciliation,
                            now: datetime) -> list[PublicationPart]:
        if source.final_contributor_did != SARUKU_DID:
            return []
        if not result.ok or result.canonical_poem is None or result.poem_sha256 is None:
            return []
        parts = plan_publication(source, result.canonical_poem, result.poem_sha256)
        with self.state.db:
            for part in parts:
                self.state.db.execute(
                    "INSERT OR IGNORE INTO publication_parts("
                    "publication_id,game_id,final_version,poem_sha256,part_index,part_count,"
                    "exact_content,content_sha256,parent_part_index,state) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (part.publication_id, part.game_id, part.final_version, part.poem_sha256,
                     part.part_index, part.part_count, part.exact_content, part.content_sha256,
                     part.parent_part_index, PublicationDelivery.PLANNED.value),
                )
        return parts

    def parts(self, game_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.state.db.execute(
            "SELECT * FROM publication_parts WHERE game_id=? ORDER BY part_index", (game_id,)
        )]

    def closure(self, game_id: str) -> dict[str, Any] | None:
        row = self.state.db.execute(
            "SELECT * FROM entry_closures WHERE game_id=?", (game_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def frozen_source(self, game_id: str) -> FrozenPoemInput | None:
        row = self.closure(game_id)
        if row is None:
            return None
        return FrozenPoemInput(
            row["game_id"], row["poem_room"], row["room_generation"],
            row["final_version"], row["final_state_hash"], row["final_contributor_did"],
            (), None, row["roster_fingerprint"],
        )

    def persist_publication_intent(self, publication_id: str, part_index: int,
                                   now: datetime) -> dict[str, Any] | None:
        with self.state.db:
            row = self.state.db.execute(
                "SELECT * FROM publication_parts WHERE publication_id=? AND part_index=?",
                (publication_id, part_index),
            ).fetchone()
            if row is None or row["state"] not in {
                PublicationDelivery.PLANNED.value, PublicationDelivery.DEFINITELY_NOT_SENT.value,
            }:
                return None
            if part_index and not row["parent_post_id"]:
                parent = self.state.db.execute(
                    "SELECT x_post_id,state FROM publication_parts WHERE publication_id=? AND part_index=?",
                    (publication_id, part_index - 1),
                ).fetchone()
                if parent is None or parent["state"] != PublicationDelivery.CONFIRMED.value:
                    return None
                self.state.db.execute(
                    "UPDATE publication_parts SET parent_post_id=? WHERE publication_id=? AND part_index=?",
                    (parent["x_post_id"], publication_id, part_index),
                )
            self.state.db.execute(
                "UPDATE publication_parts SET state=?,attempt_started_at=? "
                "WHERE publication_id=? AND part_index=?",
                (PublicationDelivery.SEND_INTENT_PERSISTED.value, _utc(now).isoformat(),
                 publication_id, part_index),
            )
        return dict(self.state.db.execute(
            "SELECT * FROM publication_parts WHERE publication_id=? AND part_index=?",
            (publication_id, part_index),
        ).fetchone())

    def publication_result(self, publication_id: str, part_index: int, state: PublicationDelivery,
                           now: datetime, *, post_id: str | None = None,
                           account_id: str | None = None, returned_text: str | None = None,
                           returned_parent_post_id: str | None = None,
                           expected_account_id: str | None = None) -> None:
        if state == PublicationDelivery.CONFIRMED and not all((
            post_id, account_id, returned_text, expected_account_id,
        )):
            raise ValueError("confirmed publication requires strong response evidence")
        with self.state.db:
            row = self.state.db.execute(
                "SELECT exact_content,parent_post_id FROM publication_parts "
                "WHERE publication_id=? AND part_index=?",
                (publication_id, part_index),
            ).fetchone()
            if row is None:
                raise ValueError("unknown publication part")
            strong = int(
                state == PublicationDelivery.CONFIRMED
                and returned_text == row["exact_content"]
                and account_id == expected_account_id
                and returned_parent_post_id == row["parent_post_id"]
            )
            self.state.db.execute(
                "UPDATE publication_parts SET state=?,x_post_id=?,confirmed_account_id=?,"
                "confirmed_text=?,confirmed_parent_post_id=?,strong_confirmation=?,confirmed_at=? "
                "WHERE publication_id=? AND part_index=?",
                (state.value, post_id, account_id, returned_text, returned_parent_post_id, strong,
                 _utc(now).isoformat() if state == PublicationDelivery.CONFIRMED else None,
                 publication_id, part_index),
            )

    def recover_unknown(self, publication_id: str, part_index: int,
                        matches: Iterable[dict[str, Any]], expected_account_id: str,
                        now: datetime) -> bool:
        row = self.state.db.execute(
            "SELECT * FROM publication_parts WHERE publication_id=? AND part_index=?",
            (publication_id, part_index),
        ).fetchone()
        if row is None or row["state"] != PublicationDelivery.DELIVERY_UNKNOWN.value:
            return False
        exact = [item for item in matches if item.get("author_id") == expected_account_id
                 and item.get("text") == row["exact_content"]
                 and item.get("parent_post_id") == row["parent_post_id"]
                 and isinstance(item.get("id"), str)
                 and isinstance(item.get("created_at"), str)]
        if row["attempt_started_at"]:
            attempted = datetime.fromisoformat(row["attempt_started_at"])
            exact = [item for item in exact if attempted - timedelta(minutes=5)
                     <= datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                     <= _utc(now) + timedelta(minutes=1)]
        if len(exact) != 1:
            return False
        self.publication_result(publication_id, part_index, PublicationDelivery.CONFIRMED,
                                now, post_id=exact[0]["id"], account_id=expected_account_id,
                                returned_text=exact[0]["text"],
                                returned_parent_post_id=exact[0].get("parent_post_id"),
                                expected_account_id=expected_account_id)
        return True

    def publication_complete(self, game_id: str) -> bool:
        rows = self.parts(game_id)
        return bool(rows) and all(row["state"] == PublicationDelivery.CONFIRMED.value
                                  and row["x_post_id"] for row in rows)

    def strong_confirmation(self, game_id: str) -> bool:
        rows = self.parts(game_id)
        return bool(rows) and all(row["state"] == PublicationDelivery.CONFIRMED.value
                                  and row["strong_confirmation"] for row in rows)

    def prepare_submission(self, source: FrozenPoemInput, poem_sha256: str,
                           integrity: PublicationIntegrity, now: datetime,
                           deadline: datetime, rid: str | None = None) -> dict[str, Any] | None:
        if source.final_contributor_did != SARUKU_DID:
            return None
        existing = self.state.db.execute(
            "SELECT packet_json FROM submission_intents WHERE game_id=? "
            "AND state IN (?,?) ORDER BY created_at DESC LIMIT 1",
            (source.game_id, SubmissionDelivery.SEND_INTENT_PERSISTED.value,
             SubmissionDelivery.DELIVERY_UNKNOWN.value),
        ).fetchone()
        if existing is not None:
            return json.loads(existing["packet_json"])
        if _utc(now) >= _utc(deadline) or not self.publication_complete(source.game_id):
            return None
        if integrity == PublicationIntegrity.CONFLICT or (
            integrity == PublicationIntegrity.UNRESOLVED and not self.strong_confirmation(source.game_id)
        ):
            return None
        ids = [row["x_post_id"] for row in self.parts(source.game_id)]
        packet = submit(source.game_id, source.poem_room, source.room_generation,
                        source.final_version, poem_sha256, ids, rid or request_id("submit"))
        encoded = compact(packet)
        with self.state.db:
            self.state.db.execute(
                "INSERT INTO submission_intents("
                "request_id,game_id,poem_room,room_generation,final_version,final_state_hash,"
                "poem_sha256,x_post_ids_json,packet_json,packet_sha256,state,created_at,"
                "last_attempt_at,attempt_count,next_retry_at,receipt_seq,entry_id,failure_code) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (packet["request_id"], source.game_id, source.poem_room, source.room_generation,
                 source.final_version, source.final_state_hash, poem_sha256,
                 json.dumps(ids, separators=(",", ":")), encoded,
                 hashlib.sha256(encoded.encode()).hexdigest(),
                 SubmissionDelivery.SEND_INTENT_PERSISTED.value, _utc(now).isoformat(),
                 None, 0, None, None, None, None),
            )
        return packet

    def mark_submission_unknown(self, request_id_value: str, now: datetime) -> None:
        with self.state.db:
            self.state.db.execute(
                "UPDATE submission_intents SET state=?,last_attempt_at=? WHERE request_id=?",
                (SubmissionDelivery.DELIVERY_UNKNOWN.value, _utc(now).isoformat(), request_id_value),
            )

    def pending_submission(self, game_id: str) -> dict[str, Any] | None:
        row = self.state.db.execute(
            "SELECT * FROM submission_intents WHERE game_id=? AND state IN (?,?) "
            "ORDER BY created_at DESC LIMIT 1",
            (game_id, SubmissionDelivery.SEND_INTENT_PERSISTED.value,
             SubmissionDelivery.DELIVERY_UNKNOWN.value),
        ).fetchone()
        return dict(row) if row is not None else None

    def submission_retry_due(self, game_id: str, now: datetime) -> dict[str, Any] | None:
        pending = self.pending_submission(game_id)
        if pending is None or int(pending.get("attempt_count") or 0) >= self.MAX_SUBMISSION_ATTEMPTS:
            return None
        retry_value = pending.get("next_retry_at")
        if isinstance(retry_value, str):
            try:
                if _utc(now) < _utc(datetime.fromisoformat(retry_value)):
                    return None
            except ValueError:
                return None
        return pending

    def mark_submission_attempt(self, request_id_value: str, now: datetime) -> None:
        row = self.state.db.execute(
            "SELECT attempt_count FROM submission_intents WHERE request_id=?",
            (request_id_value,),
        ).fetchone()
        if row is None:
            return
        attempt_count = int(row["attempt_count"] or 0) + 1
        delay_seconds = min(30 * (2 ** (attempt_count - 1)), 300)
        next_retry = _utc(now) + timedelta(seconds=delay_seconds)
        with self.state.db:
            self.state.db.execute(
                "UPDATE submission_intents SET state=?,last_attempt_at=?,attempt_count=?,"
                "next_retry_at=? WHERE request_id=? "
                "AND state IN (?,?)",
                (SubmissionDelivery.DELIVERY_UNKNOWN.value, _utc(now).isoformat(),
                 attempt_count, next_retry.isoformat(), request_id_value,
                 SubmissionDelivery.SEND_INTENT_PERSISTED.value,
                 SubmissionDelivery.DELIVERY_UNKNOWN.value),
            )

    def reject_submission(self, request_id_value: str, failure_code: str) -> None:
        with self.state.db:
            self.state.db.execute(
                "UPDATE submission_intents SET state=?,failure_code=? WHERE request_id=?",
                (SubmissionDelivery.RECEIPT_REJECTED.value, failure_code, request_id_value),
            )

    def accept_submission(self, game_id: str, request_id_value: str, entry_id: str,
                          receipt_seq: int, referee_did: str, now: datetime) -> bool:
        with self.state.db:
            row = self.state.db.execute(
                "SELECT 1 FROM submission_intents WHERE request_id=? AND game_id=?",
                (request_id_value, game_id),
            ).fetchone()
            if row is None:
                return False
            self.state.db.execute(
                "UPDATE submission_intents SET state=?,receipt_seq=?,entry_id=? WHERE request_id=?",
                (SubmissionDelivery.RECEIPT_ACCEPTED.value, receipt_seq, entry_id, request_id_value),
            )
            self.state.db.execute(
                "INSERT INTO participant_commitments(contest_id,game_id,state,updated_at,"
                "release_entry_id,release_submission_request_id,release_receipt_seq,release_referee_did) "
                "VALUES(?,?,'RELEASED',?,?,?,?,?) ON CONFLICT(contest_id,game_id) DO UPDATE SET "
                "state='RELEASED',release_entry_id=excluded.release_entry_id,"
                "release_submission_request_id=excluded.release_submission_request_id,"
                "release_receipt_seq=excluded.release_receipt_seq,"
                "release_referee_did=excluded.release_referee_did,updated_at=excluded.updated_at",
                (CONTEST_ID, game_id, _utc(now).isoformat(), entry_id, request_id_value,
                 receipt_seq, referee_did),
            )
        return True

    def commitment(self, game_id: str) -> dict[str, Any] | None:
        row = self.state.db.execute(
            "SELECT * FROM participant_commitments WHERE contest_id=? AND game_id=?",
            (CONTEST_ID, game_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def release_from_peer_receipt(self, game_id: str, request_id_value: str,
                                  entry_id: str, receipt_seq: int,
                                  referee_did: str, now: datetime) -> bool:
        closure = self.closure(game_id)
        if closure is None or closure["final_contributor_did"] == SARUKU_DID:
            return False
        with self.state.db:
            self.state.db.execute(
                "UPDATE participant_commitments SET state='RELEASED',release_entry_id=?,"
                "release_submission_request_id=?,release_receipt_seq=?,release_referee_did=?,"
                "updated_at=? WHERE contest_id=? AND game_id=?",
                (entry_id, request_id_value, receipt_seq, referee_did, _utc(now).isoformat(),
                 CONTEST_ID, game_id),
            )
        return True

    def release_from_generic_peer_receipt(self, payload: dict[str, Any], receipt_seq: int,
                                          referee_did: str, now: datetime) -> bool:
        """Release from the observed generic receipt schema.

        The live referee's accepted receipt has no game_id.  Its authoritative
        entry_id identifies the submitted game, and sender_did identifies the
        final contributor whose submission was accepted.
        """
        entry_id = payload.get("entry_id")
        sender_did = payload.get("sender_did")
        request_id_value = payload.get("request_id")
        if not all(isinstance(value, str) and value for value in (
            entry_id, sender_did, request_id_value
        )):
            return False
        closure = self.closure(entry_id)
        if (
            closure is None
            or closure["final_contributor_did"] == SARUKU_DID
            or closure["final_contributor_did"] != sender_did
        ):
            return False
        return self.release_from_peer_receipt(
            entry_id, request_id_value, entry_id, receipt_seq, referee_did, now
        )


class PeerFinalCoordinator:
    REMINDER_INTERVAL = timedelta(minutes=20)

    def __init__(self, store: EntryClosureStore):
        self.store = store

    def observe(self, source: FrozenPoemInput, now: datetime) -> None:
        stamp = _utc(now).isoformat()
        with self.store.state.db:
            self.store.state.db.execute(
                "INSERT INTO peer_endgame_state VALUES(?,?,?,?,?,NULL,0,NULL,'WAIT_PEER_PUBLICATION',?) "
                "ON CONFLICT(game_id) DO NOTHING",
                (source.game_id, source.final_contributor_did, source.final_version,
                 stamp, stamp, stamp),
            )

    def reminder(self, game_id: str, now: datetime, deadline: datetime) -> dict[str, Any] | None:
        row = self.store.state.db.execute(
            "SELECT * FROM peer_endgame_state WHERE game_id=?", (game_id,)
        ).fetchone()
        if row is None:
            return None
        if _utc(now) >= _utc(deadline):
            with self.store.state.db:
                self.store.state.db.execute(
                    "UPDATE peer_endgame_state SET state='PEER_DEADLINE_RECONCILING',updated_at=? "
                    "WHERE game_id=?", (_utc(now).isoformat(), game_id),
                )
            return None
        last = datetime.fromisoformat(row["last_progress_at"])
        if row["last_reminder_at"]:
            last = max(last, datetime.fromisoformat(row["last_reminder_at"]))
        if _utc(now) - _utc(last) < self.REMINDER_INTERVAL:
            return None
        dedupe = f"{game_id}:{row['final_version']}:{int(row['reminder_stage']) + 1}"
        with self.store.state.db:
            self.store.state.db.execute(
                "UPDATE peer_endgame_state SET last_reminder_at=?,reminder_stage=reminder_stage+1,"
                "dedupe_key=?,updated_at=? WHERE game_id=?",
                (_utc(now).isoformat(), dedupe, _utc(now).isoformat(), game_id),
            )
        return {"type": "sonnet.note.v1", "contest_id": CONTEST_ID, "game_id": game_id,
                "request_id": request_id("endgame-reminder"),
                "text": "The poem is frozen. Publication and submission are still pending."}

    def meaningful_progress(self, game_id: str, kind: str, now: datetime) -> bool:
        if kind not in {"publication_evidence", "submission_evidence", "trusted_receipt"}:
            return False
        with self.store.state.db:
            changed = self.store.state.db.execute(
                "UPDATE peer_endgame_state SET last_progress_at=?,updated_at=? WHERE game_id=?",
                (_utc(now).isoformat(), _utc(now).isoformat(), game_id),
            ).rowcount
        return bool(changed)

    def authoritative_no_submission(self, game_id: str, now: datetime) -> None:
        with self.store.state.db:
            self.store.state.db.execute(
                "UPDATE peer_endgame_state SET state='UNSUBMITTED_AT_DEADLINE',updated_at=? "
                "WHERE game_id=?", (_utc(now).isoformat(), game_id),
            )
