from __future__ import annotations

import json
import re
import secrets
from typing import Iterable

from .config import CONTEST_ID, ROOMS, SARUKU_DID

GAME_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")


def request_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def compact(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def register_writer(x_account_url: str, rid: str | None = None) -> dict:
    return {
        "type": "sonnet.register.v1",
        "contest_id": CONTEST_ID,
        "role": "writer",
        "x_account_url": x_account_url,
        "request_id": rid or request_id("register"),
    }


def team_request(game_id: str, rid: str | None = None) -> dict:
    if not GAME_ID.fullmatch(game_id):
        raise ValueError("game_id must be 1-16 lowercase letters/digits/_/-, starting alphanumeric")
    return {
        "type": "sonnet.team-request.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "request_id": rid or request_id("team"),
    }


def discovery_advertisement(rid: str | None = None) -> dict:
    return {
        "type": "sonnet.note.v1",
        "contest_id": CONTEST_ID,
        "request_id": rid or request_id("available"),
        "text": (
            "Saruku is a registered writer and available for a roster. "
            "Include my DID in a valid 4-8 writer sonnet.roster.v1 proposal if you want me to join."
        ),
    }


def team_application(
    game_id: str, x_account_url: str, rid: str | None = None, *, active_vacancy: bool = False,
) -> dict:
    if not GAME_ID.fullmatch(game_id):
        raise ValueError("game_id must be 1-16 lowercase letters/digits/_/-, starting alphanumeric")
    if not isinstance(x_account_url, str) or not x_account_url:
        raise ValueError("x_account_url is required")
    return {
        "type": "sonnet.application.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "did": SARUKU_DID,
        "role": "writer",
        "x_account_url": x_account_url,
        "request_id": rid or request_id("application"),
        "text": (
            f"Saruku is available to join {game_id} if you're still forming. "
            "Registered Sonnet-2 writer. No live roster consent. "
            "Ready to countersign the exact canonical roster when posted."
        ) if active_vacancy else (
            f"YES {game_id}. Registered Sonnet-2 writer. No live roster consent. "
            "Ready to countersign the exact canonical roster when posted."
        ),
    }


def roster(
    game_id: str,
    poem_room: str,
    room_generation: int,
    members: Iterable[str],
    rid: str | None = None,
) -> dict:
    if poem_room != ROOMS.team(game_id):
        raise ValueError("poem_room does not match the active contest team namespace")
    member_list = list(members)
    if not 4 <= len(member_list) <= 8:
        raise ValueError("roster must contain 4-8 members")
    if len(set(member_list)) != len(member_list):
        raise ValueError("roster members must be unique")
    return {
        "type": "sonnet.roster.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "poem_room": poem_room,
        "room_generation": room_generation,
        "members": member_list,
        "request_id": rid or request_id("roster"),
    }


def withdraw(game_id: str, rid: str | None = None) -> dict:
    return {
        "type": "sonnet.withdraw.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "request_id": rid or request_id("withdraw"),
    }


def word(
    game_id: str,
    room_generation: int,
    version: int,
    previous_state_hash: str,
    token: str,
    rid: str | None = None,
) -> dict:
    return {
        "type": "sonnet.word.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "room_generation": room_generation,
        "version": version,
        "previous_state_hash": previous_state_hash,
        "word": token,
        "request_id": rid or request_id("word"),
    }


def submit(
    game_id: str,
    poem_room: str,
    room_generation: int,
    final_version: int,
    poem_sha256: str,
    x_post_ids: list[str],
    rid: str | None = None,
) -> dict:
    if poem_room != ROOMS.team(game_id):
        raise ValueError("poem_room does not match the active contest team namespace")
    return {
        "type": "sonnet.submit.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "poem_room": poem_room,
        "room_generation": room_generation,
        "final_version": final_version,
        "poem_sha256": poem_sha256,
        "x_post_ids": x_post_ids,
        "request_id": rid or request_id("submit"),
    }
