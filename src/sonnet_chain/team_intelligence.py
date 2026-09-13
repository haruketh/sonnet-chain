from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import CONTEST_ID, SARUKU_DID
from .llm import LLMClient
from .poetry import did_letters, missing_letters
from .signing import verify_room_signature
from .state import StateStore

SEMANTIC_SCHEMA_VERSION = "team-intelligence.v1"
EXTRACTOR_VERSION = 1
REDUCER_VERSION = 1
MAX_ANALYSIS_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (60, 300)
CAPABILITY_TEXT = (
    "I’m Saruku. I can help coordinate turns, syllables, DID-letter constraints, and contributor coverage.\n"
    "If I become the final contributor, I can publish the exact frozen poem from my registered X "
    "account and submit it."
)
TRUSTED_SELF_ALIASES = frozenset({"saruku", "@saruku"})


def resolve_target(target_text: str | None, members: set[str]) -> str | None:
    if target_text in members:
        return target_text
    if (
        isinstance(target_text, str)
        and target_text.strip().casefold() in TRUSTED_SELF_ALIASES
        and SARUKU_DID in members
    ):
        return SARUKU_DID
    return None

TEAM_MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"type": "string"},
                    "value": {"type": ["string", "number", "boolean", "null"]},
                    "scope": {"type": "string", "enum": ["next_word", "current_line", "poem"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["predicate", "value", "scope", "confidence"],
                "additionalProperties": False,
            },
        },
        "proposals": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "proposal_type": {"type": "string"},
                    "proposal_mode": {"type": "string", "enum": [
                        "SELF_COMMITMENT", "NOMINATION", "REQUEST", "PREFERENCE"
                    ]},
                    "target_text": {"type": ["string", "null"]},
                    "value": {"type": ["string", "number", "boolean", "null"]},
                    "scope": {"type": "string", "enum": ["next_word", "current_line", "poem"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "requested_word": {"type": ["string", "null"], "maxLength": 64},
                    "lexical_constraint": {
                        "anyOf": [
                            {"type": "null"},
                            {"type": "object", "properties": {
                                "type": {"type": "string", "enum": ["prefix", "suffix"]},
                                "value": {"type": "string", "maxLength": 32},
                            }, "required": ["type", "value"], "additionalProperties": False},
                        ]
                    },
                    "semantic_constraint": {
                        "anyOf": [
                            {"type": "null"},
                            {"type": "object", "properties": {
                                "type": {"type": "string", "enum": ["topic"]},
                                "value": {"type": "string", "maxLength": 64},
                            }, "required": ["type", "value"], "additionalProperties": False},
                        ]
                    },
                },
                "required": ["proposal_type", "proposal_mode", "target_text", "value", "scope",
                             "confidence", "requested_word", "lexical_constraint",
                             "semantic_constraint"],
                "additionalProperties": False,
            },
        },
        "questions": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_text": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["text", "target_text", "confidence"],
                "additionalProperties": False,
            },
        },
        "constraints": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"type": "string"},
                    "value": {"type": ["string", "number", "boolean", "null"]},
                    "scope": {"type": "string", "enum": ["next_word", "current_line", "poem"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["predicate", "value", "scope", "confidence"],
                "additionalProperties": False,
            },
        },
        "retractions": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"type": "string"},
                    "scope": {"type": "string", "enum": ["next_word", "current_line", "poem"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["predicate", "scope", "confidence"],
                "additionalProperties": False,
            },
        },
        "unclassified": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
    },
    "required": ["claims", "proposals", "questions", "constraints", "retractions", "unclassified"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Source:
    game_id: str
    room: str
    generation: int
    seq: int
    speaker: str
    poem_version: int
    poem_line: int
    text_hash: str


def capability_announcement(game_id: str, request_id: str) -> dict[str, Any]:
    return {
        "type": "sonnet.note.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "purpose": "team_capabilities",
        "request_id": request_id,
        "text": CAPABILITY_TEXT,
    }


class TeamIntelligence:
    def __init__(self, store: StateStore, llm: LLMClient | None = None):
        self.store = store
        self.llm = llm

    def sync_sources(
        self, room: str, generation: int, allowed_senders: set[str],
        referee_did: str | None = None,
    ) -> list[dict[str, Any]]:
        """Verify each potentially relevant room record once across restarts."""
        scope = "referee" if referee_did is not None and allowed_senders == {referee_did} else "semantic"
        for raw in self.store.unprocessed_team_sources(room, generation, scope):
            try:
                seq = int(raw.get("seq", 0) or 0)
            except (TypeError, ValueError):
                continue
            sender, text = raw.get("from"), raw.get("text")
            if not isinstance(sender, str) or not isinstance(text, str):
                self.store.mark_team_source_processed(room, generation, seq, scope)
                continue
            cached = self.store.verified_team_source(room, generation, seq)
            if cached is not None:
                self.store.mark_team_source_processed(room, generation, seq, scope)
                continue
            if sender not in allowed_senders:
                # A room-open probe initially knows only the referee. Preserve
                # possible peer messages until the trusted roster is known.
                self.store.mark_team_source_processed(room, generation, seq, scope)
                continue
            receipt_verified = (
                sender == referee_did
                and self.store.has_verified_receipt(room, generation, seq)
            )
            if not receipt_verified and not verify_room_signature(
                room, sender, raw.get("nonce", ""), text, raw.get("sig", "")
            ):
                self.store.mark_team_source_processed(room, generation, seq, scope)
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            kind = payload.get("type") if isinstance(payload, dict) else None
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            self.store.persist_team_source(
                room, generation, seq, sender, raw, kind,
                request_id if isinstance(request_id, str) else None, scope,
            )
            if (
                sender == referee_did and isinstance(payload, dict)
                and payload.get("contest_id") == CONTEST_ID
                and (kind == "sonnet.word-accepted.v1" or (
                    kind == "sonnet.receipt.v1" and payload.get("status") == "accepted"
                ))
            ):
                self.store.close_team_room(room, generation, seq)
        return self.store.verified_team_sources(room, generation)

    @staticmethod
    def _event_id(source: Source, ordinal: int, event_type: str) -> str:
        raw = (
            f"{source.game_id}|{source.room}|{source.generation}|{source.seq}|"
            f"{EXTRACTOR_VERSION}|{ordinal}|{event_type}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _append(
        self,
        source: Source,
        ordinal: int,
        evidence_class: str,
        event_type: str,
        *,
        actor: str | None = None,
        subject: str | None = None,
        scope: str | None = None,
        predicate: str | None = None,
        value: Any = None,
        target_text: str | None = None,
        resolved_target: str | None = None,
        method: str = "deterministic",
        confidence: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> str:
        event_id = self._event_id(source, ordinal, event_type)
        payload = {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "source": {
                "room": source.room,
                "room_generation": source.generation,
                "seq": source.seq,
                "verified_speaker_did": source.speaker,
                "poem_version_at_observation": source.poem_version,
                "current_line_at_observation": source.poem_line,
                "source_text_sha256": source.text_hash,
            },
            "extraction": {
                "method": method,
                "extractor_version": EXTRACTOR_VERSION,
                "confidence": confidence,
            },
            "semantic": extra or {},
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT OR IGNORE INTO team_events("
                "event_id,game_id,room,room_generation,source_seq,source_ordinal,source_poem_version,"
                "evidence_class,event_type,actor_did,subject_did,scope,predicate,value_json,"
                "target_text,resolved_target_did,extraction_method,extraction_confidence,"
                "extractor_version,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id, source.game_id, source.room, source.generation, source.seq,
                    ordinal, source.poem_version, evidence_class, event_type, actor, subject, scope,
                    predicate, json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    target_text, resolved_target, method, confidence, EXTRACTOR_VERSION,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return event_id

    def _analysis_row(self, source: Source) -> Any:
        row = self.store.db.execute(
            "SELECT status,attempts,processed_at FROM team_message_analysis WHERE game_id=? AND room=? AND generation=? "
            "AND seq=? AND extractor_version=?",
            (source.game_id, source.room, source.generation, source.seq, EXTRACTOR_VERSION),
        ).fetchone()
        return row

    def _analysis_due(self, source: Source, now: datetime) -> bool:
        row = self._analysis_row(source)
        if row is None:
            return True
        if row["status"] in {"parsed", "parsed_empty", "terminal_error"}:
            return False
        attempts = int(row["attempts"])
        if attempts >= MAX_ANALYSIS_ATTEMPTS:
            return False
        processed = datetime.fromisoformat(str(row["processed_at"]) + "+00:00")
        delay = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        return (now - processed).total_seconds() >= delay

    def _record_analysis(self, source: Source, status: str, error: str | None = None) -> None:
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO team_message_analysis(game_id,room,generation,seq,extractor_version,"
                "status,attempts,last_error_code) VALUES(?,?,?,?,?,?,1,?) "
                "ON CONFLICT(game_id,room,generation,seq,extractor_version) DO UPDATE SET "
                "status=excluded.status,attempts=team_message_analysis.attempts+1,"
                "last_error_code=excluded.last_error_code,processed_at=CURRENT_TIMESTAMP",
                (source.game_id, source.room, source.generation, source.seq,
                 EXTRACTOR_VERSION, status, error),
            )

    @staticmethod
    def _valid_extraction(value: Any) -> dict[str, list[dict[str, Any]] | list[str]]:
        keys = {"claims", "proposals", "questions", "constraints", "retractions", "unclassified"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("invalid team extraction shape")
        if not all(isinstance(value[key], list) for key in keys):
            raise ValueError("invalid team extraction values")
        if any(not isinstance(item, dict) for key in keys - {"unclassified"} for item in value[key]):
            raise ValueError("invalid team extraction items")
        if any(not isinstance(item, str) for item in value["unclassified"]):
            raise ValueError("invalid unclassified items")
        required = {
            "claims": {"predicate", "value", "scope", "confidence"},
            "proposals": {"proposal_type", "proposal_mode", "target_text", "value", "scope",
                          "confidence", "requested_word", "lexical_constraint",
                          "semantic_constraint"},
            "questions": {"text", "target_text", "confidence"},
            "constraints": {"predicate", "value", "scope", "confidence"},
            "retractions": {"predicate", "scope", "confidence"},
        }
        for group, fields in required.items():
            for item in value[group]:
                confidence = item.get("confidence")
                if set(item) != fields or not isinstance(confidence, (int, float)):
                    raise ValueError("invalid team extraction item")
                if isinstance(confidence, bool) or not 0 <= confidence <= 1:
                    raise ValueError("invalid extraction confidence")
                if group != "questions" and item.get("scope") not in {
                    "next_word", "current_line", "poem"
                }:
                    raise ValueError("invalid semantic scope")
                if group == "proposals":
                    requested = item["requested_word"]
                    lexical = item["lexical_constraint"]
                    semantic = item["semantic_constraint"]
                    if requested is not None and (
                        not isinstance(requested, str) or not requested or len(requested) > 64
                    ):
                        raise ValueError("invalid requested word")
                    if lexical is not None and (
                        not isinstance(lexical, dict) or set(lexical) != {"type", "value"}
                        or lexical["type"] not in {"prefix", "suffix"}
                        or not isinstance(lexical["value"], str)
                        or not lexical["value"] or len(lexical["value"]) > 32
                    ):
                        raise ValueError("invalid lexical constraint")
                    if semantic is not None and (
                        not isinstance(semantic, dict) or set(semantic) != {"type", "value"}
                        or semantic["type"] != "topic"
                        or not isinstance(semantic["value"], str)
                        or not semantic["value"] or len(semantic["value"]) > 64
                    ):
                        raise ValueError("invalid semantic constraint")
        return value

    def _extract_planning(
        self, source: Source, text: str, members: set[str], llm_budget: list[int], now: datetime
    ) -> None:
        if not self._analysis_due(source, now) or llm_budget[0] <= 0:
            return
        llm_budget[0] -= 1
        if self.llm is None:
            self._record_analysis(source, "retryable_error", "llm_unavailable")
            return
        try:
            extracted = self._valid_extraction(self.llm.structured(
                "Extract claims, proposals, questions, constraints, and retractions from this signed "
                "planning text, including bounded requested_word, prefix/suffix lexical constraints, and "
                "topic semantic constraints when explicit. The verified speaker is supplied as metadata "
                "and must not be inferred from the text. Return natural-language targets only as target_text; "
                "never resolve aliases to DIDs.",
                {
                    "verified_speaker_did": source.speaker,
                    "room": source.room,
                    "room_generation": source.generation,
                    "seq": source.seq,
                    "current_poem_version": source.poem_version,
                    "text": text,
                },
                "team_message_understanding", TEAM_MESSAGE_SCHEMA,
            ))
        except ValueError as exc:
            self._record_analysis(source, "terminal_error", type(exc).__name__)
            return
        except Exception as exc:
            self._record_analysis(source, "retryable_error", type(exc).__name__)
            return
        ordinal = 0
        for item in extracted["claims"]:
            ordinal += 1
            self._append(
                source, ordinal, "CLAIM", "claim", actor=source.speaker, subject=source.speaker,
                scope=item.get("scope"), predicate=item.get("predicate"), value=item.get("value"),
                method="llm", confidence=float(item.get("confidence", 0)),
            )
        for item in extracted["proposals"]:
            ordinal += 1
            target_text = item.get("target_text") if isinstance(item.get("target_text"), str) else None
            mode = item["proposal_mode"]
            resolved = source.speaker if mode == "SELF_COMMITMENT" else resolve_target(
                target_text, members
            )
            self._append(
                source, ordinal, "CLAIM", "proposal", actor=source.speaker, subject=resolved,
                scope=item.get("scope"), predicate=item.get("proposal_type"), value=item.get("value"),
                target_text=target_text, resolved_target=resolved, method="llm",
                confidence=float(item.get("confidence", 0)),
                extra={"observed_at_version": source.poem_version,
                       "observed_at_line": source.poem_line,
                       "proposal_mode": mode,
                       "requested_word": item["requested_word"],
                       "lexical_constraint": item["lexical_constraint"],
                       "semantic_constraint": item["semantic_constraint"]},
            )
        for group, event_type in (("questions", "question"), ("constraints", "constraint"),
                                  ("retractions", "retraction")):
            for item in extracted[group]:
                ordinal += 1
                target_text = item.get("target_text") if isinstance(item.get("target_text"), str) else None
                resolved = resolve_target(target_text, members)
                self._append(
                    source, ordinal, "CLAIM", event_type, actor=source.speaker, subject=resolved,
                    scope=item.get("scope"), predicate=item.get("predicate"),
                    value=item.get("text", item.get("value")), target_text=target_text,
                    resolved_target=resolved, method="llm",
                    confidence=float(item.get("confidence", 0)),
                )
        for text_value in extracted["unclassified"]:
            ordinal += 1
            self._append(source, ordinal, "CLAIM", "unclassified", actor=source.speaker,
                         value=text_value, method="llm", confidence=1.0)
        self._record_analysis(source, "parsed" if ordinal else "parsed_empty")

    def sync(
        self,
        game_id: str,
        room: str,
        generation: int,
        members: list[str],
        referee_did: str | None,
        current_line: int = 1,
        max_llm_messages: int = 1,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        version = 0
        line = current_line
        now = now or datetime.now(timezone.utc)
        member_set = set(members)
        llm_budget = [max_llm_messages]
        self._materialize_member_facts(game_id, room, generation, members, referee_did, line)
        events = self.sync_sources(room, generation, member_set | ({referee_did} if referee_did else set()), referee_did)
        for raw in events:
            speaker = raw.get("from")
            text = raw.get("text")
            try:
                seq = int(raw.get("seq", 0))
            except (TypeError, ValueError):
                continue
            if not isinstance(speaker, str) or not isinstance(text, str):
                continue
            source = Source(
                game_id, room, generation, seq, speaker, version, line,
                hashlib.sha256(text.encode()).hexdigest(),
            )
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("contest_id") == CONTEST_ID:
                kind = payload.get("type")
                if speaker == referee_did and (
                    kind == "sonnet.word-accepted.v1"
                    or (kind == "sonnet.receipt.v1" and payload.get("status") == "accepted")
                ):
                    accepted_version = payload.get("version")
                    if isinstance(accepted_version, int) and not isinstance(accepted_version, bool):
                        version = max(version, accepted_version)
                        accepted_source = Source(
                            game_id, room, generation, seq, speaker, version, line, source.text_hash
                        )
                        self._append(
                            accepted_source, 0, "FACT", "poem_version", actor=speaker,
                            subject=payload.get("contributor_did"), predicate="current_version",
                            value=version,
                        )
                        contributor = payload.get("contributor_did")
                        if isinstance(contributor, str):
                            self._append(
                                accepted_source, 1, "FACT", "accepted_contribution",
                                actor=speaker, subject=contributor, predicate="has_contributed", value=True,
                            )
                        line_number = payload.get("line_number")
                        if isinstance(line_number, int) and not isinstance(line_number, bool):
                            line = max(line, line_number)
                            self._append(
                                accepted_source, 2, "FACT", "current_line", actor=speaker,
                                predicate="current_line", value=line_number,
                            )
                    self._record_analysis(source, "parsed")
                    continue
                if kind == "sonnet.word.v1":
                    self._append(
                        source, 0, "FACT", "protocol_word_proposed", actor=speaker,
                        subject=speaker, scope="next_word", predicate="word_proposed",
                        value=payload.get("word"),
                        extra={"observed_at_version": payload.get("version")},
                    )
                    self._record_analysis(source, "parsed")
                    continue
                if (
                    speaker == SARUKU_DID and kind == "sonnet.note.v1"
                    and payload.get("purpose") == "team_capabilities"
                ):
                    self._append(
                        source, 0, "CLAIM", "claim", actor=speaker, subject=speaker,
                        scope="poem", predicate="can_publish_x", value=True,
                    )
                    self._record_analysis(source, "parsed")
                    continue
                planning_text = payload.get("text")
                if isinstance(planning_text, str) and speaker in member_set:
                    self._extract_planning(source, planning_text, member_set, llm_budget, now)
                    continue
                self._record_analysis(source, "parsed_empty")
                continue
            if speaker in member_set:
                self._extract_planning(source, text, member_set, llm_budget, now)
        return self.rebuild_snapshot(game_id, room, generation, current_line)

    def _materialize_member_facts(self, game_id: str, room: str, generation: int,
                                  members: list[str], referee_did: str | None,
                                  current_line: int) -> None:
        roster_hash = hashlib.sha256("|".join(sorted(members)).encode()).hexdigest()
        source = Source(game_id, room, generation, 0, referee_did or "local", 0,
                        current_line, roster_hash)
        for index, member in enumerate(sorted(set(members))):
            base = index * 10
            common = {"actor": referee_did, "subject": member,
                      "extra": {"source_kind": "trusted_current_roster"}}
            self._append(source, base, "FACT", "roster_member", predicate="roster_member",
                         value=True, **common)
            self._append(source, base + 1, "FACT", "available_letters",
                         predicate="available_letters", value=sorted(did_letters(member)), **common)
            self._append(source, base + 2, "FACT", "missing_letters",
                         predicate="missing_letters", value=sorted(missing_letters(member)), **common)
            self._append(source, base + 3, "FACT", "contribution_status",
                         predicate="has_contributed", value=False, **common)

    def _rows(self, game_id: str, room: str, generation: int) -> list[dict[str, Any]]:
        rows = self.store.db.execute(
            "SELECT * FROM team_events WHERE game_id=? AND room=? AND room_generation=? "
            "ORDER BY room_generation,source_seq,source_ordinal,event_id",
            (game_id, room, generation),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def _ensure_risks(self, game_id: str, room: str, generation: int) -> None:
        for claim in self._rows(game_id, room, generation):
            if not (
                claim["evidence_class"] == "CLAIM"
                and claim["predicate"] == "can_publish_x"
                and claim["value"] is False
                and claim["subject_did"]
            ):
                continue
            source_data = claim["payload"]["source"]
            source = Source(
                game_id, claim["room"], claim["room_generation"], claim["source_seq"],
                source_data["verified_speaker_did"], claim["source_poem_version"],
                int(source_data.get("current_line_at_observation", 1)),
                source_data["source_text_sha256"],
            )
            risk_id = hashlib.sha256(
                f"risk|{claim['event_id']}|{REDUCER_VERSION}".encode()
            ).hexdigest()
            payload = {
                "schema_version": SEMANTIC_SCHEMA_VERSION,
                "source": source_data,
                "extraction": {"method": "deterministic", "extractor_version": EXTRACTOR_VERSION,
                               "confidence": 1.0},
                "semantic": {"basis_event_ids": [claim["event_id"]],
                             "condition": "subject becomes final contributor", "severity": "high"},
            }
            # Reducer-triggered inference materialization is deterministic and idempotent;
            # its stable ID preserves the append-only ledger on every rebuild.
            with self.store.db:
                self.store.db.execute(
                    "INSERT OR IGNORE INTO team_events(event_id,game_id,room,room_generation,source_seq,"
                    "source_ordinal,source_poem_version,evidence_class,event_type,actor_did,subject_did,scope,predicate,"
                    "value_json,target_text,resolved_target_did,extraction_method,extraction_confidence,"
                    "extractor_version,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        risk_id, game_id, source.room, source.generation, source.seq,
                        1_000_000 + int(claim["source_ordinal"]), source.poem_version,
                        "INFERENCE", "terminal_publish_risk", SARUKU_DID,
                        claim["subject_did"], "poem", "terminal_publish_risk", "true", None, None,
                        "deterministic", 1.0, EXTRACTOR_VERSION,
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

    def rebuild_snapshot(
        self, game_id: str, room: str, generation: int, current_line: int = 1
    ) -> dict[str, Any]:
        self._ensure_risks(game_id, room, generation)
        rows = self._rows(game_id, room, generation)
        version = max(
            (int(row["value"]) for row in rows if row["event_type"] == "poem_version"),
            default=0,
        )
        derived_line = max(
            (int(row["value"]) for row in rows if row["event_type"] == "current_line"),
            default=current_line,
        )
        contributors = [
            row["subject_did"] for row in rows
            if row["event_type"] == "accepted_contribution" and row["subject_did"]
        ]
        members: dict[str, dict[str, Any]] = {}
        claims: dict[tuple[str, str], list[dict[str, Any]]] = {}
        active_proposals, stale_proposals, superseded_proposals = [], [], []
        questions, constraints, unclassified, unresolved = [], [], [], []
        retractions = [row for row in rows if row["event_type"] == "retraction"]
        proposals = [row for row in rows if row["event_type"] == "proposal"]
        for row in rows:
            subject = row["subject_did"]
            if isinstance(subject, str):
                members.setdefault(subject, {"facts": {}, "claims": {}, "proposals_about_member": []})
            if row["evidence_class"] == "FACT" and subject:
                members[subject]["facts"][row["predicate"]] = row["value"]
            if row["evidence_class"] == "CLAIM" and row["event_type"] == "claim" and subject:
                claims.setdefault((subject, row["predicate"]), []).append(row)
            if row["event_type"] == "proposal":
                proposal = {
                    "event_id": row["event_id"], "actor_did": row["actor_did"],
                    "target_text": row["target_text"],
                    "resolved_target_did": row["resolved_target_did"],
                    "proposal_type": row["predicate"], "scope": row["scope"],
                    "proposal_mode": row["payload"]["semantic"].get("proposal_mode", "PREFERENCE"),
                    "requested_word": row["payload"]["semantic"].get("requested_word"),
                    "lexical_constraint": row["payload"]["semantic"].get("lexical_constraint"),
                    "semantic_constraint": row["payload"]["semantic"].get("semantic_constraint"),
                    "source_event_id": row["event_id"],
                    "extraction_confidence": row["extraction_confidence"],
                    "provenance": row["payload"]["source"],
                    "value": row["value"],
                    "observed_at_version": row["source_poem_version"],
                    "observed_at_line": row["payload"]["source"].get(
                        "current_line_at_observation", current_line
                    ),
                }
                superseded = any(
                    later["source_seq"] > row["source_seq"]
                    and later["actor_did"] == row["actor_did"]
                    and later["scope"] == row["scope"]
                    and later["predicate"] == row["predicate"]
                    for later in retractions
                ) or any(
                    row["scope"] == "poem"
                    and later["source_seq"] > row["source_seq"]
                    and later["actor_did"] == row["actor_did"]
                    and later["scope"] == row["scope"]
                    and later["predicate"] == row["predicate"]
                    for later in proposals
                )
                observed_line = row["payload"]["source"].get(
                    "current_line_at_observation", current_line
                )
                stale = (
                    row["scope"] == "next_word" and version > row["source_poem_version"]
                ) or (
                    row["scope"] == "current_line" and derived_line > observed_line
                )
                destination = (
                    superseded_proposals if superseded else stale_proposals if stale else active_proposals
                )
                destination.append(proposal)
                if subject:
                    members[subject]["proposals_about_member"].append(row["event_id"])
                elif row["target_text"]:
                    unresolved.append({"event_id": row["event_id"], "target_text": row["target_text"]})
            if row["event_type"] == "question":
                questions.append({"event_id": row["event_id"], "value": row["value"],
                                  "resolved_target_did": row["resolved_target_did"]})
            if row["event_type"] == "constraint":
                constraints.append({"event_id": row["event_id"], "actor_did": row["actor_did"],
                                    "predicate": row["predicate"], "scope": row["scope"],
                                    "value": row["value"],
                                    "source": row["payload"]["source"]})
            if row["event_type"] == "unclassified":
                unclassified.append({"event_id": row["event_id"], "actor_did": row["actor_did"],
                                     "value": row["value"],
                                     "source": row["payload"]["source"]})
        latest_claim_values = {}
        for (subject, predicate), history in claims.items():
            latest = history[-1]
            conflicts = [item["event_id"] for item in history[:-1] if item["value"] != latest["value"]]
            members[subject]["claims"][predicate] = {
                "latest": {"event_id": latest["event_id"], "value": latest["value"]},
                "prior_claim_event_ids": [item["event_id"] for item in history[:-1]],
                "conflicting_claim_event_ids": conflicts,
            }
            latest_claim_values[(subject, predicate)] = latest["value"]
        risks = []
        for row in rows:
            if row["event_type"] != "terminal_publish_risk":
                continue
            semantic = row["payload"]["semantic"]
            risks.append({
                "event_id": row["event_id"], "subject_did": row["subject_did"],
                "condition": semantic["condition"], "severity": semantic["severity"],
                "basis_event_ids": semantic["basis_event_ids"],
                "active": latest_claim_values.get((row["subject_did"], "can_publish_x")) is False,
            })
        analysis = self.store.db.execute(
            "SELECT room,generation,seq,status FROM team_message_analysis WHERE game_id=? "
            "AND room=? AND generation=? "
            "AND status IN ('retryable_error','terminal_error') ORDER BY generation,seq",
            (game_id, room, generation),
        ).fetchall()
        ids = sorted(row["event_id"] for row in rows)
        snapshot = {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "reducer_version": REDUCER_VERSION,
            "game_id": game_id,
            "room": room,
            "room_generation": generation,
            "current_version": version,
            "current_line": derived_line,
            "previous_contributor": contributors[-1] if contributors else None,
            "members": members,
            "active_proposals": active_proposals,
            "active_writing_requests": [
                proposal for proposal in active_proposals
                if proposal["proposal_mode"] == "REQUEST"
            ],
            "stale_proposals": stale_proposals,
            "superseded_proposals": superseded_proposals,
            "terminal_risks": risks,
            "questions": questions,
            "constraints": constraints,
            "unclassified": unclassified,
            "unresolved_mentions": unresolved,
            "unparsed_messages": {
                "count": len(analysis),
                "latest_source_refs": [dict(row) for row in analysis[-8:]],
            },
            "final_contributor_reserved": None,
            "ledger_high_watermark": hashlib.sha256("|".join(ids).encode()).hexdigest(),
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO team_context_snapshots(game_id,schema_version,reducer_version,"
                "ledger_high_watermark,payload_json) VALUES(?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET "
                "schema_version=excluded.schema_version,reducer_version=excluded.reducer_version,"
                "ledger_high_watermark=excluded.ledger_high_watermark,payload_json=excluded.payload_json,"
                "rebuilt_at=CURRENT_TIMESTAMP",
                (game_id, SEMANTIC_SCHEMA_VERSION, REDUCER_VERSION,
                 snapshot["ledger_high_watermark"], json.dumps(snapshot, separators=(",", ":"))),
            )
        return snapshot

    def load_snapshot(self, game_id: str) -> dict[str, Any] | None:
        row = self.store.db.execute(
            "SELECT payload_json FROM team_context_snapshots WHERE game_id=?", (game_id,)
        ).fetchone()
        return None if row is None else json.loads(row["payload_json"])
