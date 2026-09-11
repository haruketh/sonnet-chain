from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import CONTEST_ID
from .signing import verify_room_signature

SHA256 = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class TrustedLaunch:
    referee_did: str
    manifest_url: str
    manifest_sha256: str
    package_url: str | None
    contest: dict[str, Any]


def owner_did(note: Any) -> str | None:
    if isinstance(note, str):
        value = note.strip()
        if value.startswith("did:key:"):
            return value
        try:
            return owner_did(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(note, dict):
        for key in ("owner", "owner_did", "did", "value"):
            value = note.get(key)
            if isinstance(value, str) and value.startswith("did:key:"):
                return value
    return None


def verify_launch_record(room: str, owner_note: Any, message: dict) -> TrustedLaunch | None:
    owner = owner_did(owner_note)
    if owner is None or message.get("from") != owner:
        return None
    if not verify_room_signature(
        room, owner, message.get("nonce", ""), message.get("text", ""), message.get("sig", "")
    ):
        return None
    try:
        payload = json.loads(message["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("contest_id") != CONTEST_ID:
        return None
    record_type = payload.get("type")
    if not isinstance(record_type, str) or not ({"launch", "rules"} & set(record_type.lower().split("."))):
        return None
    manifest_url = payload.get("manifest_url")
    manifest_hash = payload.get("manifest_sha256")
    if not isinstance(manifest_url, str) or not manifest_url.startswith("https://"):
        return None
    if not isinstance(manifest_hash, str) or not SHA256.fullmatch(manifest_hash):
        return None
    referee = payload.get("referee_did", owner)
    if referee != owner:
        return None
    contest = payload.get("contest") if isinstance(payload.get("contest"), dict) else {}
    return TrustedLaunch(
        referee_did=owner,
        manifest_url=manifest_url,
        manifest_sha256=manifest_hash.lower(),
        package_url=payload.get("package_url") if isinstance(payload.get("package_url"), str) else None,
        contest=contest,
    )
