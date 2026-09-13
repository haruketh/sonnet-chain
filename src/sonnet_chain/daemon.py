from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from .cli import emit_or_post, signer_from
from .config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from .decision import (
    Action, build_decision_state, coordination_key, coordination_text, decide,
    validate_decision,
)
from .launch import TrustedLaunch, owner_did, verify_launch_record
from .journal import Journal
from .invites import (
    InviteCandidate,
    application_age_minutes,
    application_blocks_invite,
    application_expiry_reason,
    choose_invite,
    direct_invite,
    expired_application_games,
    invite_room_is_open,
    pending_application,
    record_time,
)
from .llm import LLMClient, LLMUnavailable, RECEIPT_SCHEMA
from .official import sha256, verify_package
from .poetry import PoemState, build_word_index
from .protocol import discovery_advertisement, register_writer, request_id, submit, team_application, withdraw, word
from .publisher import CommandPublisher, PublisherAuthRequired, PublisherUnavailable, canonical_poem
from .receipts import normalize_llm_receipt, receipt_candidate, receipt_matches
from .rosters import CanonicalRoster, current_roster_signers, roster_consensus, signed_roster, team_room_is_open
from .state import Phase, StateStore
from .team_intelligence import TeamIntelligence, capability_announcement
from .technocore import Technocore
from .writing import WritingPlanner, build_writing_context, validate_writing_candidate

def before_deadline(store: StateStore) -> bool:
    value = store.get("deadline")
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) <= deadline


