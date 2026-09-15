from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterable

from .config import CONTEST_ID, ROOMS, SARUKU_DID
from .formation_records import VerifiedFormationRecord
from .launch import DID_KEY
from .rosters import CanonicalRoster, roster_consensus, signed_roster, signed_withdrawal
from .signing import verify_room_signature
from .state import StateStore

SOFT_STALL = timedelta(minutes=20)
HARD_STALL = timedelta(minutes=60)
APPLICATION_RECONCILE_WINDOW = timedelta(minutes=10)
REPLACEMENT_RECOVERY_GRACE = timedelta(minutes=20)
MIN_EXTERNAL_COUNTERSIGNERS = 2


class ApplicationDelivery(StrEnum):
    NOT_SENT = "NOT_SENT"
    POSTED_UNCONFIRMED = "POSTED_UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    EXPLICITLY_NOT_PERSISTED = "EXPLICITLY_NOT_PERSISTED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class ConsentDelivery(StrEnum):
    NOT_SENT = "NOT_SENT"
    CONSENT_POSTED_UNCONFIRMED = "CONSENT_POSTED_UNCONFIRMED"
    CONSENT_CONFIRMED = "CONSENT_CONFIRMED"
    CONSENT_EXPLICITLY_NOT_PERSISTED = "CONSENT_EXPLICITLY_NOT_PERSISTED"
    CONSENT_DELIVERY_UNKNOWN = "CONSENT_DELIVERY_UNKNOWN"


class FormationStage(StrEnum):
    INVITED = "INVITED"
    APPLIED = "APPLIED"
    ENGAGED = "ENGAGED"
    ROSTER_PROPOSED = "ROSTER_PROPOSED"
    ROSTER_PROGRESSING = "ROSTER_PROGRESSING"
    READY_TO_COUNTERSIGN = "READY_TO_COUNTERSIGN"
    CONSENT_RECONCILING = "CONSENT_RECONCILING"
    WAIT_ROSTER_READY = "WAIT_ROSTER_READY"
    TEAM_READY = "TEAM_READY"


STAGE_RANK = {stage: rank for rank, stage in enumerate(FormationStage)}


@dataclass(frozen=True)
class GapRange:
    start: int
    end: int


@dataclass
class FormationHistoryState:
    room: str
    generation: int
    complete_from_seq: int | None = None
    complete_through_seq: int = 0
    highest_observed_seq: int = 0
    gap_ranges: list[GapRange] = field(default_factory=list)
    reconciliation_required: bool = False
    available_from_seq: int | None = None
    available_through_seq: int | None = None
    retention_truncated: bool = False

    def observe(
        self, seqs: Iterable[int], *, available_from_seq: int | None = None,
        available_through_seq: int | None = None,
    ) -> None:
        observed = sorted({int(seq) for seq in seqs if int(seq) > 0})
        if available_from_seq is not None:
            self.available_from_seq = available_from_seq
        if available_through_seq is not None:
            self.available_through_seq = available_through_seq
        if not observed or self.available_from_seq is not None and self.available_through_seq is None:
            return
        floor = self.available_from_seq if self.available_from_seq is not None else observed[0]
        ceiling = self.available_through_seq if self.available_through_seq is not None else observed[-1]
        retained = [seq for seq in observed if floor <= seq <= ceiling]
        self.highest_observed_seq = ceiling
        start = floor
        self.complete_from_seq = start
        self.gap_ranges = []
        cursor = start
        for seq in retained:
            if seq > cursor:
                self.gap_ranges.append(GapRange(cursor, seq - 1))
            cursor = max(cursor, seq + 1)
        if cursor <= ceiling:
            self.gap_ranges.append(GapRange(cursor, ceiling))
        self.complete_through_seq = (
            self.gap_ranges[0].start - 1 if self.gap_ranges else self.highest_observed_seq
        )
        self.reconciliation_required = bool(self.gap_ranges)
        self.retention_truncated = floor > 1 or any(seq < floor for seq in observed)

    def is_complete(self, start_seq: int, end_seq: int) -> bool:
        if end_seq < start_seq:
            return True
        if self.available_from_seq is not None and start_seq < self.available_from_seq:
            return False
        if self.complete_from_seq is None or start_seq < self.complete_from_seq:
            return False
        if end_seq > self.highest_observed_seq:
            return False
        return not any(g.start <= end_seq and g.end >= start_seq for g in self.gap_ranges)


