from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .config import CONTEST_ID, ROOMS, SARUKU_DID
from .launch import DID_KEY, owner_did
from .signing import public_key_of_did, verify_room_signature


@dataclass(frozen=True)
class CanonicalRoster:
    game_id: str
    poem_room: str
    room_generation: int
    members: tuple[str, ...]

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "type": "sonnet.roster.v1",
            "contest_id": CONTEST_ID,
            "game_id": self.game_id,
            "poem_room": self.poem_room,
            "room_generation": self.room_generation,
            "members": list(self.members),
            "request_id": request_id,
        }


@dataclass(frozen=True)
class RosterConsensus:
    roster: CanonicalRoster
    signers: frozenset[str]
    completed_at_seq: int


def signed_roster(record: dict[str, Any], room: str = ROOMS.discovery) -> tuple[CanonicalRoster, str] | None:
    signer = record.get("from")
    if not isinstance(signer, str) or not DID_KEY.fullmatch(signer):
        return None
    try:
        payload = json.loads(record["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "sonnet.roster.v1":
        return None
    if payload.get("contest_id") != CONTEST_ID:
        return None
    if not verify_room_signature(
        room, signer, record.get("nonce", ""), record.get("text", ""), record.get("sig", "")
    ):
        return None
    game_id = payload.get("game_id")
    poem_room = payload.get("poem_room")
    generation = payload.get("room_generation")
    members = payload.get("members")
    request_id = payload.get("request_id")
    if not isinstance(game_id, str) or not isinstance(poem_room, str):
        return None
    try:
        expected_room = ROOMS.team(game_id)
    except ValueError:
        return None
    if poem_room != expected_room:
        return None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        return None
    if not isinstance(request_id, str) or not request_id:
        return None
    if not isinstance(members, list) or not 4 <= len(members) <= 8:
        return None
    if not all(isinstance(member, str) and DID_KEY.fullmatch(member) for member in members):
        return None
    try:
        for member in members:
            public_key_of_did(member)
    except ValueError:
        return None
    if len(set(members)) != len(members) or signer not in members:
        return None
    return CanonicalRoster(game_id, poem_room, generation, tuple(members)), signer


def signed_withdrawal(record: dict[str, Any], room: str = ROOMS.discovery) -> tuple[str, str] | None:
    signer = record.get("from")
    if not isinstance(signer, str) or not DID_KEY.fullmatch(signer):
        return None
    try:
        payload = json.loads(record["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "sonnet.withdraw.v1":
        return None
    if payload.get("contest_id") != CONTEST_ID:
        return None
    if not verify_room_signature(
        room, signer, record.get("nonce", ""), record.get("text", ""), record.get("sig", "")
    ):
        return None
    game_id = payload.get("game_id")
    request_id = payload.get("request_id")
    if not isinstance(game_id, str):
        return None
    if not isinstance(request_id, str) or not request_id:
        return None
    try:
        ROOMS.team(game_id)
    except ValueError:
        return None
    return game_id, signer


def _sequence(record: dict[str, Any]) -> int:
    try:
        return int(record.get("seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


def current_roster_signers(
    records: Iterable[dict[str, Any]],
    target: CanonicalRoster,
) -> frozenset[str]:
    current: dict[str, CanonicalRoster] = {}
    for record in sorted(records, key=_sequence):
        parsed = signed_roster(record)
        if parsed is not None:
            roster, signer = parsed
            current[signer] = roster
            continue
        withdrawal = signed_withdrawal(record)
        if withdrawal is not None:
            game_id, signer = withdrawal
            if signer in current and current[signer].game_id == game_id:
                del current[signer]
    return frozenset(
        signer
        for signer, roster in current.items()
        if roster == target
    )


def roster_consensus(
    records: Iterable[dict[str, Any]],
    saruku_did: str = SARUKU_DID,
    anchor_signer: str | None = None,
    min_anchor_seq: int | None = None,
) -> list[RosterConsensus]:
    current: dict[str, CanonicalRoster] = {}
    current_seq: dict[str, int] = {}
    last_seq: dict[CanonicalRoster, int] = {}
    for record in sorted(records, key=_sequence):
        parsed = signed_roster(record)
        if parsed is not None:
            roster, signer = parsed
            current[signer] = roster
            current_seq[signer] = _sequence(record)
            last_seq[roster] = max(last_seq.get(roster, 0), _sequence(record))
            continue
        withdrawal = signed_withdrawal(record)
        if withdrawal is not None:
            game_id, signer = withdrawal
            if signer in current and current[signer].game_id == game_id:
                del current[signer]
                current_seq.pop(signer, None)
    signatures: dict[CanonicalRoster, set[str]] = {}
    for signer, roster in current.items():
        signatures.setdefault(roster, set()).add(signer)
    ready = []
    for candidate, signers in signatures.items():
        if saruku_did not in candidate.members or saruku_did in signers:
            continue

        if anchor_signer is None:
            ready_now = set(candidate.members) - {saruku_did} <= signers
        else:
            # Early consent is allowed only when the DID that invited Saruku
            # currently signs this exact canonical roster, and that anchor
            # signature belongs to this application epoch.
            ready_now = (
                anchor_signer in signers
                and (
                    min_anchor_seq is None
                    or current_seq.get(anchor_signer, 0) > min_anchor_seq
                )
            )

        if ready_now:
            ready.append(
                RosterConsensus(
                    candidate,
                    frozenset(signers),
                    last_seq[candidate],
                )
            )
    return sorted(ready, key=lambda item: (item.completed_at_seq, item.roster.game_id, item.roster.members))


def team_room_is_open(
    roster: CanonicalRoster,
    referee_did: str,
    owner_note: Any,
    actual_generation: int | None,
    records: Iterable[Any],
) -> bool:
    if owner_did(owner_note) != referee_did or actual_generation != roster.room_generation:
        return False
    for record in records:
        raw = record.raw if hasattr(record, "raw") else record
        if not isinstance(raw, dict) or raw.get("from") != referee_did:
            continue
        if not verify_room_signature(
            roster.poem_room,
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
            # In a team room, an accepted generic referee receipt represents an
            # accepted word. Treat any future ambiguous accepted shape as frozen.
            return False
    return True
