from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .active_acquisition import ActiveAcquisition, ACTIVE_SEARCH_FRESHNESS
from .cli import emit_or_post, signer_from
from .config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from .formation_reflex import FormationReflexResponder
from .formation_records import VerifiedFormationRecord
from .decision import (
    Action, build_decision_state, coordination_key, coordination_text, decide,
    validate_decision,
)
from .launch import TrustedLaunch, owner_did, verify_launch_record
from .journal import Journal
from .invites import (
    DirectInvite, InviteCandidate, PendingApplication,
    application_age_minutes,
    application_expiry_reason,
    choose_invite,
    direct_invite,
    expired_application_games,
    pending_application,
    record_time,
)
from .llm import LLMClient, LLMUnavailable, RECEIPT_SCHEMA
from .official import sha256, verify_package
from .poetry import PoemState, build_word_index
from .protocol import discovery_advertisement, register_writer, request_id, submit, team_application, withdraw, word
from .publisher import CommandPublisher, PublisherAuthRequired, PublisherUnavailable, canonical_poem
from .receipts import NormalizedReceipt, normalize_llm_receipt, receipt_candidate, receipt_matches
from .rosters import (
    CanonicalRoster, current_roster_signers, roster_consensus, signed_roster,
    signed_withdrawal,
)
from .state import Phase, StateStore
from .team_intelligence import TeamIntelligence, capability_announcement
from .technocore import Technocore
from .signing import verify_room_signature
from .team_formation import (
    ApplicationDelivery, ConsentDelivery, FormationHistoryState, FormationOpportunity,
    FormationStage, TeamFormationStore,
    StructuralKey, formation_decision, formation_watchdog, materially_stronger,
    reduce_candidate_states, roster_fingerprint, start_epoch,
    targeted_recruitment_note,
)
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
        return self._read_incremental(room, getattr(self, "wait", 0))

    def _read_incremental(self, room: str, wait: int = 0) -> list[dict[str, Any]]:
        cursor, stored_generation = self.state.cursor(room)
        page = self.tc.read_page(room, cursor, wait)
        records, generation = page
        if stored_generation is not None and generation is not None and generation != stored_generation:
            self.state.reset_cursor(room, generation)
            page = self.tc.read_page(room, 0, 0)
            records, generation = page
        elif stored_generation is None and generation is not None and not records:
            self.state.reset_cursor(room, generation)
        fresh = []
        for record in records:
            raw = record.raw if hasattr(record, "raw") else record
            seq = record.seq if hasattr(record, "seq") else raw.get("seq", 0)
            if isinstance(raw, dict) and self.state.record_event(room, int(seq), generation, raw):
                fresh.append(raw)
        if generation is not None:
            rows = self.state.db.execute(
                "SELECT seq FROM events WHERE room=? AND generation=? ORDER BY seq",
                (room, generation),
            ).fetchall()
            history = (
                TeamFormationStore(self.state).load_history(room, generation)
                or FormationHistoryState(room, generation)
            )
            response_first = getattr(page, "first_seq", None)
            # On an incremental response first_seq is normally cursor+1, not
            # the retained-ring floor. It proves truncation only when it jumps
            # over the requested cursor (or during a baseline read from zero).
            retained_floor = (
                response_first
                if isinstance(response_first, int)
                and (cursor == 0 or response_first > cursor + 1)
                else None
            )
            history.observe(
                (row["seq"] for row in rows),
                available_from_seq=retained_floor,
                available_through_seq=getattr(page, "last_seq", None),
            )
            TeamFormationStore(self.state).save_history(history)
            if history.reconciliation_required:
                self._journal(
                    "formation_history_gap_detected", room=room, generation=generation,
                    gap_ranges=[{"start": gap.start, "end": gap.end} for gap in history.gap_ranges],
                    reason_code="missing_sequence_interval",
                )
        return fresh

    def _reconcile_history(
        self, room: str, generation: int, start_seq: int, end_seq: int,
    ) -> bool:
        """Actively repair a required interval, then recompute continuity."""
        formation = TeamFormationStore(self.state)
        existing = formation.load_history(room, generation)
        if (
            existing is not None and existing.available_from_seq is not None
            and start_seq < existing.available_from_seq
        ):
            # The authoritative retained ring has already proven this prefix
            # unavailable. Re-export cannot restore it; remain fail-closed.
            return False
        try:
            page = self.tc.export_history(room)
            records, exported_generation = page
            if exported_generation is not None and exported_generation != generation:
                raise RuntimeError("authoritative export generation changed")
            for record in records:
                self.state.record_event(room, record.seq, generation, record.raw)
            rows = self.state.db.execute(
                "SELECT seq FROM events WHERE room=? AND generation=? ORDER BY seq",
                (room, generation),
            ).fetchall()
            history = FormationHistoryState(room, generation)
            history.observe(
                (row["seq"] for row in rows),
                available_from_seq=getattr(page, "first_seq", None),
                available_through_seq=getattr(page, "last_seq", None),
            )
            formation.save_history(history)
            complete = history.is_complete(start_seq, end_seq)
        except Exception as exc:
            history = formation.load_history(room, generation) or FormationHistoryState(room, generation)
            history.reconciliation_required = True
            formation.save_history(history)
            self._journal(
                "formation_history_reconcile_failed", room=room, generation=generation,
                reason_code=f"export_{type(exc).__name__}",
                history_complete_through_seq=history.complete_through_seq,
            )
            return False
        if complete:
            self._journal(
                "formation_history_reconciled", room=room, generation=generation,
                reason_code="required_interval_complete", start_seq=start_seq,
                end_seq=end_seq, history_complete_through_seq=history.complete_through_seq,
            )
            return True
        self._journal(
            "formation_history_reconcile_failed", room=room, generation=generation,
            reason_code="export_still_incomplete", start_seq=start_seq, end_seq=end_seq,
            history_complete_through_seq=history.complete_through_seq,
        )
        return False

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

    def _trusted_writer_dids(self) -> set[str]:
        return self.state.trusted_writer_dids()

    def _team_room_open(self, candidate: Any, referee_did: str) -> bool:
        room = candidate.poem_room
        expected_generation = candidate.room_generation
        cache = getattr(self, "_team_room_open_cache", None)
        if cache is None:
            cache = self._team_room_open_cache = {}
        key = (room, expected_generation, referee_did)
        if key in cache:
            return cache[key]
        if owner_did(self.tc.owner_note(room)) != referee_did:
            cache[key] = False
            return False
        self._read_incremental(room, 0)
        _, actual_generation = self.state.cursor(room)
        if actual_generation != expected_generation:
            cache[key] = False
            return False
        TeamIntelligence(self.state).sync_sources(
            room, expected_generation, {referee_did}, referee_did,
        )
        if self.state.team_room_closed(room, expected_generation):
            cache[key] = False
            return False
        history = TeamFormationStore(self.state).load_history(room, expected_generation)
        cache[key] = bool(
            history is not None and history.is_complete(1, history.highest_observed_seq)
        )
        return cache[key]

    def _sync_formation_events(self) -> None:
        """Verify each relevant Discovery record once, then use durable facts."""
        trusted = self._trusted_writer_dids()
        for raw in self.state.unprocessed_formation_events(ROOMS.discovery):
            generation = int(raw.pop("_formation_generation"))
            seq = int(raw.pop("_formation_seq"))
            sender = raw.get("from")
            text_value = raw.get("text")
            try:
                payload = json.loads(text_value) if isinstance(text_value, str) else None
            except json.JSONDecodeError:
                payload = None
            if not isinstance(sender, str) or not isinstance(payload, dict):
                self.state.mark_formation_event_processed(ROOMS.discovery, generation, seq)
                continue
            kind = payload.get("type")
            normalized_kind = None
            game_id = payload.get("game_id") if isinstance(payload.get("game_id"), str) else None
            fingerprint = None
            # Structural routing ensures an event reaches at most one Ed25519 check.
            if kind == "sonnet.roster.v1":
                parsed = signed_roster(raw)
                if parsed is not None:
                    normalized_kind = "ROSTER_CONSENT"
                    game_id = parsed[0].game_id
                    fingerprint = roster_fingerprint(parsed[0])
            elif kind == "sonnet.withdraw.v1":
                parsed_withdrawal = signed_withdrawal(raw)
                if parsed_withdrawal is not None:
                    normalized_kind = "ROSTER_WITHDRAWAL"
                    game_id = parsed_withdrawal[0]
            elif kind == "sonnet.note.v1":
                opportunity = targeted_recruitment_note(raw, trusted)
                if opportunity is not None:
                    normalized_kind = "TARGETED_RECRUITMENT_NOTE"
            elif (
                kind == "sonnet.application.v1" and sender == SARUKU_DID
                and payload.get("contest_id") == CONTEST_ID and game_id is not None
                and verify_room_signature(
                    ROOMS.discovery, sender, raw.get("nonce", ""),
                    text_value, raw.get("sig", ""),
                )
            ):
                normalized_kind = "APPLICATION_READBACK"
            if normalized_kind is None:
                self.state.mark_formation_event_processed(ROOMS.discovery, generation, seq)
                continue
            stamp = record_time(raw)
            self.state.persist_formation_event(
                ROOMS.discovery, generation, seq, normalized_kind, game_id, sender,
                payload.get("request_id") if isinstance(payload.get("request_id"), str) else None,
                fingerprint, raw, stamp.isoformat() if stamp is not None else None,
            )
            if normalized_kind == "TARGETED_RECRUITMENT_NOTE" and opportunity is not None:
                if TeamFormationStore(self.state).save_opportunity(opportunity):
                    self._journal(
                        "formation_opportunity_observed", game_id=opportunity.game_id,
                        source_seq=opportunity.source_seq,
                        trusted_event_at=(opportunity.observed_at.isoformat()
                                          if opportunity.observed_at else None),
                        reason_code="trusted_targeted_recruitment_note",
                    )

    def _formation_records(
        self, generation: int | None = None, *, game_id: str | None = None,
        request_id: str | None = None, event_kind: str | None = None,
        min_seq: int | None = None, sender_did: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.state.formation_events(
            ROOMS.discovery, generation, game_id=game_id, request_id=request_id,
            event_kind=event_kind, min_seq=min_seq, sender_did=sender_did,
        )

    def _formation_invite_blocked(self, invite: Any) -> bool:
        rows = self.state.db.execute(
            "SELECT payload_json,active FROM formation_epochs WHERE game_id=?",
            (invite.game_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if bool(row["active"]):
                return True
            prior_seq = payload.get("starting_invite_seq")
            if isinstance(prior_seq, int) and invite.seq <= prior_seq:
                return True
        return False

    def _opportunity_has_live_structure(
        self, opportunity: FormationOpportunity, generation: int, referee: str,
    ) -> bool:
        records = self._formation_records(
            generation, game_id=opportunity.game_id, min_seq=opportunity.source_seq,
        )
        rosters = {
            parsed[0] for raw in records
            if (parsed := signed_roster(raw)) is not None and SARUKU_DID in parsed[0].members
        }
        for roster in rosters:
            signers = current_roster_signers(records, roster)
            if opportunity.inviter_did in signers and len(signers) >= 2 and self._team_room_open(roster, referee):
                return True
        return False

    def _reconcile_formation_transport(self) -> None:
        formation = TeamFormationStore(self.state)
        epoch = formation.active_epoch()
        if epoch is None:
            return
        withdraw_intent = self.state.unresolved_protocol_intent("withdraw")
        if withdraw_intent is not None and epoch.withdraw_pending:
            for raw in self._formation_records(request_id=withdraw_intent["request_id"]):
                if signed_withdrawal(raw) != (epoch.game_id, SARUKU_DID):
                    continue
                try:
                    payload = json.loads(raw["text"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue
                if payload.get("request_id") != withdraw_intent["request_id"]:
                    continue
                self.state.set_protocol_delivery(withdraw_intent["request_id"], "CONFIRMED")
                self.state.set_request_status(withdraw_intent["request_id"], "accepted")
                epoch.withdraw_pending = False
                epoch.status = "abandoned"
                formation.save_epoch(epoch, active=False)
                self.state.set("active_team", None)
                self._journal(
                    "formation_withdraw_reconciled", game_id=epoch.game_id,
                    epoch_id=epoch.epoch_id, request_id=withdraw_intent["request_id"],
                    reason_code="exact_discovery_readback",
                )
                self._journal(
                    "formation_epoch_ended", game_id=epoch.game_id,
                    epoch_id=epoch.epoch_id, reason_code="withdraw_confirmed",
                )
                return
        found: dict[str, Any] | None = None
        for raw in self._formation_records(request_id=epoch.application_request_id):
            if raw.get("from") != SARUKU_DID or (
                not isinstance(raw, VerifiedFormationRecord) and not verify_room_signature(
                ROOMS.discovery, SARUKU_DID, raw.get("nonce", ""),
                raw.get("text", ""), raw.get("sig", ""),
            )):
                continue
            try:
                payload = json.loads(raw["text"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("request_id") == epoch.application_request_id:
                found = raw
                break
        if found is not None and epoch.application_delivery_state == ApplicationDelivery.POSTED_UNCONFIRMED:
            stamp = record_time(found)
            if stamp is not None:
                epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
                epoch.application_source_seq = int(found.get("seq", 0) or 0)
                epoch.application_observed_at = stamp.isoformat()
                formation.save_epoch(epoch)
                self.state.set_protocol_delivery(epoch.application_request_id, ApplicationDelivery.CONFIRMED)
                self._journal(
                    "formation_application_confirmed", game_id=epoch.game_id,
                    request_id=epoch.application_request_id,
                    source_seq=epoch.application_source_seq,
                    reason_code="trusted_discovery_readback",
                )
            return
        if epoch.application_delivery_state == ApplicationDelivery.POSTED_UNCONFIRMED:
            deadline = datetime.fromisoformat(epoch.application_reconcile_deadline)
            if datetime.now(timezone.utc) >= deadline:
                epoch.application_delivery_state = ApplicationDelivery.DELIVERY_UNKNOWN
                epoch.status = "abandoned_unconfirmed"
                formation.save_epoch(epoch, active=False)
                self.state.set_protocol_delivery(epoch.application_request_id, ApplicationDelivery.DELIVERY_UNKNOWN)
                self.state.set_request_status(epoch.application_request_id, "delivery_unknown")
                self._journal(
                    "formation_application_delivery_unknown", game_id=epoch.game_id,
                    request_id=epoch.application_request_id,
                    reason_code="reconcile_deadline_elapsed",
                )
                self._journal(
                    "team_application_expired", game_id=epoch.game_id,
                    request_id=epoch.application_request_id, reason="delivery_unknown",
                )
                self._journal(
                    "formation_epoch_ended", game_id=epoch.game_id,
                    reason_code="abandoned_unconfirmed",
                )

    def _bootstrap_formation_state(self) -> None:
        formation = TeamFormationStore(self.state)
        if formation.active_epoch() is not None:
            return
        phase = self.state.phase
        if phase not in {Phase.DISCOVERY, Phase.WAIT_ROSTER_READY}:
            return
        row = self.state.db.execute(
            "SELECT request_id,kind,payload,status,created_at FROM requests "
            "WHERE (kind LIKE 'application:%' OR kind='roster' OR kind='withdraw') "
            "AND status IN ('pending','posted') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        roster_intent = self.state.unresolved_protocol_intent("roster")
        withdraw_intent = self.state.unresolved_protocol_intent("withdraw")
        if row is None and roster_intent is None and withdraw_intent is None:
            return
        payload = json.loads(row["payload"]) if row is not None else (
            roster_intent or withdraw_intent
        )["payload"]
        game_id = payload.get("game_id")
        if not isinstance(game_id, str):
            return
        opportunities = []
        trusted = self._trusted_writer_dids()
        for raw in self._formation_records(game_id=game_id):
            opportunity = targeted_recruitment_note(raw, trusted)
            if opportunity is not None and opportunity.game_id == game_id:
                opportunities.append(opportunity)
        opportunity = max(opportunities, key=lambda item: item.source_seq, default=None)
        inviter = opportunity.inviter_did if opportunity is not None else "did:key:zUnknown"
        source_seq = opportunity.source_seq if opportunity is not None else 0
        created_raw = row["created_at"] if row is not None else (
            roster_intent or withdraw_intent
        )["created_at"]
        try:
            created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            created = datetime.now(timezone.utc)
        if row is not None and row["kind"] == "roster" and roster_intent is None:
            roster_intent = self.state.protocol_intent(row["request_id"])
        if row is not None and row["kind"] == "roster" and roster_intent is None:
            self.state.persist_protocol_intent(
                row["request_id"], "roster", ROOMS.discovery, payload,
                ConsentDelivery.CONSENT_DELIVERY_UNKNOWN,
                game_id=game_id, created_at=created.isoformat(),
            )
            roster_intent = self.state.protocol_intent(row["request_id"])
        if row is not None and row["kind"] == "withdraw" and withdraw_intent is None:
            withdraw_intent = self.state.protocol_intent(row["request_id"])
        if row is not None and row["kind"] == "withdraw" and withdraw_intent is None:
            self.state.persist_protocol_intent(
                row["request_id"], "withdraw", ROOMS.discovery, payload,
                "DELIVERY_UNKNOWN", game_id=game_id, created_at=created.isoformat(),
            )
            withdraw_intent = self.state.protocol_intent(row["request_id"])
        application_request = (
            row["request_id"] if row is not None and str(row["kind"]).startswith("application:")
            else f"legacy-{game_id}-{source_seq}"
        )
        epoch = start_epoch(
            FormationOpportunity(game_id, inviter, source_seq, created),
            application_request, created,
        )
        if row is None or not str(row["kind"]).startswith("application:"):
            epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
            epoch.application_observed_at = created.isoformat()
        if roster_intent is not None:
            epoch.consent_delivery_state = ConsentDelivery(roster_intent["delivery_state"])
            epoch.consent_request_id = roster_intent["request_id"]
            epoch.consent_roster_fingerprint = roster_intent.get("roster_fingerprint")
            epoch.formation_stage = FormationStage.CONSENT_RECONCILING
        if withdraw_intent is not None:
            epoch.withdraw_pending = True
            epoch.withdraw_request_id = withdraw_intent["request_id"]
            if epoch.consent_delivery_state == ConsentDelivery.NOT_SENT:
                epoch.consent_delivery_state = ConsentDelivery.CONSENT_DELIVERY_UNKNOWN
            epoch.formation_stage = FormationStage.WAIT_ROSTER_READY
        formation.save_epoch(epoch)
        self._journal(
            "formation_epoch_started", game_id=game_id, epoch_id=epoch.epoch_id,
            request_id=application_request, reason_code="durable_history_reconstruction",
        )

    def _reduce_epoch_structure(
        self, epoch: Any, discovery_events: list[dict[str, Any]],
    ) -> datetime | None:
        relevant = []
        for raw in discovery_events:
            parsed = signed_roster(raw)
            withdrawn = signed_withdrawal(raw)
            if (
                (parsed is not None and parsed[0].game_id == epoch.game_id)
                or (withdrawn is not None and withdrawn[0] == epoch.game_id)
            ):
                relevant.append(raw)
        latest_activity_seq = max(
            (int(item.get("seq", 0) or 0) for item in relevant), default=0
        )
        activity_key = f"formation_activity_seq:{epoch.epoch_id}"
        prior_activity_seq = int(self.state.get(activity_key, 0) or 0)
        if latest_activity_seq > prior_activity_seq:
            self.state.set(activity_key, latest_activity_seq)
            self._journal(
                "formation_activity", game_id=epoch.game_id,
                epoch_id=epoch.epoch_id, source_seq=latest_activity_seq,
                reason_code="verified_formation_event",
            )
            for raw in relevant:
                seq = int(raw.get("seq", 0) or 0)
                if seq <= prior_activity_seq:
                    continue
                parsed = signed_roster(raw)
                withdrawn = signed_withdrawal(raw)
                if parsed is not None:
                    self._journal(
                        "formation_consent_changed", game_id=epoch.game_id,
                        epoch_id=epoch.epoch_id, source_seq=seq,
                        reason_code="latest_action_roster",
                    )
                elif withdrawn is not None:
                    self._journal(
                        "formation_consent_withdrawn", game_id=epoch.game_id,
                        epoch_id=epoch.epoch_id, source_seq=seq,
                        reason_code="latest_action_withdrawal",
                    )
        candidates: list[tuple[tuple[Any, ...], CanonicalRoster, StructuralKey, datetime, int]] = []
        seen: set[CanonicalRoster] = set()
        for raw in discovery_events:
            parsed = signed_roster(raw)
            if parsed is None or parsed[0] in seen:
                continue
            roster = parsed[0]
            if roster.game_id != epoch.game_id or SARUKU_DID not in roster.members:
                continue
            seen.add(roster)
            signers = current_roster_signers(discovery_events, roster)
            missing = len(set(roster.members) - set(signers))
            ready = set(roster.members) - {SARUKU_DID} <= set(signers)
            stage = FormationStage.READY_TO_COUNTERSIGN if ready else (
                FormationStage.ROSTER_PROGRESSING if len(signers) > 1
                else FormationStage.ROSTER_PROPOSED
            )
            key = StructuralKey(
                int({value: rank for rank, value in enumerate(FormationStage)}[stage]),
                True, True, epoch.inviter_did in signers, missing, len(signers),
            )
            related = [
                item for item in discovery_events
                if (parsed_item := signed_roster(item)) is not None
                and parsed_item[0] == roster and parsed_item[1] in signers
            ]
            stamps = [(record_time(item), int(item.get("seq", 0) or 0)) for item in related]
            stamps = [(stamp, seq) for stamp, seq in stamps if stamp is not None]
            at, seq = max(stamps, default=(datetime.now(timezone.utc), 0))
            candidates.append((key.comparison(), roster, key, at, seq))
        if not candidates:
            return None
        _, roster, key, at, seq = max(candidates, key=lambda item: item[0])
        previous_hwm = tuple(epoch.epoch_high_watermark_key) if epoch.epoch_high_watermark_key else None
        previous_fingerprint = epoch.active_roster_fingerprint
        lineage_progress, epoch_progress = epoch.observe_lineage(
            roster_fingerprint(roster), key, at, seq
        )
        if (
            previous_fingerprint is not None
            and previous_fingerprint != epoch.active_roster_fingerprint
        ):
            self._journal(
                "formation_replacement_started", game_id=epoch.game_id,
                epoch_id=epoch.epoch_id, source_seq=seq,
                roster_fingerprint=epoch.active_roster_fingerprint,
                reason_code="different_current_roster_lineage",
            )
        if epoch_progress:
            epoch.formation_stage = FormationStage(
                list(FormationStage)[key.stage_rank]
            )
            TeamFormationStore(self.state).save_epoch(epoch)
            self._journal(
                "formation_progress", game_id=epoch.game_id, source_seq=seq,
                structural_key=list(key.comparison()), reason_code="strict_hwm_improvement",
            )
        elif lineage_progress:
            TeamFormationStore(self.state).save_epoch(epoch)
            self._journal(
                "formation_replacement_progress", game_id=epoch.game_id,
                source_seq=seq, reason_code="lineage_local_improvement",
            )
        elif previous_hwm is not None and key.comparison() < previous_hwm:
            regression_key = f"formation_regression_seq:{epoch.epoch_id}"
            if int(self.state.get(regression_key, 0) or 0) < seq:
                self.state.set(regression_key, seq)
                self._journal(
                    "formation_regression", game_id=epoch.game_id,
                    epoch_id=epoch.epoch_id, source_seq=seq,
                    current_structural_key=list(key.comparison()),
                    reason_code="below_epoch_high_watermark",
                )
        if epoch.epoch_high_watermark_at:
            return datetime.fromisoformat(epoch.epoch_high_watermark_at)
        return None

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
        for raw in self.state.unprocessed_receipt_events(room):
            generation = int(raw.pop("_receipt_generation"))
            seq = int(raw.pop("_receipt_seq"))
            existing = self.state.db.execute(
                "SELECT kind,payload,accepted FROM receipts WHERE room=? "
                "AND generation=? AND seq=?", (room, generation, seq),
            ).fetchone()
            if existing is not None:
                receipt = NormalizedReceipt(
                    existing["kind"], json.loads(existing["payload"])
                )
            else:
                # Sender equality is a cheap relevance gate. Only messages
                # attributed to the pinned referee reach Ed25519 verification.
                if raw.get("from") != referee:
                    self.state.mark_receipt_event_processed(
                        room, generation, seq, "done"
                    )
                    continue
                if room == ROOMS.registration:
                    try:
                        structural = json.loads(raw.get("text", ""))
                    except (TypeError, json.JSONDecodeError):
                        structural = None
                    if (
                        not isinstance(structural, dict)
                        or structural.get("type") not in {
                            "sonnet.receipt.v1",
                            "sonnet.registration-accepted.v1",
                            "sonnet.registration-rejected.v1",
                        }
                    ):
                        self.state.mark_receipt_event_processed(
                            room, generation, seq, "done"
                        )
                        continue
                receipt = receipt_candidate(room, raw, referee)
            if receipt is None:
                self.state.mark_receipt_event_processed(
                    room, generation, seq, "done"
                )
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

            if (
                pending is not None
                and receipt.kind == "unknown"
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
            actionable = bool(pending and receipt_matches(receipt, pending))
            trusted_registration = (
                room == ROOMS.registration
                and receipt.kind == "registration_accepted"
            )
            accepted = actionable or trusted_registration
            if existing is None:
                with self.state.db:
                    self.state.db.execute(
                        "INSERT OR IGNORE INTO receipts(room,generation,seq,kind,payload,accepted) "
                        "VALUES(?,?,?,?,?,?)",
                        (room, generation, seq, receipt.kind,
                         json.dumps(receipt.payload), int(accepted)),
                    )
            self.state.mark_receipt_event_processed(room, generation, seq, "normalized")
            if not actionable:
                self.state.mark_receipt_event_processed(room, generation, seq, "done")
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
            elif receipt.kind == "application_rejected":
                self.state.set_request_status(request_id, "rejected")
                self.state.set_protocol_delivery(
                    request_id, ApplicationDelivery.EXPLICITLY_NOT_PERSISTED
                )
                epoch = TeamFormationStore(self.state).active_epoch()
                if epoch is not None and epoch.application_request_id == request_id:
                    epoch.application_delivery_state = ApplicationDelivery.EXPLICITLY_NOT_PERSISTED
                    epoch.status = "invalid"
                    TeamFormationStore(self.state).save_epoch(epoch, active=False)
                    self._journal(
                        "formation_epoch_ended", game_id=epoch.game_id,
                        epoch_id=epoch.epoch_id,
                        reason_code="trusted_application_rejection",
                    )
            elif receipt.kind == "roster_consent_accepted":
                self.state.set_request_status(request_id, "accepted")
                self.state.set("roster_consent_accepted", True)
                self.state.set_protocol_delivery(request_id, ConsentDelivery.CONSENT_CONFIRMED)
                epoch = TeamFormationStore(self.state).active_epoch()
                if epoch is not None and epoch.consent_request_id == request_id:
                    epoch.consent_delivery_state = ConsentDelivery.CONSENT_CONFIRMED
                    epoch.formation_stage = FormationStage.WAIT_ROSTER_READY
                    TeamFormationStore(self.state).save_epoch(epoch)
                self._journal(
                    "formation_consent_confirmed", request_id=request_id,
                    game_id=pending.get("game_id"), reason_code="trusted_referee_receipt",
                )
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
                self.state.set_protocol_delivery(request_id, ConsentDelivery.CONSENT_CONFIRMED)
                epoch = TeamFormationStore(self.state).active_epoch()
                if epoch is not None:
                    epoch.consent_delivery_state = ConsentDelivery.CONSENT_CONFIRMED
                    epoch.formation_stage = FormationStage.TEAM_READY
                    epoch.status = "team_ready"
                    TeamFormationStore(self.state).save_epoch(epoch, active=False)
                self._clear_roster_wait_state()
                self.state.phase = Phase.WRITING
            elif receipt.kind == "roster_rejected":
                self.state.set_request_status(request_id, "rejected")
                self.state.set_protocol_delivery(
                    request_id, ConsentDelivery.CONSENT_EXPLICITLY_NOT_PERSISTED
                )
                epoch = TeamFormationStore(self.state).active_epoch()
                if epoch is not None and epoch.consent_request_id == request_id:
                    epoch.consent_delivery_state = ConsentDelivery.CONSENT_EXPLICITLY_NOT_PERSISTED
                    TeamFormationStore(self.state).save_epoch(epoch)
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
            self.state.mark_receipt_event_processed(room, generation, seq, "done")

    def _discovery(self) -> None:
        self._team_room_open_cache = {}
        # Trusted referee terminal facts are processed before every local
        # formation heuristic.
        self._receipts(ROOMS.discovery)
        self._sync_formation_events()
        if self.state.phase != Phase.DISCOVERY:
            return
        # Recruitment authority comes from referee-accepted registration
        # evidence, never from a note's self-declared role.
        self._receipts(ROOMS.registration)
        self._bootstrap_formation_state()
        self._reconcile_formation_transport()
        _, reflex_generation = self.state.cursor(ROOMS.discovery)
        if (
            self.live and reflex_generation is not None
            and self.cfg.openai_api_key_file is not None
        ):
            reflex = FormationReflexResponder(
                self.state,
                LLMClient(
                    self.cfg.openai_api_key_file,
                    self.cfg.reflex_model or self.cfg.model,
                    timeout=10,
                ),
                self._journal, self._post, True,
            ).run_once(reflex_generation)
            if reflex.replied or (reflex.processed and reflex.payload is not None):
                return
            # Observe at most one currently relevant formation room per cycle.
            # Room ownership/generation/completeness are checked before peer
            # messages enter the durable verified source store.
            formation = TeamFormationStore(self.state)
            active_for_reflex = formation.active_epoch()
            opportunity = (
                formation.latest_opportunity(active_for_reflex.game_id)
                if active_for_reflex is not None else None
            )
            if opportunity is None and active_for_reflex is None:
                opportunity = next((
                    item for item in formation.opportunities()
                    if item.opportunity_kind == "TARGETED_INVITE"
                ), None)
            referee = self.state.get("referee_did")
            if (
                opportunity is not None and opportunity.poem_room is not None
                and opportunity.room_generation is not None and isinstance(referee, str)
                and self._team_room_open(opportunity, referee)
            ):
                TeamIntelligence(self.state).sync_sources(
                    opportunity.poem_room, opportunity.room_generation,
                    self._trusted_writer_dids() | {referee}, referee,
                )
                reflex = FormationReflexResponder(
                    self.state,
                    LLMClient(
                        self.cfg.openai_api_key_file,
                        self.cfg.reflex_model or self.cfg.model,
                        timeout=10,
                    ),
                    self._journal, self._post, True,
                ).run_once(
                    opportunity.room_generation, team_game_id=opportunity.game_id,
                    team_room=opportunity.poem_room,
                )
                if reflex.replied or (reflex.processed and reflex.payload is not None):
                    return
        active_epoch = TeamFormationStore(self.state).active_epoch()
        if active_epoch is not None and (
            active_epoch.possibly_consented or active_epoch.withdraw_pending
        ):
            self.state.set("last_error", "Roster consent delivery unresolved; reconciling fail-closed")
            return
        if (
            active_epoch is not None
            and active_epoch.application_delivery_state == ApplicationDelivery.POSTED_UNCONFIRMED
        ):
            # Application transport is still within its fixed reconciliation
            # window. Never create a second active application.
            return
        if not self.state.get("registered") or not before_deadline(self.state):
            return
        if self.state.active_team() is None and self.cfg.x_account_url:
            journal_path = self.cfg.state_db.parent / "journal" / "sonnet.jsonl"
            _, discovery_generation = self.state.cursor(ROOMS.discovery)
            scoped_game = active_epoch.game_id if active_epoch is not None else None
            discovery_events = self._formation_records(
                discovery_generation, game_id=scoped_game,
                min_seq=(active_epoch.starting_invite_seq if active_epoch is not None else None),
            ) if scoped_game is not None else []
            parsed_rosters = [
                (item, parsed)
                for item in discovery_events
                if (parsed := signed_roster(item)) is not None
            ]
            signed_games = {
                row["game_id"] for row in self.state.db.execute(
                    "SELECT DISTINCT game_id FROM formation_events WHERE room=? AND generation=? "
                    "AND verified=1 AND event_kind='ROSTER_CONSENT' AND sender_did=? "
                    "AND game_id IS NOT NULL",
                    (ROOMS.discovery, discovery_generation, SARUKU_DID),
                ).fetchall()
            }
            roster_games = {parsed[0].game_id for _, parsed in parsed_rosters}
            pending_app = pending_application(journal_path)
            if pending_app is not None and active_epoch is None:
                durable = self.state.db.execute(
                    "SELECT 1 FROM requests WHERE request_id=? AND kind LIKE 'application:%' "
                    "AND status IN ('pending','posted')",
                    (pending_app.request_id,),
                ).fetchone()
                if durable is None:
                    pending_app = None
            if active_epoch is not None and active_epoch.status == "active":
                observed = None
                if active_epoch.application_observed_at:
                    observed = datetime.fromisoformat(active_epoch.application_observed_at)
                pending_app = PendingApplication(
                    active_epoch.game_id, active_epoch.application_request_id, observed,
                    invite_seq=active_epoch.starting_invite_seq,
                    inviter_did=active_epoch.inviter_did,
                )
            now = datetime.now(timezone.utc)
            progressed_at = None
            if pending_app is not None:
                if active_epoch is not None:
                    progressed_at = self._reduce_epoch_structure(
                        active_epoch, discovery_events
                    )
                else:
                    for item, parsed in parsed_rosters:
                        if parsed[0].game_id != pending_app.game_id or SARUKU_DID not in parsed[0].members:
                            continue
                        stamp = record_time(item)
                        if stamp is not None and (progressed_at is None or stamp > progressed_at):
                            progressed_at = stamp
            age = (
                application_age_minutes(pending_app, now, progressed_at=progressed_at)
                if pending_app is not None
                else None
            )
            history_unresolved = False
            if (
                pending_app is not None and age is not None and age >= 20
                and discovery_generation is not None
            ):
                history = TeamFormationStore(self.state).load_history(
                    ROOMS.discovery, discovery_generation
                )
                if history is not None:
                    required_start = (
                        active_epoch.starting_invite_seq
                        if active_epoch is not None and active_epoch.starting_invite_seq
                        else pending_app.invite_seq or 1
                    )
                    required_end = history.highest_observed_seq
                    if not history.is_complete(required_start, required_end):
                        history_unresolved = True
                        if self._reconcile_history(
                            ROOMS.discovery, discovery_generation,
                            required_start, required_end,
                        ):
                            return self._discovery()
                        if age < 60:
                            return
            # v0.2 soft stall is reevaluation only. Mere challenger presence at
            # twenty minutes cannot abandon the current epoch.
            scan_candidates = pending_app is None or (
                age is not None and age >= 20 and not history_unresolved
            )
            selected = None
            if scan_candidates:
                candidates = []
                formation_store = TeamFormationStore(self.state)
                referee = self.state.get("referee_did")
                acquisition = None
                if discovery_generation is not None and isinstance(referee, str):
                    acquisition = ActiveAcquisition(
                        self.state, discovery_generation, self._trusted_writer_dids(),
                        lambda roster: self._team_room_open(roster, referee), self._journal,
                    )
                    acquisition.scan(now)
                for opportunity in formation_store.opportunities():
                    # Reject unavailable/expired vacancy evidence before room
                    # I/O or per-game reconstruction. Targeted notes retain
                    # their separate existing freshness/structure semantics.
                    if opportunity.opportunity_kind == "ACTIVE_VACANCY" and (
                        opportunity.observed_at is None
                        or opportunity.observed_at.tzinfo is None
                        or not timedelta(0) <= now - opportunity.observed_at <= ACTIVE_SEARCH_FRESHNESS
                    ):
                        continue
                    if not formation_store.opportunity_is_fresh_after_terminal(opportunity):
                        continue
                    invite = DirectInvite(
                        opportunity.game_id, opportunity.inviter_did,
                        opportunity.poem_room, opportunity.room_generation, 1,
                        opportunity.lead_verified,
                        opportunity.observed_at.timestamp() if opportunity.observed_at else float("-inf"),
                        opportunity.source_seq,
                        opportunity.opportunity_kind,
                    )
                    if pending_app is not None and invite.game_id == pending_app.game_id:
                        continue
                    if (
                        invite.game_id in signed_games
                        or self._formation_invite_blocked(invite)
                        or self.state.pending_request(f"application:{invite.game_id}") is not None
                    ):
                        continue
                    room_verified = False
                    if invite.poem_room is not None:
                        if not isinstance(referee, str):
                            continue
                        if not self._team_room_open(invite, referee):
                            continue
                        room_verified = True
                    if opportunity.opportunity_kind == "ACTIVE_VACANCY":
                        if acquisition is None or not acquisition.revalidate(opportunity, now):
                            continue
                    stale_note = opportunity.opportunity_kind == "TARGETED_INVITE" and (
                        opportunity.observed_at is None
                        or now - opportunity.observed_at.astimezone(timezone.utc) >= timedelta(minutes=20)
                    )
                    if stale_note and not (
                        isinstance(referee, str) and discovery_generation is not None
                        and self._opportunity_has_live_structure(opportunity, discovery_generation, referee)
                    ):
                        continue
                    candidates.append(InviteCandidate(invite, room_verified))
                # Entry-source priority is not permission to preempt an epoch.
                preferred = [item for item in candidates
                             if item.invite.opportunity_kind == "TARGETED_INVITE"]
                selected = choose_invite(preferred if pending_app is None and preferred else candidates)
                if pending_app is not None and selected is not None and age is not None and age < 60:
                    opportunities = [
                        FormationOpportunity(
                            item.invite.game_id, item.invite.from_did, item.invite.seq,
                            datetime.fromtimestamp(item.invite.message_time, timezone.utc)
                            if item.invite.message_time != float("-inf") else None,
                            item.invite.poem_room, item.invite.room_generation,
                        ) for item in candidates
                    ]
                    verified_rooms = {
                        (item.invite.poem_room, item.invite.room_generation): item.room_verified
                        for item in candidates if item.invite.poem_room is not None
                        and item.invite.room_generation is not None
                    }
                    candidate_records: list[dict[str, Any]] = []
                    for candidate_game in {item.invite.game_id for item in candidates}:
                        candidate_records.extend(self._formation_records(
                            discovery_generation, game_id=candidate_game,
                        ))
                    reduced = reduce_candidate_states(
                        candidate_records, opportunities, verified_rooms
                    )
                    referee = self.state.get("referee_did")
                    if isinstance(referee, str):
                        for candidate_state in reduced:
                            roster = candidate_state.roster
                            if roster is None or (
                                roster.poem_room, roster.room_generation
                            ) in verified_rooms:
                                continue
                            verified_rooms[(roster.poem_room, roster.room_generation)] = self._team_room_open(
                                roster, referee
                            )
                        reduced = reduce_candidate_states(
                            candidate_records, opportunities, verified_rooms
                        )
                    challenger = max(
                        reduced, key=lambda item: (
                            item.structural_key.comparison(), item.source_seq
                        ), default=None,
                    )
                    current_key = StructuralKey(
                        int({value: rank for rank, value in enumerate(FormationStage)}[
                            FormationStage.APPLIED
                        ]), False, False, False, 99, 0,
                    )
                    if active_epoch is not None and active_epoch.epoch_high_watermark_key is not None:
                        raw = tuple(active_epoch.epoch_high_watermark_key)
                        current_key = StructuralKey(
                            int(raw[0]), bool(raw[1]), bool(raw[2]), bool(raw[3]),
                            -int(raw[4]), int(raw[5]),
                        )
                    stronger = challenger is not None and materially_stronger(
                        challenger.structural_key, current_key
                    )
                    self._journal(
                        "formation_candidate_compared", game_id=pending_app.game_id,
                        challenger_game_id=(challenger.opportunity.game_id if challenger else None),
                        current_structural_key=list(current_key.comparison()),
                        challenger_structural_key=(
                            list(challenger.structural_key.comparison()) if challenger else None
                        ), decision="SWITCH" if stronger else "STAY",
                        reason_code="strict_structural_comparator",
                    )
                    if stronger and challenger is not None:
                        selected = next(
                            item.invite for item in candidates
                            if item.invite.game_id == challenger.opportunity.game_id
                            and item.invite.from_did == challenger.opportunity.inviter_did
                        )
                    else:
                        selected = None
            expiry_reason = None
            if pending_app is not None:
                ready_at_boundary = False
                if age is not None and age >= 60:
                    ready_at_boundary = any(
                        item.roster.game_id == pending_app.game_id
                        for item in roster_consensus(
                            discovery_events,
                            anchor_signer=pending_app.inviter_did,
                            min_anchor_seq=pending_app.invite_seq,
                        )
                    )
                if age is not None and age >= 20:
                    epoch_id = active_epoch.epoch_id if active_epoch is not None else pending_app.request_id
                    soft_key = f"formation_soft_stall:{epoch_id}"
                    if not self.state.get(soft_key, False):
                        self.state.set(soft_key, True)
                        self._journal(
                            "formation_soft_stall", game_id=pending_app.game_id,
                            epoch_id=epoch_id, stall_age_seconds=round(age * 60),
                            reason_code="structural_progress_watchdog",
                        )
                    if selected is None:
                        self._journal(
                            "formation_stay", game_id=pending_app.game_id,
                            epoch_id=epoch_id, decision="STAY",
                            reason_code="no_materially_stronger_challenger",
                        )
                if (
                    active_epoch is not None and age is not None and age >= 60
                    and not ready_at_boundary
                ):
                    now_utc = now.astimezone(timezone.utc)
                    if active_epoch.replacement_recovery_active(now_utc):
                        self._journal(
                            "formation_stay", game_id=active_epoch.game_id,
                            epoch_id=active_epoch.epoch_id,
                            decision="REPLACEMENT_RECOVERY",
                            reason_code="fixed_recovery_grace_active",
                            replacement_recovery_deadline=active_epoch.replacement_recovery_deadline,
                        )
                        return
                    if (
                        active_epoch.replacement_recovery_used
                        and active_epoch.replacement_recovery_deadline is not None
                    ):
                        self._journal(
                            "formation_recovery_grace_expired", game_id=active_epoch.game_id,
                            epoch_id=active_epoch.epoch_id,
                            reason_code="fixed_deadline_reached",
                            replacement_recovery_deadline=active_epoch.replacement_recovery_deadline,
                        )
                        active_epoch.replacement_recovery_deadline = None
                        TeamFormationStore(self.state).save_epoch(active_epoch)
                    elif (
                        active_epoch.replacement_of_fingerprint is not None
                        and not active_epoch.replacement_recovery_used
                        and active_epoch.lineage_last_progress_at is not None
                        and now_utc - datetime.fromisoformat(
                            active_epoch.lineage_last_progress_at
                        ) <= timedelta(minutes=20)
                    ):
                        history = (
                            TeamFormationStore(self.state).load_history(
                                ROOMS.discovery, discovery_generation
                            ) if discovery_generation is not None else None
                        )
                        complete = history is None or not history.reconciliation_required
                        replacement_roster = next((
                            parsed[0] for _, parsed in parsed_rosters
                            if roster_fingerprint(parsed[0]) == active_epoch.active_roster_fingerprint
                        ), None)
                        joinable = False
                        if complete and replacement_roster is not None:
                            referee = self.state.get("referee_did")
                            joinable = isinstance(referee, str) and self._team_room_open(
                                replacement_roster, referee
                            )
                        if joinable and active_epoch.maybe_start_replacement_recovery(now_utc):
                            TeamFormationStore(self.state).save_epoch(active_epoch)
                            self._journal(
                                "formation_recovery_grace_started", game_id=active_epoch.game_id,
                                epoch_id=active_epoch.epoch_id,
                                reason_code="legitimate_replacement_progress",
                                replacement_recovery_used=True,
                                replacement_recovery_deadline=active_epoch.replacement_recovery_deadline,
                            )
                            return
                if active_epoch is not None:
                    policy_decision = formation_decision(
                        active_epoch,
                        history_complete=not history_unresolved,
                        ready_to_countersign=ready_at_boundary,
                        challenger_stronger=(
                            selected is not None and age is not None and 20 <= age < 60
                        ),
                        now=now,
                    )
                    expiry_reason = {
                        "SWITCH": "materially_stronger_candidate",
                        "HARD_STALL": "hard_timeout",
                        "ABANDON_UNCERTAIN_HISTORY": "hard_timeout",
                    }.get(policy_decision)
                else:
                    expiry_reason = None if ready_at_boundary else (
                        "materially_stronger_candidate"
                        if selected is not None and age is not None and 20 <= age < 60
                        else application_expiry_reason(
                            pending_app,
                            now=now,
                            active_team=self.state.active_team(),
                            signed_games=signed_games,
                            progressed_games=roster_games,
                            better_candidate_available=False,
                            progressed_at=progressed_at,
                        )
                    )
                if expiry_reason is not None:
                    epoch = TeamFormationStore(self.state).active_epoch()
                    if epoch is not None and epoch.application_request_id == pending_app.request_id:
                        history = (
                            TeamFormationStore(self.state).load_history(
                                ROOMS.discovery, discovery_generation
                            ) if discovery_generation is not None else None
                        )
                        uncertain = bool(history and history.reconciliation_required)
                        epoch.status = (
                            "abandoned" if expiry_reason == "materially_stronger_candidate"
                            else "abandoned_uncertain_history" if uncertain
                            else "hard_stalled"
                        )
                        TeamFormationStore(self.state).save_epoch(epoch, active=False)
                        self._journal(
                            "formation_switch" if expiry_reason == "materially_stronger_candidate"
                            else "formation_preconsent_uncertain_abandon" if uncertain
                            else "formation_hard_stall",
                            game_id=epoch.game_id,
                            reason_code=expiry_reason,
                            decision=("SWITCH" if expiry_reason == "materially_stronger_candidate" else "HARD_STALL"),
                        )
                        self._journal(
                            "formation_epoch_ended", game_id=epoch.game_id,
                            reason_code=epoch.status,
                        )
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
                now = datetime.now(timezone.utc)
                selected_opportunity = next((
                    item for item in TeamFormationStore(self.state).opportunities(game_id=selected.game_id)
                    if item.inviter_did == selected.from_did and item.source_seq == selected.seq
                ), None)
                if selected_opportunity is None:
                    return
                active_vacancy = selected_opportunity.opportunity_kind == "ACTIVE_VACANCY"
                if active_vacancy:
                    # Refresh owner/open authority immediately before the
                    # existing write-ahead executor; no independent write path.
                    self._team_room_open_cache = {}
                    if (
                        TeamFormationStore(self.state).active_epoch() is not None
                        or self.state.active_team() is not None
                        or self.state.unresolved_protocol_intent("roster") is not None
                        or self.state.unresolved_protocol_intent("withdraw") is not None
                        or acquisition is None
                        or not acquisition.revalidate(selected_opportunity, now)
                    ):
                        self._journal(
                            "formation_active_search_candidate_invalidated",
                            game_id=selected.game_id, source_seq=selected.seq,
                            opportunity_kind="ACTIVE_VACANCY", reason_code="pre_application_revalidation_failed",
                        )
                        return
                    self._journal(
                        "formation_active_search_candidate_selected", game_id=selected.game_id,
                        source_seq=selected.seq, opportunity_kind="ACTIVE_VACANCY",
                        reason_code="verified_vacancy_selected", room_verified=True,
                    )
                payload = team_application(
                    selected.game_id, self.cfg.x_account_url, active_vacancy=active_vacancy,
                )
                if not self.live:
                    self.state.set("dry_run_action", payload)
                    return
                if not TeamFormationStore(self.state).consume_opportunity(
                    selected_opportunity, payload["request_id"], now
                ):
                    return
                epoch = start_epoch(
                    selected_opportunity, payload["request_id"], now,
                )
                self.state.persist_protocol_intent(
                    payload["request_id"], "application", ROOMS.discovery, payload,
                    ApplicationDelivery.POSTED_UNCONFIRMED.value,
                    game_id=selected.game_id, created_at=now.isoformat(),
                    reconcile_started_at=epoch.application_reconcile_started_at,
                    reconcile_deadline=epoch.application_reconcile_deadline,
                )
                TeamFormationStore(self.state).save_epoch(epoch)
                self._journal(
                    "formation_epoch_started", game_id=selected.game_id,
                    epoch_id=epoch.epoch_id, request_id=payload["request_id"],
                    reason_code="application_intent_created",
                )
                self.state.reserve_request(payload["request_id"], f"application:{selected.game_id}", payload)
                self._journal(
                    "formation_application_posted_unconfirmed", game_id=selected.game_id,
                    request_id=payload["request_id"], reason_code="send_intent_persisted",
                )
                self._post(ROOMS.discovery, payload)
                if active_vacancy:
                    self._journal(
                        "formation_active_search_application_started", game_id=selected.game_id,
                        source_seq=selected.seq, opportunity_kind="ACTIVE_VACANCY",
                        request_id=payload["request_id"], reason_code="application_intent_sent",
                    )
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
        discovery_events: list[dict[str, Any]] = []
        journal_path = self.cfg.state_db.parent / "journal" / "sonnet.jsonl"
        pending_game = pending_application(journal_path)
        active_epoch = TeamFormationStore(self.state).active_epoch()
        if pending_game is not None and active_epoch is None:
            durable = self.state.db.execute(
                "SELECT 1 FROM requests WHERE request_id=? AND kind LIKE 'application:%' "
                "AND status IN ('pending','posted')", (pending_game.request_id,),
            ).fetchone()
            if durable is None:
                pending_game = None
        if active_epoch is not None and active_epoch.status == "active":
            observed = (
                datetime.fromisoformat(active_epoch.application_observed_at)
                if active_epoch.application_observed_at else None
            )
            pending_game = PendingApplication(
                active_epoch.game_id, active_epoch.application_request_id, observed,
                invite_seq=active_epoch.starting_invite_seq,
                inviter_did=active_epoch.inviter_did,
            )
        relevant_games = (
            {pending_game.game_id} if pending_game is not None
            else {item.game_id for item in TeamFormationStore(self.state).opportunities()}
        )
        for relevant_game in relevant_games:
            discovery_events.extend(self._formation_records(
                discovery_generation, game_id=relevant_game,
                min_seq=(pending_game.invite_seq if pending_game is not None else None),
            ))
        if not relevant_games:
            # Detect newly arriving pre-existing roster affirmations without
            # decoding every historical roster on every idle cycle. Once a
            # game appears in the incremental slice, load only that game's
            # normalized formation facts for current-consent reduction.
            frontier_key = f"formation_idle_roster_frontier:{discovery_generation}"
            frontier = int(self.state.get(frontier_key, 0) or 0)
            fresh_rosters = self._formation_records(
                discovery_generation, event_kind="ROSTER_CONSENT", min_seq=frontier + 1,
            )
            if fresh_rosters:
                self.state.set(frontier_key, max(int(item.get("seq", 0) or 0) for item in fresh_rosters))
                for game in {
                    parsed[0].game_id for item in fresh_rosters
                    if (parsed := signed_roster(item)) is not None
                }:
                    discovery_events.extend(self._formation_records(
                        discovery_generation, game_id=game,
                    ))
        expired_games = {
            row["game_id"] for row in self.state.db.execute(
                "SELECT game_id FROM formation_epochs WHERE active=0 "
                "AND status IN ('hard_stalled','abandoned','abandoned_uncertain_history',"
                "'abandoned_unconfirmed','invalid')"
            ).fetchall()
        }
        # Backward-compatible journal evidence is supplementary only when a
        # durable request row independently confirms the terminal lifecycle.
        for game_id in expired_application_games(journal_path):
            durable = self.state.db.execute(
                "SELECT 1 FROM requests WHERE kind=? AND status IN "
                "('expired','withdrawn','delivery_unknown','rejected') LIMIT 1",
                (f"application:{game_id}",),
            ).fetchone()
            if durable is not None:
                expired_games.add(game_id)
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
        consensus_candidates = roster_consensus(
            discovery_events,
            anchor_signer=anchor_signer,
            min_anchor_seq=min_anchor_seq,
        )
        if pending_game is not None:
            compatible = [item for item in consensus_candidates if item.roster.game_id == pending_game.game_id]
            if len(compatible) > 1:
                self._journal(
                    "formation_roster_ambiguous", game_id=pending_game.game_id,
                    variant_count=len(compatible), reason_code="multiple_current_inviter_rosters",
                )
                return
        for consensus in consensus_candidates:
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
            history = TeamFormationStore(self.state).load_history(
                ROOMS.discovery, discovery_generation
            ) if discovery_generation is not None else None
            relevant_start = min_anchor_seq or 1
            if history is not None and not history.is_complete(
                relevant_start, consensus.completed_at_seq
            ):
                if self._reconcile_history(
                    ROOMS.discovery, discovery_generation,
                    relevant_start, consensus.completed_at_seq,
                ):
                    return self._discovery()
                continue
            if not self._team_room_open(proposal, referee):
                continue
            self._journal(
                "formation_roster_affirmed_by_recruitment", game_id=proposal.game_id,
                source_seq=consensus.completed_at_seq,
                roster_fingerprint=roster_fingerprint(proposal),
                signer_count=len(consensus.signers), member_count=len(proposal.members),
                missing_signers=len(set(proposal.members) - set(consensus.signers)),
                reason_code="unique_complete_inviter_anchored_roster",
            )
            self._journal(
                "formation_ready_to_countersign", game_id=proposal.game_id,
                source_seq=consensus.completed_at_seq,
                roster_fingerprint=roster_fingerprint(proposal),
                reason_code="all_other_members_current_consent",
                decision="COUNTERSIGN",
            )
            payload = proposal.payload(request_id("roster"))
            self.state.set("roster_candidate", payload)
            if not self.live:
                self.state.set("dry_run_action", payload)
                return
            if not self.state.select_team(proposal.game_id):
                return
            fingerprint = roster_fingerprint(proposal)
            created_at = datetime.now(timezone.utc).isoformat()
            self.state.persist_protocol_intent(
                payload["request_id"], "roster", ROOMS.discovery, payload,
                ConsentDelivery.CONSENT_POSTED_UNCONFIRMED.value,
                game_id=proposal.game_id, roster_fingerprint=fingerprint,
                created_at=created_at,
            )
            epoch = TeamFormationStore(self.state).active_epoch()
            if epoch is not None:
                epoch.consent_delivery_state = ConsentDelivery.CONSENT_POSTED_UNCONFIRMED
                epoch.consent_request_id = payload["request_id"]
                epoch.consent_roster_fingerprint = fingerprint
                TeamFormationStore(self.state).save_epoch(epoch)
            self._journal(
                "formation_consent_intent_persisted", game_id=proposal.game_id,
                request_id=payload["request_id"], roster_fingerprint=fingerprint,
                reason_code="write_ahead_committed",
            )
            self.state.reserve_request(payload["request_id"], "roster", payload)
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
        self._team_room_open_cache = {}
        # Referee resolution wins every race. Process receipts first.
        self._receipts(ROOMS.discovery)
        self._sync_formation_events()
        self._bootstrap_formation_state()
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
        active_epoch = TeamFormationStore(self.state).active_epoch()
        discovery_events = self._formation_records(
            discovery_generation, game_id=game_id,
            min_seq=(active_epoch.starting_invite_seq if active_epoch is not None else None),
        )
        withdraw_intent = self.state.unresolved_protocol_intent("withdraw")
        if withdraw_intent is not None and withdraw_intent.get("game_id") == game_id:
            reconciled = False
            for raw in self._formation_records(
                discovery_generation, request_id=withdraw_intent["request_id"]
            ):
                parsed_withdrawal = signed_withdrawal(raw)
                if parsed_withdrawal != (game_id, SARUKU_DID):
                    continue
                try:
                    exact = json.loads(raw["text"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue
                if exact.get("request_id") == withdraw_intent["request_id"]:
                    reconciled = True
                    break
            if not reconciled:
                return
            self.state.set_protocol_delivery(withdraw_intent["request_id"], "CONFIRMED")
            self.state.set_request_status(withdraw_intent["request_id"], "accepted")
            roster_request_id = pending.get("request_id")
            if isinstance(roster_request_id, str):
                self.state.set_request_status(roster_request_id, "withdrawn")
            epoch = TeamFormationStore(self.state).active_epoch()
            if epoch is not None:
                epoch.withdraw_pending = False
                epoch.status = "abandoned"
                TeamFormationStore(self.state).save_epoch(epoch, active=False)
            self._journal(
                "formation_withdraw_reconciled", game_id=game_id,
                request_id=withdraw_intent["request_id"],
                reason_code="exact_discovery_readback",
            )
            self.state.set("active_team", None)
            self.state.set("team_setup", None)
            self.state.set("roster_candidate", None)
            self.state.set("roster_consent_accepted", False)
            self._clear_roster_wait_state()
            self.state.set("last_error", None)
            self.state.phase = Phase.DISCOVERY
            return
        intent = self.state.protocol_intent(str(pending.get("request_id", "")))
        if intent is not None and intent.get("delivery_state") in {
            ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
            ConsentDelivery.CONSENT_DELIVERY_UNKNOWN,
        }:
            confirmed = False
            for raw in self._formation_records(
                discovery_generation, request_id=str(pending.get("request_id", ""))
            ):
                parsed = signed_roster(raw)
                if parsed != (target, SARUKU_DID):
                    continue
                try:
                    exact = json.loads(raw["text"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue
                if exact.get("request_id") == pending.get("request_id"):
                    confirmed = True
                    break
            if confirmed:
                self.state.set_protocol_delivery(
                    pending["request_id"], ConsentDelivery.CONSENT_CONFIRMED
                )
                epoch = TeamFormationStore(self.state).active_epoch()
                if epoch is not None:
                    epoch.consent_delivery_state = ConsentDelivery.CONSENT_CONFIRMED
                    epoch.formation_stage = FormationStage.WAIT_ROSTER_READY
                    TeamFormationStore(self.state).save_epoch(epoch)
                self._journal(
                    "formation_consent_confirmed", game_id=game_id,
                    request_id=pending["request_id"],
                    reason_code="exact_discovery_readback",
                )
            else:
                # Official consent uncertainty has no bounded local escape.
                if intent.get("delivery_state") == ConsentDelivery.CONSENT_POSTED_UNCONFIRMED:
                    self.state.set_protocol_delivery(
                        pending["request_id"], ConsentDelivery.CONSENT_DELIVERY_UNKNOWN
                    )
                    epoch = TeamFormationStore(self.state).active_epoch()
                    if epoch is not None:
                        epoch.consent_delivery_state = ConsentDelivery.CONSENT_DELIVERY_UNKNOWN
                        epoch.formation_stage = FormationStage.CONSENT_RECONCILING
                        TeamFormationStore(self.state).save_epoch(epoch)
                    self._journal(
                        "formation_consent_delivery_unknown", game_id=game_id,
                        request_id=pending["request_id"],
                        reason_code="no_authoritative_resolution",
                    )
                return
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

        history = (
            TeamFormationStore(self.state).load_history(
                ROOMS.discovery, discovery_generation
            ) if discovery_generation is not None else None
        )
        if history is not None:
            start_seq = 1
            epoch = TeamFormationStore(self.state).active_epoch()
            if epoch is not None:
                start_seq = epoch.consent_source_seq or epoch.application_source_seq or 1
            end_seq = history.highest_observed_seq
            if not history.is_complete(start_seq, end_seq):
                if self._reconcile_history(
                    ROOMS.discovery, discovery_generation, start_seq, end_seq
                ):
                    return self._wait_roster_ready()
                return

        # Fail closed on any hint that the roster may already be frozen.
        referee = self.state.get("referee_did")
        if not isinstance(referee, str):
            return
        withdrawal_legal = self._team_room_open(target, referee)
        epoch = TeamFormationStore(self.state).active_epoch()
        if epoch is not None:
            epoch.epoch_high_watermark_at = progress_at.astimezone(timezone.utc).isoformat()
            if epoch.consent_delivery_state == ConsentDelivery.NOT_SENT:
                epoch.consent_delivery_state = ConsentDelivery.CONSENT_CONFIRMED
            TeamFormationStore(self.state).save_epoch(epoch)
            decision = formation_decision(
                epoch, history_complete=True, wait_roster_ready=True,
                withdrawal_legal=withdrawal_legal, now=now,
            )
            if decision != "SAFE_WITHDRAW":
                self.state.set(
                    "last_error",
                    "team room may be frozen/closed; refusing automatic withdrawal; "
                    "reconciling fail-closed",
                )
                return
        elif not withdrawal_legal:
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
            created_at = datetime.now(timezone.utc).isoformat()
            self.state.persist_protocol_intent(
                withdraw_payload["request_id"], "withdraw", ROOMS.discovery,
                withdraw_payload, "POSTED_UNCONFIRMED", game_id=game_id,
                created_at=created_at,
            )
            epoch = TeamFormationStore(self.state).active_epoch()
            if epoch is not None:
                epoch.withdraw_pending = True
                epoch.withdraw_request_id = withdraw_payload["request_id"]
                TeamFormationStore(self.state).save_epoch(epoch)
            self._journal(
                "formation_withdraw_intent_persisted", game_id=game_id,
                request_id=withdraw_payload["request_id"], reason_code="write_ahead_committed",
            )
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
        self._journal(
            "formation_withdraw_posted_unconfirmed",
            game_id=game_id,
            request_id=withdraw_payload["request_id"],
            minutes_since_progress=round(age_minutes, 2),
            reason_code="awaiting_authoritative_readback",
        )

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