@dataclass(frozen=True)
class ConsentValue:
    state: str  # ROSTER, NONE, or UNKNOWN
    roster: CanonicalRoster | None = None
    source_seq: int | None = None


def latest_consent(
    records: Iterable[dict[str, Any]], game_id: str, signer: str,
    history: FormationHistoryState | None = None,
) -> ConsentValue:
    latest: ConsentValue = ConsentValue("NONE")
    for record in sorted(records, key=lambda item: int(item.get("seq", 0) or 0)):
        seq = int(record.get("seq", 0) or 0)
        parsed = signed_roster(record)
        if parsed is not None and parsed[1] == signer and parsed[0].game_id == game_id:
            latest = ConsentValue("ROSTER", parsed[0], seq)
            continue
        withdrawn = signed_withdrawal(record)
        if withdrawn == (game_id, signer):
            latest = ConsentValue("NONE", None, seq)
    if (
        history is not None and latest.source_seq is not None
        and not history.is_complete(latest.source_seq, history.highest_observed_seq)
    ):
        return ConsentValue("UNKNOWN", source_seq=latest.source_seq)
    return latest


@dataclass(frozen=True)
class FormationOpportunity:
    game_id: str
    inviter_did: str
    source_seq: int
    observed_at: datetime | None
    poem_room: str | None = None
    room_generation: int | None = None
    lead_verified: bool = False
    opportunity_kind: str = "TARGETED_INVITE"
    source_generation: int | None = None


def targeted_recruitment_note(
    record: dict[str, Any], trusted_writer_dids: set[str],
    *, target_did: str = SARUKU_DID, room: str = ROOMS.discovery,
) -> FormationOpportunity | None:
    """Cheap structural filtering precedes the one required signature check."""
    sender = record.get("from")
    text = record.get("text")
    if not isinstance(sender, str) or sender == target_did or sender not in trusted_writer_dids:
        return None
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "sonnet.note.v1":
        return None
    if payload.get("contest_id") != CONTEST_ID or payload.get("target_did") != target_did:
        return None
    game_id = payload.get("game_id")
    if not isinstance(game_id, str):
        return None
    try:
        expected_room = ROOMS.team(game_id)
    except ValueError:
        return None
    poem_room = payload.get("poem_room", payload.get("team_room"))
    generation = payload.get("room_generation")
    if poem_room is not None and poem_room != expected_room:
        return None
    if (poem_room is None) != (generation is None):
        return None
    if generation is not None and (
        not isinstance(generation, int) or isinstance(generation, bool) or generation < 0
    ):
        return None
    if not DID_KEY.fullmatch(sender) or (
        not isinstance(record, VerifiedFormationRecord) and not verify_room_signature(
        room, sender, record.get("nonce", ""), text, record.get("sig", "")
    )):
        return None
    stamp = record.get("created_at", record.get("timestamp", record.get("ts")))
    observed_at = None
    if isinstance(stamp, str):
        try:
            observed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            pass
    lead = payload.get("team_lead_did", payload.get("lead_did"))
    return FormationOpportunity(
        game_id, sender, int(record.get("seq", 0) or 0), observed_at,
        poem_room, generation, isinstance(lead, str) and lead == sender,
    )


@dataclass(frozen=True)
class StructuralKey:
    stage_rank: int
    room_verified: bool
    saruku_in_roster: bool
    inviter_signed: bool
    missing_signers: int
    signer_count: int

    def comparison(self) -> tuple[int, bool, bool, bool, int, int]:
        return (
            self.stage_rank, self.room_verified, self.saruku_in_roster,
            self.inviter_signed, -self.missing_signers, self.signer_count,
        )


def materially_stronger(challenger: StructuralKey, current: StructuralKey) -> bool:
    return challenger.comparison() > current.comparison()


@dataclass(frozen=True)
class FormationCandidateState:
    opportunity: FormationOpportunity
    structural_key: StructuralKey
    roster: CanonicalRoster | None
    source_seq: int


