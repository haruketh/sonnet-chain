from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from .config import CONTEST_ID, ROOMS, SARUKU_DID
from .signing import verify_room_signature

ReceiptKind = Literal[
    "registration_accepted", "registration_rejected", "application_rejected", "team_setup", "roster_consent_accepted",
    "roster_ready", "roster_rejected",
    "word_accepted", "word_rejected", "submission_accepted", "submission_rejected", "unknown",
]
KNOWN = {
    "sonnet.registration-accepted.v1": "registration_accepted",
    "sonnet.registration-rejected.v1": "registration_rejected",
    "sonnet.application-rejected.v1": "application_rejected",
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

    # Production referee uses a generic sonnet.receipt.v1 envelope.
    # Normalize registration receipts deterministically; other generic
    # receipt forms remain unknown until their actual schemas are observed.
    if payload.get("type") == "sonnet.receipt.v1":
        if payload.get("contest_id") != CONTEST_ID:
            return NormalizedReceipt("unknown", payload)

        if room == ROOMS.registration:
            request_id = payload.get("request_id")
            participant_did = payload.get("participant_did")
            sender_did = payload.get("sender_did")
            role = payload.get("role")
            status = payload.get("status")

            valid_registration = (
                isinstance(request_id, str)
                and bool(request_id)
                and isinstance(participant_did, str)
                and participant_did.startswith("did:key:")
                and sender_did == participant_did
                and role in {"writer", "voter", "organizer"}
            )

            if valid_registration and status == "accepted":
                return NormalizedReceipt("registration_accepted", payload)

            if valid_registration and status == "rejected":
                return NormalizedReceipt("registration_rejected", payload)

        if room == ROOMS.discovery:
            valid_application_rejection = (
                isinstance(payload.get("request_id"), str)
                and bool(payload["request_id"])
                and payload.get("sender_did") == SARUKU_DID
                and payload.get("status") == "rejected"
                and payload.get("action_type") == "sonnet.application.v1"
            )
            if valid_application_rejection:
                return NormalizedReceipt("application_rejected", payload)
            valid_roster_receipt = (
                isinstance(payload.get("request_id"), str)
                and bool(payload["request_id"])
                and isinstance(payload.get("sender_did"), str)
                and isinstance(payload.get("state_hash"), str)
                and bool(payload["state_hash"])
                and isinstance(payload.get("roster_ready"), bool)
            )
            if valid_roster_receipt and payload.get("status") == "accepted":
                return NormalizedReceipt(
                    "roster_ready" if payload["roster_ready"] else "roster_consent_accepted",
                    payload,
                )
            if valid_roster_receipt and payload.get("status") == "rejected":
                return NormalizedReceipt("roster_rejected", payload)

        if room == ROOMS.submissions:
            # Observed live generic schema.  It intentionally has no game_id:
            # accepted receipts carry entry_id, while both outcomes bind the
            # authenticated submitter through sender_did and the stable request.
            valid_common = (
                isinstance(payload.get("request_id"), str)
                and bool(payload["request_id"])
                and isinstance(payload.get("sender_did"), str)
                and payload["sender_did"].startswith("did:key:")
                and isinstance(payload.get("intake_seq"), int)
            )
            if valid_common and payload.get("status") == "accepted" and (
                isinstance(payload.get("entry_id"), str) and bool(payload["entry_id"])
            ):
                return NormalizedReceipt("submission_accepted", payload)
            if valid_common and payload.get("status") == "rejected" and isinstance(
                payload.get("reason"), str
            ):
                return NormalizedReceipt("submission_rejected", payload)

        return NormalizedReceipt("unknown", payload)

    kind = KNOWN.get(payload.get("type"), "unknown")
    if kind != "unknown" and payload.get("contest_id") != CONTEST_ID:
        return NormalizedReceipt("unknown", payload)
    return NormalizedReceipt(kind, payload)


def receipt_matches(receipt: NormalizedReceipt, pending: dict[str, Any]) -> bool:
    if receipt.kind == "unknown":
        return False
    if receipt.kind == "application_rejected":
        return (
            pending.get("type") == "sonnet.application.v1"
            and receipt.payload.get("contest_id") == CONTEST_ID
            and receipt.payload.get("request_id") == pending.get("request_id")
            and receipt.payload.get("sender_did") == SARUKU_DID
        )
    if receipt.kind in {"roster_consent_accepted", "roster_ready", "roster_rejected"}:
        return (
            pending.get("type") == "sonnet.roster.v1"
            and receipt.payload.get("contest_id") == CONTEST_ID
            and receipt.payload.get("request_id") == pending.get("request_id")
            and receipt.payload.get("sender_did") == SARUKU_DID
        )
    if receipt.kind in {"submission_accepted", "submission_rejected"}:
        return (
            pending.get("type") == "sonnet.submit.v1"
            and receipt.payload.get("contest_id") == CONTEST_ID
            and receipt.payload.get("request_id") == pending.get("request_id")
            and receipt.payload.get("sender_did") == SARUKU_DID
        )
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
        "application_rejected": {"request_id"},
        "team_setup": {"request_id", "game_id", "poem_room", "room_generation"},
        "roster_consent_accepted": {"request_id"},
        "roster_ready": {"request_id", "game_id", "poem_room", "room_generation", "members"},
        "roster_rejected": {"request_id"},
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
