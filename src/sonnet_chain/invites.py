from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import CONTEST_ID, ROOMS, SARUKU_DID
from .launch import DID_KEY, owner_did
from .signing import verify_room_signature

INVITE_TYPES = {"sonnet.note.v1", "sonnet.invite.v1", "sonnet.invite.v2"}


@dataclass(frozen=True)
class DirectInvite:
    game_id: str
    from_did: str
    poem_room: str | None
    room_generation: int | None
    explicit_offer_rank: int
    lead_verified: bool
    message_time: float
    seq: int


@dataclass(frozen=True)
class InviteCandidate:
    invite: DirectInvite
    room_verified: bool


@dataclass
class PendingApplication:
    game_id: str
    request_id: str
    sent_at: datetime | None
    progressed: bool = False
    invite_seq: int | None = None


def record_time(record: dict[str, Any]) -> datetime | None:
    value = record.get("created_at", record.get("timestamp", record.get("ts")))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    return None


def _message_time(record: dict[str, Any]) -> float:
    parsed = record_time(record)
    return parsed.timestamp() if parsed is not None else float("-inf")


def choose_invite(candidates: Iterable[InviteCandidate]) -> DirectInvite | None:
    choices = list(candidates)
    if not choices:
        return None
    return max(
        choices,
        key=lambda item: (
            item.room_verified,
            item.invite.explicit_offer_rank,
            item.invite.lead_verified,
            item.invite.message_time,
            item.invite.seq,
        ),
    ).invite


