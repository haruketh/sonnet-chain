from __future__ import annotations

import json
from dataclasses import dataclass
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
    return DirectInvite(game_id, sender, poem_room, generation)


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
