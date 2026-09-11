from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from .signing import verify_room_signature

ReceiptKind = Literal[
    "registration_accepted", "registration_rejected", "team_setup", "roster_ready",
    "word_accepted", "word_rejected", "submission_accepted", "submission_rejected", "unknown",
]
KNOWN = {
    "sonnet.registration-accepted.v1": "registration_accepted",
    "sonnet.registration-rejected.v1": "registration_rejected",
    "sonnet.team-setup.v1": "team_setup",
    "sonnet.roster-ready.v1": "roster_ready",
    "sonnet.word-accepted.v1": "word_accepted",
    "sonnet.word-rejected.v1": "word_rejected",
    "sonnet.submission-accepted.v1": "submission_accepted",
    "sonnet.submission-rejected.v1": "submission_rejected",
}


@dataclass(frozen=True)
class NormalizedReceipt:
    kind: ReceiptKind
    payload: dict[str, Any]


def receipt_candidate(room: str, raw: dict, referee_did: str) -> NormalizedReceipt | None:
    if raw.get("from") != referee_did or not verify_room_signature(
        room, referee_did, raw.get("nonce", ""), raw.get("text", ""), raw.get("sig", "")
    ):
        return None
    try:
        payload = json.loads(raw["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return NormalizedReceipt("unknown", {})
    if not isinstance(payload, dict):
        return NormalizedReceipt("unknown", {})
    return NormalizedReceipt(KNOWN.get(payload.get("type"), "unknown"), payload)


def receipt_matches(receipt: NormalizedReceipt, pending: dict[str, Any]) -> bool:
    if receipt.kind == "unknown":
        return False
    for key in ("request_id", "game_id", "room_generation", "poem_room", "previous_state_hash"):
        if key in pending and receipt.payload.get(key) != pending[key]:
            return False
    if "version" in pending and "request_version" in receipt.payload:
        if receipt.payload["request_version"] != pending["version"]:
            return False
    return True


def normalize_llm_receipt(value: Any) -> NormalizedReceipt:
    if not isinstance(value, dict) or value.get("kind") not in set(KNOWN.values()) | {"unknown"}:
        return NormalizedReceipt("unknown", {})
    kind = value["kind"]
    payload = {key: item for key, item in value.items() if key != "kind" and item is not None}
    required = {
        "registration_accepted": {"request_id"},
        "registration_rejected": {"request_id"},
        "team_setup": {"request_id", "game_id", "poem_room", "room_generation"},
        "roster_ready": {"request_id", "game_id", "poem_room", "room_generation", "members"},
        "word_accepted": {"request_id", "game_id", "room_generation", "request_version", "version", "state_hash", "contributor_did"},
        "word_rejected": {"request_id", "game_id", "room_generation", "request_version"},
        "submission_accepted": {"request_id", "game_id"},
        "submission_rejected": {"request_id", "game_id"},
        "unknown": set(),
    }[kind]
    if not required <= payload.keys():
        return NormalizedReceipt("unknown", {})
    if "members" in payload and not all(isinstance(x, str) and x.startswith("did:key:") for x in payload["members"]):
        return NormalizedReceipt("unknown", {})
    return NormalizedReceipt(kind, payload)
