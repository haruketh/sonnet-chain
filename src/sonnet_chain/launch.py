from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import CONTEST_ID
from .signing import verify_room_signature

SHA256 = re.compile(r"[0-9a-fA-F]{64}")
DID_KEY = re.compile(r"did:key:z[1-9A-HJ-NP-Za-km-z]+")


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
        if DID_KEY.fullmatch(value):
            return value
        try:
            return owner_did(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            matches = [
                line.strip()
                for line in value.splitlines()
                if DID_KEY.fullmatch(line.strip())
            ]
            return matches[0] if len(matches) == 1 else None

    if isinstance(note, dict):
        for key in ("owner", "owner_did", "did", "value"):
            value = note.get(key)
            if isinstance(value, str) and DID_KEY.fullmatch(value):
                return value

    return None


def _iso_utc(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(
                float(value), timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        return value

    return None


def verify_launch_record(
    room: str,
    owner_note: Any,
    message: dict,
) -> TrustedLaunch | None:
    owner = owner_did(owner_note)
    if owner is None or message.get("from") != owner:
        return None

    if not verify_room_signature(
        room,
        owner,
        message.get("nonce", ""),
        message.get("text", ""),
        message.get("sig", ""),
    ):
        return None

    try:
        payload = json.loads(message["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    # Official nested sonnet-2 launch format.
    # If configuration/package are absent, fall through to the
    # backward-compatible flat launch format below.
    if (
        payload.get("type") == "sonnet.launch.v1"
        and isinstance(payload.get("configuration"), dict)
        and isinstance(payload.get("package"), dict)
    ):
        configuration = payload["configuration"]
        package = payload["package"]

        if configuration.get("contest_id") != CONTEST_ID:
            return None

        referee = configuration.get("referee")
        if referee != owner:
            return None

        if payload.get("status") != "open":
            return None

        if payload.get("rooms_provisioned") is not True:
            return None

        rooms = configuration.get("rooms")
        if not isinstance(rooms, dict) or rooms.get("rules") != room:
            return None

        manifest_url = package.get("url")
        manifest_hash = package.get("sha256")

        if not isinstance(manifest_url, str) or not manifest_url.startswith("https://"):
            return None

        if not isinstance(manifest_hash, str) or not SHA256.fullmatch(manifest_hash):
            return None

        manifest_hash = manifest_hash.lower()

        fingerprint = configuration.get("package_fingerprint")
        if isinstance(fingerprint, dict):
            fingerprint_manifest = fingerprint.get("manifest_sha256")
            if (
                not isinstance(fingerprint_manifest, str)
                or not SHA256.fullmatch(fingerprint_manifest)
                or fingerprint_manifest.lower() != manifest_hash
            ):
                return None

        # Local contest.json stores ISO UTC strings, while the live launch
        # publishes opening/deadline/cutoff as Unix timestamps.
        contest = dict(configuration)

        for key in ("opening", "deadline", "identity_cutoff"):
            if key in contest:
                normalized = _iso_utc(contest[key])
                if normalized is None:
                    return None
                contest[key] = normalized

        return TrustedLaunch(
            referee_did=owner,
            manifest_url=manifest_url,
            manifest_sha256=manifest_hash,
            package_url=None,
            contest=contest,
        )

    # Backward-compatible support for the originally anticipated flat format.
    if payload.get("contest_id") != CONTEST_ID:
        return None

    record_type = payload.get("type")
    if not isinstance(record_type, str) or not (
        {"launch", "rules"} & set(record_type.lower().split("."))
    ):
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

    contest = payload.get("contest")
    if not isinstance(contest, dict):
        contest = {}

    return TrustedLaunch(
        referee_did=owner,
        manifest_url=manifest_url,
        manifest_sha256=manifest_hash.lower(),
        package_url=(
            payload.get("package_url")
            if isinstance(payload.get("package_url"), str)
            else None
        ),
        contest=contest,
    )