def reduce_candidate_states(
    records: Iterable[dict[str, Any]], opportunities: Iterable[FormationOpportunity],
    room_verified: dict[tuple[str, int], bool] | None = None,
) -> list[FormationCandidateState]:
    records = list(records)
    verified = room_verified or {}
    out: list[FormationCandidateState] = []
    for opportunity in opportunities:
        progressively_ready = {
            item.roster for item in roster_consensus(
                records,
                anchor_signer=opportunity.inviter_did,
                min_anchor_seq=opportunity.source_seq,
                min_external_signers=MIN_EXTERNAL_COUNTERSIGNERS,
            )
        }
        variants: set[CanonicalRoster] = set()
        for record in records:
            parsed = signed_roster(record)
            if parsed is not None and parsed[0].game_id == opportunity.game_id:
                variants.add(parsed[0])
        choices: list[FormationCandidateState] = []
        for roster in variants:
            if SARUKU_DID not in roster.members:
                continue
            signers = {
                did for did in roster.members
                if latest_consent(records, roster.game_id, did).roster == roster
            }
            missing = len(set(roster.members) - signers)
            stage = (
                FormationStage.READY_TO_COUNTERSIGN
                if roster in progressively_ready
                else FormationStage.ROSTER_PROGRESSING if len(signers) > 1
                else FormationStage.ROSTER_PROPOSED
            )
            key = StructuralKey(
                STAGE_RANK[stage],
                verified.get((roster.poem_room, roster.room_generation), False),
                True, opportunity.inviter_did in signers, missing, len(signers),
            )
            choices.append(FormationCandidateState(opportunity, key, roster, max(
                (int(item.get("seq", 0) or 0) for item in records), default=opportunity.source_seq
            )))
        if choices:
            out.append(max(choices, key=lambda item: item.structural_key.comparison()))
        else:
            out.append(FormationCandidateState(
                opportunity,
                StructuralKey(STAGE_RANK[FormationStage.INVITED], False, False, False, 99, 0),
                None, opportunity.source_seq,
            ))
    return out