def direct_invite(
    record: dict[str, Any], target_did: str = SARUKU_DID, room: str = ROOMS.discovery
) -> DirectInvite | None:
    sender = record.get("from")
    if not isinstance(sender, str) or not DID_KEY.fullmatch(sender) or sender == target_did:
        return None
    if not verify_room_signature(
        room, sender, record.get("nonce", ""), record.get("text", ""), record.get("sig", "")
    ):
        return None
    try:
        payload = json.loads(record["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") not in INVITE_TYPES:
        return None
    if payload.get("contest_id") != CONTEST_ID or payload.get("target_did") != target_did:
        return None
    if payload.get("role") not in {None, "writer"}:
        return None
    if payload.get("status") in {"started", "frozen", "closed"}:
        return None
    if payload.get("roster_status") in {"started", "frozen", "closed"}:
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
    if (poem_room is None) != (generation is None):
        return None
    if poem_room is not None:
        if poem_room != expected_room:
            return None
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            return None
    try:
        seq = int(record.get("seq", 0) or 0)
    except (TypeError, ValueError):
        seq = 0
    lead = payload.get("team_lead_did", payload.get("lead_did"))
    explicit_offer_rank = 2 if payload["type"] in {"sonnet.invite.v1", "sonnet.invite.v2"} else 1
    return DirectInvite(
        game_id, sender, poem_room, generation, explicit_offer_rank,
        isinstance(lead, str) and lead == sender, _message_time(record), seq,
    )


def invite_room_is_open(
    invite: DirectInvite,
    referee_did: str,
    room_owner_note: Any,
    actual_generation: int | None,
    records: Iterable[Any],
) -> bool:
    if invite.poem_room is None and invite.room_generation is None:
        return True
    if owner_did(room_owner_note) != referee_did or actual_generation != invite.room_generation:
        return False
    for record in records:
        raw = record.raw if hasattr(record, "raw") else record
        if not isinstance(raw, dict) or raw.get("from") != referee_did:
            continue
        if not verify_room_signature(
            invite.poem_room or "",
            referee_did,
            raw.get("nonce", ""),
            raw.get("text", ""),
            raw.get("sig", ""),
        ):
            continue
        try:
            payload = json.loads(raw["text"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("contest_id") != CONTEST_ID:
            continue
        if payload.get("type") == "sonnet.word-accepted.v1":
            return False
        if payload.get("type") == "sonnet.receipt.v1" and payload.get("status") == "accepted":
            return False
    return True


def application_was_sent(journal_path: Path, game_id: str) -> bool:
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return True
        if (
            isinstance(record, dict)
            and record.get("event") == "team_application_sent"
            and record.get("game_id") == game_id
        ):
            return True
    return False


def application_blocks_invite(journal_path: Path, invite: DirectInvite) -> bool:
    # Block duplicates unless a genuinely fresh invite follows the latest expiry.
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    except OSError:
        return True

    latest_sent: dict[str, Any] | None = None
    latest_expiry: dict[str, Any] | None = None

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return True
        if not isinstance(record, dict):
            continue

        if (
            record.get("event") == "team_application_sent"
            and record.get("game_id") == invite.game_id
            and isinstance(record.get("request_id"), str)
        ):
            latest_sent = record
            latest_expiry = None
            continue

        if latest_sent is None:
            continue

        if (
            record.get("event") == "team_application_expired"
            and record.get("game_id") == invite.game_id
            and record.get("request_id") == latest_sent.get("request_id")
        ):
            latest_expiry = record

    if latest_sent is None:
        return False
    if latest_expiry is None:
        return True

    previous_seq = latest_sent.get("invite_seq")
    if isinstance(previous_seq, int) and not isinstance(previous_seq, bool):
        return invite.seq <= previous_seq

    expiry_at = record_time(latest_expiry)
    if expiry_at is not None and invite.message_time != float("-inf"):
        invite_at = datetime.fromtimestamp(invite.message_time, tz=timezone.utc)
        return invite_at <= expiry_at

    return True


def pending_application(journal_path: Path) -> PendingApplication | None:
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return PendingApplication("unknown", "unknown", None)
    pending = None
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return PendingApplication("unknown", "unknown", None)
        if not isinstance(record, dict):
            continue
        game_id = record.get("game_id")
        request_id = record.get("request_id")
        if (
            record.get("event") == "team_application_sent"
            and isinstance(game_id, str)
            and isinstance(request_id, str)
        ):
            try:
                sent_at = datetime.fromisoformat(str(record.get("ts", "")).replace("Z", "+00:00"))
                sent_at = sent_at.astimezone(timezone.utc) if sent_at.tzinfo else None
            except ValueError:
                sent_at = None
            invite_seq = record.get("invite_seq")
            if not isinstance(invite_seq, int) or isinstance(invite_seq, bool):
                invite_seq = None
            pending = PendingApplication(
                game_id,
                request_id,
                sent_at,
                invite_seq=invite_seq,
            )
            continue
        if pending is None or game_id != pending.game_id:
            continue
        if record.get("event") in {"team_application_accepted", "team_application_progressed"}:
            pending.progressed = True
            continue
        ended = (
            record.get("event") in {"team_application_ended", "team_application_expired"}
            or (
                record.get("event") == "receipt_accepted"
                and record.get("kind") in {"roster_ready", "roster_rejected"}
            )
            or (
                record.get("event") == "technocore_post_succeeded"
                and record.get("type") in {"sonnet.roster.v1", "sonnet.withdraw.v1"}
            )
        )
        if ended:
            pending = None
    return pending


def pending_application_game(journal_path: Path) -> str | None:
    pending = pending_application(journal_path)
    return pending.game_id if pending is not None else None


def application_age_minutes(
    pending: PendingApplication,
    now: datetime,
    progressed_at: datetime | None = None,
) -> float | None:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    reference = pending.sent_at
    if progressed_at is not None:
        if progressed_at.tzinfo is None:
            raise ValueError("progressed_at must include a timezone")
        progressed_at = progressed_at.astimezone(timezone.utc)
        if reference is None or progressed_at > reference:
            reference = progressed_at
    if reference is None:
        return None
    return max(0.0, (now.astimezone(timezone.utc) - reference).total_seconds() / 60)


def application_expiry_reason(
    pending: PendingApplication,
    *,
    now: datetime,
    active_team: str | None,
    signed_games: set[str],
    progressed_games: set[str],
    better_candidate_available: bool,
    progressed_at: datetime | None = None,
) -> str | None:
    _ = progressed_games
    if active_team is not None or pending.game_id in signed_games:
        return None
    age = application_age_minutes(pending, now, progressed_at=progressed_at)
    if age is None:
        return None
    if age >= 60:
        return "hard_timeout"
    if age >= 20 and better_candidate_available:
        return "better_candidate_available"
    return None


def application_should_expire(
    pending: PendingApplication,
    *,
    now: datetime,
    active_team: str | None,
    signed_games: set[str],
    progressed_games: set[str],
    better_candidate_available: bool = False,
    progressed_at: datetime | None = None,
) -> bool:
    return application_expiry_reason(
        pending,
        now=now,
        active_team=active_team,
        signed_games=signed_games,
        progressed_games=progressed_games,
        better_candidate_available=better_candidate_available,
        progressed_at=progressed_at,
    ) is not None


def expired_application_games(journal_path: Path) -> set[str]:
    # Only the latest application epoch for a game matters.
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()

    states: dict[str, tuple[str, str | None]] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        game_id = record.get("game_id")
        request_id = record.get("request_id")
        if not isinstance(game_id, str):
            continue

        if record.get("event") == "team_application_sent":
            states[game_id] = (
                "pending",
                request_id if isinstance(request_id, str) else None,
            )
            continue

        if record.get("event") == "team_application_expired":
            current = states.get(game_id)
            if current is not None and current[1] == request_id:
                states[game_id] = ("expired", current[1])

    return {
        game_id
        for game_id, (state, _) in states.items()
        if state == "expired"
    }