class Daemon:
    def __init__(self, cfg: Config, live: bool = False, wait: int = 10):
        self.cfg = cfg
        self.live = live
        self.wait = max(0, min(10, wait))
        self.state = StateStore(cfg.state_db)
        self.tc = Technocore(cfg.technocore_url)
        self.journal = Journal(cfg.state_db.parent / "journal" / "sonnet.jsonl")

    def close(self) -> None:
        self.tc.close()
        self.state.close()

    def _journal(self, event: str, **data: Any) -> None:
        journal = getattr(self, "journal", None)
        if journal is None:
            return
        try:
            journal.append(event, **data)
        except OSError:
            # Journaling is observability only; never stop contest execution.
            pass

    def _post(self, room: str, payload: dict) -> None:
        if not self.live:
            raise RuntimeError("internal safety gate refused a non-live POST")
        self.state.increment("technocore_write_attempts")
        emit_or_post(self.cfg, room, payload, True)
        self._journal(
            "technocore_post_succeeded",
            room=room,
            type=payload.get("type"),
            request_id=payload.get("request_id"),
            game_id=payload.get("game_id"),
            word=payload.get("word"),
            members=payload.get("members"),
            poem_room=payload.get("poem_room"),
            room_generation=payload.get("room_generation"),
        )

    def _read(self, room: str) -> list[dict[str, Any]]:
        cursor, stored_generation = self.state.cursor(room)
        records, generation = self.tc.read_page(room, cursor, self.wait)
        if stored_generation is not None and generation is not None and generation != stored_generation:
            self.state.reset_cursor(room, generation)
            records, generation = self.tc.read_page(room, 0, 0)
        elif stored_generation is None and generation is not None and not records:
            self.state.reset_cursor(room, generation)
        fresh = []
        for record in records:
            if self.state.record_event(room, record.seq, generation, record.raw):
                fresh.append(record.raw)
        return fresh

    def _verify_local_launch(self, launch: TrustedLaunch) -> dict:
        manifest = self.cfg.official_dir / "manifest.json"
        if sha256(manifest) != launch.manifest_sha256:
            raise RuntimeError("launch manifest hash does not match downloaded official package")
        contest = verify_package(self.cfg.official_dir, self.cfg.official_commit, launch.manifest_sha256)
        if contest.get("contest_id") != CONTEST_ID:
            raise RuntimeError("official contest_id mismatch")
        for key, expected in launch.contest.items():
            if key in {"contest_id", "opening", "deadline", "rules_version"} and contest.get(key) != expected:
                raise RuntimeError(f"launch contest configuration mismatch: {key}")
        cache = self.cfg.state_db.parent / f"words-{self.cfg.official_commit[:12]}.json"
        if not cache.exists():
            build_word_index(self.cfg.official_dir / "cmudict.dict", cache)
        return contest

    def _wait_launch(self) -> None:
        owner = owner_did(self.tc.owner_note(self.cfg.rules_room))
        self._read(self.cfg.rules_room)
        self.state.set("last_error", None)
        if owner is None:
            self.state.set("rules_owner", None)
            return
        self.state.set("rules_owner", owner)
        for message in self.state.events(self.cfg.rules_room):
            launch = verify_launch_record(self.cfg.rules_room, owner, message)
            if launch is None:
                continue
            if self.cfg.referee_did and self.cfg.referee_did != launch.referee_did:
                raise RuntimeError("configured referee DID does not match signed launch")
            if self.cfg.manifest_sha256 and self.cfg.manifest_sha256 != launch.manifest_sha256:
                raise RuntimeError("configured manifest hash does not match signed launch")
            contest = self._verify_local_launch(launch)
            with self.state.transaction():
                self.state.set("trusted_launch", asdict(launch))
                self.state.set("referee_did", launch.referee_did)
                self.state.set("manifest_sha256", launch.manifest_sha256)
                self.state.set("deadline", contest.get("deadline"))
                self.state.phase = Phase.REGISTER
            return

    def _register(self) -> None:
        if not before_deadline(self.state):
            return
        if not self.cfg.x_account_url:
            return
        pending = self.state.pending_request("register")
        payload = pending or register_writer(self.cfg.x_account_url)
        if not self.live:
            self.state.set("dry_run_action", payload)
            return
        signer_from(self.cfg)
        if pending is None:
            self.state.reserve_request(payload["request_id"], "register", payload)
        self._post(ROOMS.registration, payload)
        self.state.phase = Phase.WAIT_REGISTRATION_RECEIPT

    def _receipts(self, room: str) -> None:
        referee = self.state.get("referee_did")
        if not referee:
            return
        self._read(room)
        for raw in self.state.events(room):
            receipt = receipt_candidate(room, raw, referee)
            if receipt is None:
                continue

            # Ignore referee receipts/notices that do not correspond to one of
            # this participant's pending requests. This prevents unrelated room
            # traffic from triggering LLM normalization.
            request_id = receipt.payload.get("request_id")
            pending = None
            if isinstance(request_id, str):
                row = self.state.db.execute(
                    "SELECT payload FROM requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                pending = json.loads(row["payload"]) if row else None
                if pending is not None:
                    pending["request_id"] = request_id

            if pending is None:
                continue

            if (
                receipt.kind == "unknown"
                and receipt.payload.get("type") != "sonnet.receipt.v1"
                and self.cfg.openai_api_key_file is not None
            ):
                try:
                    normalized = LLMClient(self.cfg.openai_api_key_file, self.cfg.model).structured(
                        "Normalize this cryptographically verified referee message. Extract only explicit facts; use unknown if ambiguous.",
                        {"room": room, "referee_did": referee, "exact_text": raw.get("text", "")},
                        "referee_receipt", RECEIPT_SCHEMA,
                    )
                    receipt = normalize_llm_receipt(normalized)
                except LLMUnavailable as exc:
                    self.state.set("last_error", f"LLMUnavailable: {exc}")
            accepted = bool(pending and receipt_matches(receipt, pending))
            with self.state.db:
                inserted = self.state.db.execute(
                    "INSERT OR IGNORE INTO receipts(room,generation,seq,kind,payload,accepted) VALUES(?,?,?,?,?,?)",
                    (room, int(raw.get("_room_generation") or -1), int(raw.get("seq", 0)), receipt.kind,
                     json.dumps(receipt.payload), int(accepted)),
                ).rowcount
            if not inserted:
                continue
            if not accepted:
                continue
            self._journal(
                "receipt_accepted",
                room=room,
                kind=receipt.kind,
                request_id=request_id,
                status=receipt.payload.get("status"),
                roster_ready=receipt.payload.get("roster_ready"),
                state_hash=receipt.payload.get("state_hash"),
                game_id=pending.get("game_id") if pending else None,
            )
            if receipt.kind == "registration_accepted":
                self.state.set_request_status(request_id, "accepted")
                self.state.set("registered", True)
                self.state.phase = Phase.DISCOVERY
            elif receipt.kind == "registration_rejected":
                self.state.set_request_status(request_id, "rejected")
            elif receipt.kind == "roster_consent_accepted":
                self.state.set_request_status(request_id, "accepted")
                self.state.set("roster_consent_accepted", True)
            elif receipt.kind == "roster_ready":
                state_hash = receipt.payload.get("state_hash")
                if not isinstance(state_hash, str) or not state_hash:
                    continue
                self.state.set_request_status(request_id, "accepted")
                self.state.set("active_team", pending["game_id"])
                self.state.set("current_roster", pending["members"])
                self.state.set("poem_room", pending["poem_room"])
                self.state.set("room_generation", pending["room_generation"])
                self.state.set("team_setup", {
                    "game_id": pending["game_id"],
                    "poem_room": pending["poem_room"],
                    "room_generation": pending["room_generation"],
                    "members": pending["members"],
                })
                self.state.set("poem_version", 0)
                self.state.set("poem_state_hash", state_hash)
                self.state.set("poem_last_progress_at", datetime.now(timezone.utc).isoformat())
                self.state.set("roster_ready", True)
                self._clear_roster_wait_state()
                self.state.phase = Phase.WRITING
            elif receipt.kind == "roster_rejected":
                self.state.set_request_status(request_id, "rejected")
                self.state.set("active_team", None)
                self.state.set("team_setup", None)
                self._clear_roster_wait_state()
                self.state.phase = Phase.DISCOVERY
            elif receipt.kind == "word_accepted":
                old_version = int(self.state.get("poem_version", 0) or 0)
                self.state.set_request_status(request_id, "accepted")
                self.state.set("poem_version", receipt.payload.get("version"))
                self.state.set("poem_state_hash", receipt.payload.get("state_hash"))
                self.state.set("previous_contributor", receipt.payload.get("contributor_did"))
                if isinstance(receipt.payload.get("lines"), list):
                    self.state.set("poem_lines", receipt.payload["lines"])
                new_version = receipt.payload.get("version")
                if isinstance(new_version, int) and new_version > old_version:
                    self.state.set("poem_last_progress_at", datetime.now(timezone.utc).isoformat())
                if receipt.payload.get("complete") is True:
                    self.state.set("final_contributor", receipt.payload.get("contributor_did"))
                    self.state.phase = Phase.POEM_COMPLETE
            elif receipt.kind == "word_rejected":
                self.state.set_request_status(request_id, "rejected")
            elif receipt.kind == "submission_accepted":
                self.state.set_request_status(request_id, "accepted")
                self.state.set("submission_state", "accepted")
                self.state.phase = Phase.DONE
            elif receipt.kind == "submission_rejected":
                self.state.set_request_status(request_id, "rejected")
                self.state.set("submission_state", "rejected")

    def _discovery(self) -> None:
        self._read(ROOMS.discovery)
        if not self.state.get("registered") or not before_deadline(self.state):
            return
        if self.state.active_team() is None and self.cfg.x_account_url:
            journal_path = self.cfg.state_db.parent / "journal" / "sonnet.jsonl"
            _, discovery_generation = self.state.cursor(ROOMS.discovery)
            discovery_events = [
                event for event in self.state.events(ROOMS.discovery)
                if event.get("_room_generation") == discovery_generation
            ]
            parsed_rosters = [
                (item, parsed)
                for item in discovery_events
                if (parsed := signed_roster(item)) is not None
            ]
            signed_games = {
                parsed[0].game_id
                for _, parsed in parsed_rosters
                if parsed[1] == SARUKU_DID
            }
            roster_games = {
                parsed[0].game_id
                for _, parsed in parsed_rosters
                if SARUKU_DID in parsed[0].members
            }
            pending_app = pending_application(journal_path)
            now = datetime.now(timezone.utc)
            progressed_at = None
            if pending_app is not None:
                for item, parsed in parsed_rosters:
                    if (
                        parsed[0].game_id != pending_app.game_id
                        or SARUKU_DID not in parsed[0].members
                    ):
                        continue
                    stamp = record_time(item)
                    if stamp is not None and (
                        progressed_at is None or stamp > progressed_at
                    ):
                        progressed_at = stamp
            age = (
                application_age_minutes(pending_app, now, progressed_at=progressed_at)
                if pending_app is not None
                else None
            )
            scan_candidates = pending_app is None or (
                age is not None and age >= 20
            )
            selected = None
            if scan_candidates:
                candidates = []
                for event in discovery_events:
                    invite = direct_invite(event)
                    if invite is None:
                        continue
                    if pending_app is not None and invite.game_id == pending_app.game_id:
                        continue
                    if (
                        invite.game_id in signed_games
                        or application_blocks_invite(journal_path, invite)
                        or self.state.pending_request(f"application:{invite.game_id}") is not None
                    ):
                        continue
                    room_verified = False
                    if invite.poem_room is not None:
                        referee = self.state.get("referee_did")
                        if not isinstance(referee, str):
                            continue
                        owner = self.tc.owner_note(invite.poem_room)
                        records, generation = self.tc.read_page(invite.poem_room, 0, 0)
                        if not invite_room_is_open(invite, referee, owner, generation, records):
                            continue
                        room_verified = True
                    candidates.append(InviteCandidate(invite, room_verified))
                selected = choose_invite(candidates)
            expiry_reason = None
            if pending_app is not None:
                expiry_reason = application_expiry_reason(
                    pending_app,
                    now=now,
                    active_team=self.state.active_team(),
                    signed_games=signed_games,
                    progressed_games=roster_games,
                    better_candidate_available=selected is not None,
                    progressed_at=progressed_at,
                )
                if expiry_reason is not None:
                    self._journal(
                        "team_application_expired",
                        game_id=pending_app.game_id,
                        request_id=pending_app.request_id,
                        reason=expiry_reason,
                    )
                    self.state.set_request_status(
                        pending_app.request_id,
                        "expired",
                    )
                    pending_app = None
                    if selected is None:
                        return
            if pending_app is None and selected is not None:
                payload = team_application(selected.game_id, self.cfg.x_account_url)
                if not self.live:
                    self.state.set("dry_run_action", payload)
                    return
                self.state.reserve_request(
                    payload["request_id"], f"application:{selected.game_id}", payload
                )
                self._post(ROOMS.discovery, payload)
                self._journal(
                    "team_application_sent",
                    game_id=selected.game_id,
                    target_from_did=selected.from_did,
                    request_id=payload["request_id"],
                    invite_seq=selected.seq,
                )
                return
        if not self.state.get("discovery_advertised"):
            if self.state.get("discovery_advertisement_attempted"):
                self.state.set("last_error", "Discovery advertisement outcome is ambiguous; refusing a duplicate")
                return
            payload = discovery_advertisement()
            if not self.live:
                self.state.set("dry_run_action", payload)
                return
            self.state.reserve_request(payload["request_id"], "discovery_advertisement", payload)
            self.state.set("discovery_advertisement_attempted", True)
            self._post(ROOMS.discovery, payload)
            self.state.set_request_status(payload["request_id"], "accepted")
            self.state.set("discovery_advertised", True)
            self.state.set("last_error", None)
            return
        pending_roster = self.state.pending_request("roster")
        if pending_roster is not None:
            self.state.phase = Phase.WAIT_ROSTER_READY
            return
        if self.state.active_team() is not None:
            return
        referee = self.state.get("referee_did")
        if not isinstance(referee, str):
            return
        _, discovery_generation = self.state.cursor(ROOMS.discovery)
        discovery_events = [
            event for event in self.state.events(ROOMS.discovery)
            if event.get("_room_generation") == discovery_generation
        ]
        journal_path = self.cfg.state_db.parent / "journal" / "sonnet.jsonl"
        pending_game = pending_application(journal_path)
        expired_games = expired_application_games(journal_path)
        anchor_signer = (
            pending_game.inviter_did
            if (
                pending_game is not None
                and pending_game.inviter_did is not None
                and pending_game.invite_seq is not None
            )
            else None
        )
        min_anchor_seq = (
            pending_game.invite_seq
            if anchor_signer is not None and pending_game is not None
            else None
        )
        for consensus in roster_consensus(
            discovery_events,
            anchor_signer=anchor_signer,
            min_anchor_seq=min_anchor_seq,
        ):
            proposal = consensus.roster
            if pending_game is not None and proposal.game_id != pending_game.game_id:
                continue
            if (
                pending_game is not None
                and pending_game.invite_seq is not None
                and consensus.completed_at_seq <= pending_game.invite_seq
            ):
                # Do not revive a roster completed before this fresh re-invite.
                continue
            if proposal.game_id in expired_games:
                continue
            owner = self.tc.owner_note(proposal.poem_room)
            records, generation = self.tc.read_page(proposal.poem_room, 0, 0)
            if not team_room_is_open(proposal, referee, owner, generation, records):
                continue
            payload = proposal.payload(request_id("roster"))
            self.state.set("roster_candidate", payload)
            if not self.live:
                self.state.set("dry_run_action", payload)
                return
            self.state.reserve_request(payload["request_id"], "roster", payload)
            if not self.state.select_team(proposal.game_id):
                return
            self.state.set("team_setup", {
                "game_id": proposal.game_id,
                "poem_room": proposal.poem_room,
                "room_generation": proposal.room_generation,
                "members": list(proposal.members),
            })
            wait_started = datetime.now(timezone.utc).isoformat()
            self.state.set("roster_wait_started_at", wait_started)
            self.state.set("roster_wait_last_progress_at", wait_started)
            self.state.set(
                "roster_wait_signers",
                sorted(set(consensus.signers) | {SARUKU_DID}),
            )
            self.state.set("roster_wait_soft_timeout_noted", False)
            self._post(ROOMS.discovery, payload)
            self.state.phase = Phase.WAIT_ROSTER_READY
            return

    def _clear_roster_wait_state(self) -> None:
        self.state.set("roster_wait_started_at", None)
        self.state.set("roster_wait_last_progress_at", None)
        self.state.set("roster_wait_signers", [])
        self.state.set("roster_wait_soft_timeout_noted", False)

    def _wait_roster_ready(self) -> None:
        # Referee resolution wins every race. Process receipts first.
        self._receipts(ROOMS.discovery)
        if self.state.phase != Phase.WAIT_ROSTER_READY:
            return

        pending = self.state.pending_request("roster")
        if not isinstance(pending, dict):
            self.state.set(
                "last_error",
                "WAIT_ROSTER_READY without a pending roster; refusing automatic action",
            )
            return

        game_id = pending.get("game_id")
        poem_room = pending.get("poem_room")
        generation = pending.get("room_generation")
        members = pending.get("members")
        if (
            not isinstance(game_id, str)
            or not isinstance(poem_room, str)
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or not isinstance(members, list)
            or not all(isinstance(member, str) for member in members)
            or SARUKU_DID not in members
            or self.state.active_team() != game_id
        ):
            self.state.set(
                "last_error",
                "invalid roster wait state; refusing automatic withdrawal",
            )
            return

        target = CanonicalRoster(
            game_id,
            poem_room,
            generation,
            tuple(members),
        )

        _, discovery_generation = self.state.cursor(ROOMS.discovery)
        discovery_events = [
            event
            for event in self.state.events(ROOMS.discovery)
            if event.get("_room_generation") == discovery_generation
        ]
        observed = set(current_roster_signers(discovery_events, target))
        # Our POST may be absent from the local read after an ambiguous
        # transport outcome. Treat our consent as potentially live.
        observed.add(SARUKU_DID)

        previous_raw = self.state.get("roster_wait_signers", [])
        previous = {
            signer
            for signer in previous_raw
            if isinstance(signer, str)
        } if isinstance(previous_raw, list) else {SARUKU_DID}

        additions = observed - previous
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        if additions:
            self.state.set("roster_wait_last_progress_at", now_iso)
            self._journal(
                "roster_wait_progress",
                game_id=game_id,
                added_signers=sorted(additions),
                signer_count=len(observed),
                member_count=len(members),
            )
        self.state.set("roster_wait_signers", sorted(observed))

        # Once every listed member is on the exact canonical roster, do not
        # auto-withdraw. At this point we only wait for the referee receipt.
        if set(members) <= observed:
            return

        raw_progress_at = self.state.get("roster_wait_last_progress_at")
        if not isinstance(raw_progress_at, str):
            self.state.set("roster_wait_last_progress_at", now_iso)
            return
        try:
            progress_at = datetime.fromisoformat(
                raw_progress_at.replace("Z", "+00:00")
            )
        except ValueError:
            self.state.set("roster_wait_last_progress_at", now_iso)
            return
        if progress_at.tzinfo is None:
            self.state.set("roster_wait_last_progress_at", now_iso)
            return
        age_minutes = max(
            0.0,
            (now - progress_at.astimezone(timezone.utc)).total_seconds() / 60,
        )

        if (
            age_minutes >= 20
            and not bool(self.state.get("roster_wait_soft_timeout_noted"))
        ):
            self.state.set("roster_wait_soft_timeout_noted", True)
            self._journal(
                "roster_wait_soft_timeout",
                game_id=game_id,
                minutes_since_progress=round(age_minutes, 2),
                signer_count=len(observed),
                member_count=len(members),
            )

        if age_minutes < 60:
            return

        # Fail closed on any hint that the roster may already be frozen.
        referee = self.state.get("referee_did")
        if not isinstance(referee, str):
            return
        owner = self.tc.owner_note(poem_room)
        room_records, actual_generation = self.tc.read_page(
            poem_room, 0, 0
        )
        if not team_room_is_open(
            target,
            referee,
            owner,
            actual_generation,
            room_records,
        ):
            self.state.set(
                "last_error",
                "team room may be frozen/closed; refusing automatic withdrawal",
            )
            return

        withdraw_pending = self.state.pending_request("withdraw")
        withdraw_payload = withdraw_pending or withdraw(game_id)
        if not self.live:
            self.state.set("dry_run_action", withdraw_payload)
            return

        if withdraw_pending is None:
            self.state.reserve_request(
                withdraw_payload["request_id"],
                "withdraw",
                withdraw_payload,
            )

        self._post(ROOMS.discovery, withdraw_payload)
        self.state.set_request_status(
            withdraw_payload["request_id"],
            "posted",
        )
        roster_request_id = pending.get("request_id")
        if isinstance(roster_request_id, str):
            self.state.set_request_status(
                roster_request_id,
                "withdrawn",
            )
        self._journal(
            "roster_wait_withdrawn",
            game_id=game_id,
            request_id=withdraw_payload["request_id"],
            minutes_since_progress=round(age_minutes, 2),
        )
        self.state.set("active_team", None)
        self.state.set("team_setup", None)
        self.state.set("roster_candidate", None)
        self.state.set("roster_consent_accepted", False)
        self._clear_roster_wait_state()
        self.state.set("last_error", None)
        self.state.phase = Phase.DISCOVERY

    def _ensure_team_intro(self, poem_room: str, game_id: str) -> bool:
        kind = f"team_capability_announcement:{game_id}"
        if self.state.db.execute("SELECT 1 FROM requests WHERE kind=? LIMIT 1", (kind,)).fetchone():
            return False
        payload = capability_announcement(game_id, request_id("capabilities"))
        if not self.live:
            self.state.set("dry_run_action", payload)
            return True
        # This is an application-local planning envelope, not a referee action;
        # it deliberately expects no receipt.
        self.state.reserve_request(payload["request_id"], kind, payload)
        self._post(poem_room, payload)
        return True

    def _writing(self) -> None:
        poem_room = self.state.get("poem_room")
        if not isinstance(poem_room, str):
            return
        self._receipts(poem_room)
        if self.state.phase != Phase.WRITING or not before_deadline(self.state):
            return
        game_id = self.state.active_team()
        setup = self.state.get("team_setup", {})
        generation = setup.get("room_generation") if isinstance(setup, dict) else None
        if not isinstance(game_id, str) or not isinstance(generation, int):
            return
        if self._ensure_team_intro(poem_room, game_id):
            return
        snapshot = None
        decision_llm = (
            LLMClient(self.cfg.openai_api_key_file, self.cfg.model)
            if self.cfg.openai_api_key_file is not None else None
        )
        try:
            intelligence = TeamIntelligence(
                self.state,
                decision_llm,
            )
            snapshot = intelligence.sync(
                game_id, poem_room, generation, self.state.get("current_roster", []),
                self.state.get("referee_did"),
                int(self.state.get("line_number", 1) or 1),
            )
        except Exception as exc:
            # Team understanding is observational; failure must not block the legacy writer.
            self.state.set("last_team_intelligence_error", type(exc).__name__)
            try:
                snapshot = TeamIntelligence(self.state).rebuild_snapshot(
                    game_id, poem_room, generation,
                    int(self.state.get("line_number", 1) or 1),
                )
            except Exception:
                persisted = TeamIntelligence(self.state).load_snapshot(game_id)
                if (
                    isinstance(persisted, dict)
                    and persisted.get("room") == poem_room
                    and persisted.get("room_generation") == generation
                ):
                    snapshot = persisted
                else:
                    roster = self.state.get("current_roster", [])
                    snapshot = {
                        "game_id": game_id, "room": poem_room,
                        "room_generation": generation, "current_version": int(
                            self.state.get("poem_version", 0) or 0
                        ),
                        "members": {
                            did: {"facts": {"roster_member": True,
                                             "has_contributed": did in self.state.get(
                                                 "accepted_contributions", []
                                             )}}
                            for did in roster if isinstance(did, str)
                        },
                        "active_proposals": [], "terminal_risks": [], "questions": [],
                        "ledger_high_watermark": "minimal-safe-context",
                    }
        if snapshot is None:
            return
        current = PoemState(
            version=int(self.state.get("poem_version", 0) or 0),
            state_hash=self.state.get("poem_state_hash") or "",
            words=self.state.get("poem_words", []),
            line_syllables=int(self.state.get("line_syllables", 0) or 0),
            line_number=int(self.state.get("line_number", 1) or 1),
            previous_contributor=self.state.get("previous_contributor"),
        )
        request_kind = f"word:{current.version}"
        pending = self.state.pending_request(request_kind)
        progress_at = self.state.get("poem_last_progress_at")
        if not isinstance(progress_at, str):
            progress_at = datetime.now(timezone.utc).isoformat()
            self.state.set("poem_last_progress_at", progress_at)
        sent_keys = {
            row["kind"].split(":", 1)[1] for row in self.state.db.execute(
                "SELECT kind FROM requests WHERE kind LIKE 'coordination:%'"
            )
        }
        runtime = {
            "game_id": game_id, "poem_room": poem_room, "room_generation": generation,
            "current_version": current.version, "current_state_hash": current.state_hash,
            "current_line": current.line_number,
            "current_line_syllables": current.line_syllables,
            "last_progress_at": progress_at, "previous_contributor": current.previous_contributor,
            "roster": self.state.get("current_roster", []), "poem_complete": False,
        }
        failure = self.state.get("writing_failure_state", {})
        if isinstance(failure, dict) and (
            failure.get("game_id"), failure.get("room_generation"),
            failure.get("version"), failure.get("state_hash")
        ) == (game_id, generation, current.version, current.state_hash):
            runtime.update({
                "quality_generation_exhausted_for_current_state": bool(
                    failure.get("quality_generation_exhausted")
                ),
                "emergency_fallback_attempted_for_current_state": bool(
                    failure.get("emergency_fallback_attempted")
                ),
                "saruku_no_feasible_word_for_current_state": bool(
                    failure.get("saruku_no_feasible_word")
                ),
            })
        now = datetime.now(timezone.utc)
        decision_state = build_decision_state(
            runtime=runtime, snapshot=snapshot, now=now, phase=self.state.phase,
            deadline_ok=before_deadline(self.state), pending_word=pending is not None,
            coordination_sent=sent_keys,
            commitment_waits=self.state.get("self_commitment_waits", {}),
        )
        self.state.set("self_commitment_waits", decision_state.commitment_wait_records)
        decision = decide(decision_state, now, decision_llm)
        self._journal(
            "team_decision", game_id=game_id, room_generation=generation,
            version=current.version, action=decision.action.value,
            reason_code=decision.reason_code, target_did=decision.target_did,
            coordination_intent=decision.coordination_intent,
            decision_source=decision.decision_source, wait_started_at=decision.wait_started_at,
            reconsider_at=decision.reconsider_at,
            escalation_stage=decision_state.escalation_stage.value,
            coverage_pressure=decision_state.coverage_pressure.value,
            coordination_sendable=decision_state.coordination_sendable,
            snapshot_id=decision_state.ledger_high_watermark,
            expected_state_hash_fingerprint=hashlib.sha256(current.state_hash.encode()).hexdigest()[:16],
        )
        if pending:
            if not self.live:
                self.state.set("dry_run_action", pending)
                return
            self._post(poem_room, pending)
            return
        if decision.action == Action.WAIT:
            return
        if not validate_decision(decision, decision_state):
            return
        if (
            self.state.active_team() != decision.expected_game_id
            or int(self.state.get("poem_version", -1)) != decision.expected_version
            or self.state.get("poem_state_hash") != decision.expected_state_hash
            or int(self.state.get("team_setup", {}).get("room_generation", -1))
            != decision.expected_room_generation
        ):
            return
        if decision.action == Action.COORDINATE:
            key = coordination_key(decision_state, decision.target_did,
                                   decision.coordination_intent or "")
            if key in sent_keys:
                return
            try:
                text = coordination_text(decision.coordination_intent or "", decision.target_did)
            except ValueError:
                return
            payload = {"type": "sonnet.note.v1", "contest_id": CONTEST_ID,
                       "game_id": game_id, "purpose": "team_coordination",
                       "request_id": request_id("coordination"), "text": text}
            if not self.live:
                self.state.set("dry_run_action", payload)
                return
            self.state.reserve_request(payload["request_id"], f"coordination:{key}", payload)
            self._post(poem_room, payload)
            return
        if decision.action != Action.SARUKU_PROPOSE_WORD:
            return
        context = build_writing_context(
            decision_state, snapshot, self.state.get("poem_lines", []), decision.reason_code
        )
        try:
            planner = WritingPlanner(self.state, self.cfg.official_dir, decision_llm)
            planned = planner.plan(context, decision_state)
        except (OSError, ValueError) as exc:
            self.state.set("last_error", f"WritingPlanner: {type(exc).__name__}")
            return
        candidate = planned.selected_word
        if candidate is None:
            return
        latest_setup = self.state.get("team_setup", {})
        if (
            self.state.active_team() != decision.expected_game_id
            or int(self.state.get("poem_version", -1)) != decision.expected_version
            or self.state.get("poem_state_hash") != decision.expected_state_hash
            or self.state.get("previous_contributor") != decision_state.previous_contributor
            or self.state.pending_request(request_kind) is not None
            or not isinstance(latest_setup, dict)
            or latest_setup.get("room_generation") != decision.expected_room_generation
        ):
            return
        final_validation = validate_writing_candidate(
            candidate, context, decision_state, planner.lexicon
        )
        if not final_validation.hard_valid:
            return
        payload = word(game_id, decision.expected_room_generation, current.version,
                       current.state_hash, candidate)
        if not self.live:
            self.state.set("dry_run_action", payload)
            return
        self.state.reserve_request(payload["request_id"], request_kind, payload)
        self._post(poem_room, payload)

    def _poem_complete(self) -> None:
        if self.state.get("final_contributor") == SARUKU_DID:
            self.state.phase = Phase.PUBLISH_IF_FINAL_CONTRIBUTOR
        else:
            self.state.phase = Phase.WAIT_SUBMISSION_RECEIPT

    def _publish(self) -> None:
        if not self.live or not before_deadline(self.state):
            return
        if self.state.get("x_post_ids"):
            self.state.phase = Phase.SUBMIT
            return
        lines = self.state.get("poem_lines", [])
        try:
            post_ids = CommandPublisher(self.cfg.x_publish_cmd).publish(canonical_poem(lines))
        except PublisherAuthRequired as exc:
            self.state.set("last_error", str(exc))
            self.state.phase = Phase.X_AUTH_REQUIRED
            return
        except (PublisherUnavailable, ValueError) as exc:
            self.state.set("last_error", f"PublisherUnavailable: {exc}")
            self.state.phase = Phase.WAIT_PUBLISHER
            return
        self.state.set("x_post_ids", post_ids)
        self.state.increment("x_write_count", len(post_ids))
        self.state.phase = Phase.SUBMIT

    def _submit(self) -> None:
        import hashlib

        if not self.live or not before_deadline(self.state):
            return
        lines = self.state.get("poem_lines", [])
        setup = self.state.get("team_setup", {})
        game_id = self.state.active_team()
        poem_room = self.state.get("poem_room")
        post_ids = self.state.get("x_post_ids", [])
        if not all((isinstance(game_id, str), isinstance(poem_room, str), isinstance(setup.get("room_generation"), int), post_ids)):
            return
        text = canonical_poem(lines)
        payload = self.state.pending_request("submit") or submit(
            game_id, poem_room, setup["room_generation"], int(self.state.get("poem_version", 0)),
            hashlib.sha256(text.encode()).hexdigest(), post_ids,
        )
        if self.state.pending_request("submit") is None:
            self.state.reserve_request(payload["request_id"], "submit", payload)
        self._post(ROOMS.submissions, payload)
        self.state.phase = Phase.WAIT_SUBMISSION_RECEIPT

    def cycle(self) -> None:
        phase = self.state.phase
        phase_before = phase
        if phase == Phase.WAIT_LAUNCH:
            self._wait_launch()
        elif phase == Phase.REGISTER:
            self._register()
        elif phase == Phase.WAIT_REGISTRATION_RECEIPT:
            self._receipts(ROOMS.registration)
        elif phase in {Phase.DISCOVERY, Phase.SELECT_TEAM}:
            self._discovery()
        elif phase in {Phase.NEGOTIATE, Phase.WAIT_TEAM_SETUP, Phase.ROSTER_CONSENT}:
            # Retired score/note/team-setup join states migrate safely back to
            # deterministic roster discovery without issuing a write.
            self.state.phase = Phase.DISCOVERY
        elif phase == Phase.WAIT_ROSTER_READY:
            self._wait_roster_ready()
        elif phase == Phase.WRITING:
            self._writing()
        elif phase == Phase.POEM_COMPLETE:
            self._poem_complete()
        elif phase == Phase.PUBLISH_IF_FINAL_CONTRIBUTOR:
            self._publish()
        elif phase == Phase.SUBMIT:
            self._submit()
        elif phase == Phase.WAIT_SUBMISSION_RECEIPT:
            self._receipts(ROOMS.submissions)

        phase_after = self.state.phase
        if phase_after != phase_before:
            self._journal(
                "phase_changed",
                from_phase=phase_before.value,
                to_phase=phase_after.value,
                active_team=self.state.active_team(),
            )

    def run(self, max_cycles: int | None = None) -> int:
        cycles = 0
        backoff = 1.0
        try:
            while max_cycles is None or cycles < max_cycles:
                try:
                    self.cycle()
                    backoff = 1.0
                except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                    self.state.set("last_error", f"{type(exc).__name__}: {exc}")
                    self._journal(
                        "runtime_error",
                        phase=self.state.phase.value,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                    if max_cycles is None:
                        time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                cycles += 1
            return 0
        finally:
            print(json.dumps(self.status(), ensure_ascii=False, indent=2))

    def status(self) -> dict[str, Any]:
        cursor, generation = self.state.cursor(self.cfg.rules_room)
        return {
            "phase": self.state.phase.value,
            "live": self.live,
            "rules_room": self.cfg.rules_room,
            "rules_owner": self.state.get("rules_owner"),
            "rules_last_seq": cursor,
            "rules_generation": generation,
            "referee_did": self.state.get("referee_did"),
            "active_team": self.state.active_team(),
            "last_error": self.state.get("last_error"),
            "technocore_write_attempts": self.state.get("technocore_write_attempts", 0),
            "x_write_count": self.state.get("x_write_count", 0),
        }