def roster_fingerprint(roster: CanonicalRoster) -> str:
    raw = json.dumps({
        "game_id": roster.game_id, "poem_room": roster.poem_room,
        "room_generation": roster.room_generation, "members": list(roster.members),
    }, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass
class TeamFormationState:
    epoch_id: str
    game_id: str
    inviter_did: str
    application_request_id: str
    application_delivery_state: ApplicationDelivery
    application_reconcile_started_at: str
    application_reconcile_deadline: str
    starting_invite_seq: int | None = None
    application_source_seq: int | None = None
    application_observed_at: str | None = None
    formation_stage: FormationStage = FormationStage.APPLIED
    epoch_high_watermark_key: tuple[Any, ...] | None = None
    epoch_high_watermark_at: str | None = None
    epoch_high_watermark_seq: int | None = None
    active_roster_fingerprint: str | None = None
    replacement_of_fingerprint: str | None = None
    lineage_best_key: tuple[Any, ...] | None = None
    lineage_last_progress_at: str | None = None
    lineage_last_progress_seq: int | None = None
    replacement_recovery_used: bool = False
    replacement_recovery_deadline: str | None = None
    consent_delivery_state: ConsentDelivery = ConsentDelivery.NOT_SENT
    consent_request_id: str | None = None
    consent_roster_fingerprint: str | None = None
    consent_source_seq: int | None = None
    consent_observed_at: str | None = None
    withdraw_pending: bool = False
    withdraw_request_id: str | None = None
    status: str = "active"

    @property
    def possibly_consented(self) -> bool:
        return self.consent_delivery_state in {
            ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
            ConsentDelivery.CONSENT_DELIVERY_UNKNOWN,
            ConsentDelivery.CONSENT_CONFIRMED,
        }

    def observe_progress(self, key: StructuralKey, at: datetime, seq: int) -> bool:
        value = key.comparison()
        if self.epoch_high_watermark_key is not None and value <= tuple(self.epoch_high_watermark_key):
            return False
        self.epoch_high_watermark_key = value
        self.epoch_high_watermark_at = at.astimezone(timezone.utc).isoformat()
        self.epoch_high_watermark_seq = seq
        return True

    def observe_lineage(
        self, fingerprint: str, key: StructuralKey, at: datetime, seq: int,
    ) -> tuple[bool, bool]:
        """Return (lineage_progress, epoch_progress), never lowering epoch HWM."""
        value = key.comparison()
        changed = fingerprint != self.active_roster_fingerprint
        lineage_progress = changed or self.lineage_best_key is None or value > tuple(self.lineage_best_key)
        if changed:
            if self.active_roster_fingerprint is not None:
                self.replacement_of_fingerprint = self.active_roster_fingerprint
            self.active_roster_fingerprint = fingerprint
            self.lineage_best_key = value
            self.lineage_last_progress_at = at.astimezone(timezone.utc).isoformat()
            self.lineage_last_progress_seq = seq
        elif lineage_progress:
            self.lineage_best_key = value
            self.lineage_last_progress_at = at.astimezone(timezone.utc).isoformat()
            self.lineage_last_progress_seq = seq
        return lineage_progress, self.observe_progress(key, at, seq)

    def maybe_start_replacement_recovery(self, now: datetime) -> bool:
        if self.replacement_recovery_used or self.active_roster_fingerprint is None:
            return False
        self.replacement_recovery_used = True
        self.replacement_recovery_deadline = (
            now.astimezone(timezone.utc) + REPLACEMENT_RECOVERY_GRACE
        ).isoformat()
        return True

    def replacement_recovery_active(self, now: datetime) -> bool:
        if not self.replacement_recovery_deadline:
            return False
        return now.astimezone(timezone.utc) < datetime.fromisoformat(
            self.replacement_recovery_deadline
        )


class TeamFormationStore:
    def __init__(self, state: StateStore):
        self.state = state

    def save_epoch(self, epoch: TeamFormationState, active: bool = True) -> None:
        encoded = json.dumps(asdict(epoch), ensure_ascii=False, separators=(",", ":"))
        with self.state.db:
            if active:
                self.state.db.execute("UPDATE formation_epochs SET active=0 WHERE active=1 AND epoch_id<>?", (epoch.epoch_id,))
            self.state.db.execute(
                "INSERT INTO formation_epochs(epoch_id,game_id,inviter_did,payload_json,status,active) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(epoch_id) DO UPDATE SET "
                "payload_json=excluded.payload_json,status=excluded.status,active=excluded.active,"
                "updated_at=CURRENT_TIMESTAMP",
                (epoch.epoch_id, epoch.game_id, epoch.inviter_did, encoded, epoch.status, int(active)),
            )

    def save_opportunity(self, opportunity: FormationOpportunity) -> bool:
        opportunity_id = f"{opportunity.game_id}:{opportunity.inviter_did}:{opportunity.source_seq}"
        if opportunity.opportunity_kind == "ACTIVE_VACANCY":
            opportunity_id += f":vacancy:{opportunity.source_generation}"
        encoded = json.dumps(asdict(opportunity), ensure_ascii=False, separators=(",", ":"), default=str)
        with self.state.db:
            cur = self.state.db.execute(
                "INSERT OR IGNORE INTO formation_opportunities("
                "opportunity_id,game_id,inviter_did,source_seq,payload_json) VALUES(?,?,?,?,?)",
                (opportunity_id, opportunity.game_id, opportunity.inviter_did,
                 opportunity.source_seq, encoded),
            )
        return cur.rowcount == 1

    def opportunities(self, *, game_id: str | None = None) -> list[FormationOpportunity]:
        sql = ("SELECT o.payload_json FROM formation_opportunities o WHERE o.consumed_at IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM formation_opportunities newer WHERE "
               "COALESCE(json_extract(newer.payload_json,'$.opportunity_kind'),'TARGETED_INVITE')="
               "COALESCE(json_extract(o.payload_json,'$.opportunity_kind'),'TARGETED_INVITE') AND "
               "newer.game_id=o.game_id AND newer.source_seq>o.source_seq)")
        args: tuple[Any, ...] = ()
        if game_id is not None:
            sql += " AND o.game_id=?"; args = (game_id,)
        sql += " ORDER BY o.source_seq DESC"
        rows = self.state.db.execute(sql, args).fetchall()
        out = []
        for row in rows:
            value = json.loads(row["payload_json"])
            stamp = value.get("observed_at")
            value["observed_at"] = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
            out.append(FormationOpportunity(**value))
        return out

    def latest_opportunity(self, game_id: str) -> FormationOpportunity | None:
        row = self.state.db.execute(
            "SELECT payload_json FROM formation_opportunities WHERE game_id=? "
            "ORDER BY source_seq DESC LIMIT 1", (game_id,),
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row["payload_json"])
        stamp = value.get("observed_at")
        value["observed_at"] = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
        return FormationOpportunity(**value)

    def consume_opportunity(
        self, opportunity: FormationOpportunity, request_id: str, now: datetime,
    ) -> bool:
        opportunity_id = f"{opportunity.game_id}:{opportunity.inviter_did}:{opportunity.source_seq}"
        if opportunity.opportunity_kind == "ACTIVE_VACANCY":
            opportunity_id += f":vacancy:{opportunity.source_generation}"
        with self.state.db:
            cur = self.state.db.execute(
                "UPDATE formation_opportunities SET consumed_at=?,consumed_request_id=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE opportunity_id=? AND consumed_at IS NULL",
                (now.astimezone(timezone.utc).isoformat(), request_id, opportunity_id),
            )
        return cur.rowcount == 1

    def opportunity_is_fresh_after_terminal(self, opportunity: FormationOpportunity) -> bool:
        # Durable epoch source sequence is primary; legacy request timestamps
        # conservatively consume notes observed no later than the application.
        rows = self.state.db.execute(
            "SELECT payload_json FROM formation_epochs WHERE game_id=? AND active=0",
            (opportunity.game_id,),
        ).fetchall()
        for row in rows:
            value = json.loads(row["payload_json"])
            source = value.get("starting_invite_seq")
            if isinstance(source, int) and opportunity.source_seq <= source:
                return False
        if opportunity.observed_at is not None:
            row = self.state.db.execute(
                "SELECT created_at FROM requests WHERE kind=? AND status IN "
                "('expired','rejected','delivery_unknown','withdrawn') "
                "ORDER BY created_at DESC LIMIT 1", (f"application:{opportunity.game_id}",),
            ).fetchone()
            if row is not None:
                try:
                    terminal_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                    if terminal_at.tzinfo is None: terminal_at = terminal_at.replace(tzinfo=timezone.utc)
                    if opportunity.observed_at <= terminal_at:
                        return False
                except ValueError:
                    return False
        return True

    def active_epoch(self) -> TeamFormationState | None:
        row = self.state.db.execute(
            "SELECT payload_json FROM formation_epochs WHERE active=1 LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row["payload_json"])
        value["application_delivery_state"] = ApplicationDelivery(value["application_delivery_state"])
        value["formation_stage"] = FormationStage(value["formation_stage"])
        value["consent_delivery_state"] = ConsentDelivery(value["consent_delivery_state"])
        return TeamFormationState(**value)

    def save_history(self, history: FormationHistoryState) -> None:
        gaps = json.dumps([asdict(item) for item in history.gap_ranges], separators=(",", ":"))
        with self.state.db:
            self.state.db.execute(
                "INSERT INTO formation_history_state(room,generation,complete_from_seq,"
                "complete_through_seq,highest_observed_seq,gap_ranges_json,reconciliation_required,"
                "available_from_seq,available_through_seq,retention_truncated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(room,generation) DO UPDATE SET "
                "complete_from_seq=excluded.complete_from_seq,complete_through_seq=excluded.complete_through_seq,"
                "highest_observed_seq=excluded.highest_observed_seq,gap_ranges_json=excluded.gap_ranges_json,"
                "reconciliation_required=excluded.reconciliation_required,"
                "available_from_seq=excluded.available_from_seq,"
                "available_through_seq=excluded.available_through_seq,"
                "retention_truncated=excluded.retention_truncated,updated_at=CURRENT_TIMESTAMP",
                (history.room, history.generation, history.complete_from_seq,
                 history.complete_through_seq, history.highest_observed_seq, gaps,
                 int(history.reconciliation_required), history.available_from_seq,
                 history.available_through_seq, int(history.retention_truncated)),
            )

    def load_history(self, room: str, generation: int) -> FormationHistoryState | None:
        row = self.state.db.execute(
            "SELECT * FROM formation_history_state WHERE room=? AND generation=?",
            (room, generation),
        ).fetchone()
        if row is None:
            return None
        return FormationHistoryState(
            room, generation, row["complete_from_seq"], row["complete_through_seq"],
            row["highest_observed_seq"],
            [GapRange(**item) for item in json.loads(row["gap_ranges_json"])],
            bool(row["reconciliation_required"]), row["available_from_seq"],
            row["available_through_seq"], bool(row["retention_truncated"]),
        )


def start_epoch(opportunity: FormationOpportunity, request_id: str, now: datetime) -> TeamFormationState:
    deadline = now + APPLICATION_RECONCILE_WINDOW
    return TeamFormationState(
        epoch_id=f"{opportunity.game_id}:{request_id}", game_id=opportunity.game_id,
        inviter_did=opportunity.inviter_did, application_request_id=request_id,
        application_delivery_state=ApplicationDelivery.POSTED_UNCONFIRMED,
        application_reconcile_started_at=now.astimezone(timezone.utc).isoformat(),
        application_reconcile_deadline=deadline.astimezone(timezone.utc).isoformat(),
        starting_invite_seq=opportunity.source_seq,
    )


def formation_watchdog(epoch: TeamFormationState, now: datetime) -> str:
    if epoch.consent_delivery_state in {
        ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
        ConsentDelivery.CONSENT_DELIVERY_UNKNOWN,
    }:
        return "RECONCILE_CONSENT"
    anchor = epoch.epoch_high_watermark_at or epoch.application_observed_at
    if anchor is None:
        deadline = datetime.fromisoformat(epoch.application_reconcile_deadline)
        return "APPLICATION_DELIVERY_UNKNOWN" if now >= deadline else "RECONCILE_APPLICATION"
    elapsed = now - datetime.fromisoformat(anchor)
    if elapsed >= HARD_STALL:
        if epoch.replacement_recovery_active(now):
            return "REPLACEMENT_RECOVERY"
        return "HARD_STALL"
    if elapsed >= SOFT_STALL:
        return "REEVALUATE"
    return "STAY"


def formation_decision(
    epoch: TeamFormationState | None, *, history_complete: bool,
    authoritative_terminal: bool = False, team_ready: bool = False,
    ready_to_countersign: bool = False, wait_roster_ready: bool = False,
    withdrawal_legal: bool = False, challenger_stronger: bool = False,
    now: datetime,
) -> str:
    """Normative v0.2 precedence; execution and network transport stay outside."""
    if authoritative_terminal:
        return "CLOSED"
    if team_ready:
        return "TEAM_READY"
    if epoch is not None and epoch.possibly_consented and epoch.consent_delivery_state != ConsentDelivery.CONSENT_CONFIRMED:
        return "RECONCILE_CONSENT"
    if not history_complete:
        if epoch is not None and formation_watchdog(epoch, now) == "HARD_STALL" and not epoch.possibly_consented:
            return "ABANDON_UNCERTAIN_HISTORY"
        return "RECONCILE_HISTORY"
    if wait_roster_ready or (
        epoch is not None and epoch.consent_delivery_state == ConsentDelivery.CONSENT_CONFIRMED
    ):
        if epoch is not None and formation_watchdog(epoch, now) == "HARD_STALL":
            return "SAFE_WITHDRAW" if withdrawal_legal else "RECONCILE"
        return "WAIT_ROSTER_READY"
    if ready_to_countersign:
        return "COUNTERSIGN"
    if epoch is None:
        return "APPLY"
    watchdog = formation_watchdog(epoch, now)
    if watchdog == "HARD_STALL":
        return "HARD_STALL"
    if watchdog == "REEVALUATE":
        return "SWITCH" if challenger_stronger else "STAY"
    return watchdog
