from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

GAME_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")


@dataclass
class TeamCandidate:
    game_id: str
    lead_did: str | None = None
    members: list[str] = field(default_factory=list)
    open_seats: int | None = None
    target_size: int | None = None
    poem_room: str | None = None
    room_generation: int | None = None
    writer_dids: list[str] = field(default_factory=list)
    x_accounts: list[str] = field(default_factory=list)
    prestart_evidence_claims: list[str] = field(default_factory=list)
    last_activity_seq: int = 0
    last_activity_ts: str | None = None
    roster_status: str | None = None
    capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_protocol_candidate(message: dict[str, Any]) -> TeamCandidate | None:
    text = message.get("text")
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind not in {"sonnet.team-request.v1", "sonnet.roster.v1", "sonnet.recruit.v1", "sonnet.note.v1"}:
        return None
    game_id = payload.get("game_id")
    if not isinstance(game_id, str) or not GAME_ID.fullmatch(game_id):
        return None
    members = payload.get("members", [])
    members = [x for x in members if isinstance(x, str)] if isinstance(members, list) else []
    writer_dids = payload.get("writer_dids", members)
    writer_dids = [x for x in writer_dids if isinstance(x, str)] if isinstance(writer_dids, list) else []
    return TeamCandidate(
        game_id=game_id,
        lead_did=message.get("from") if isinstance(message.get("from"), str) else None,
        members=members,
        open_seats=payload.get("open_seats") if isinstance(payload.get("open_seats"), int) else None,
        target_size=payload.get("target_size") if isinstance(payload.get("target_size"), int) else None,
        poem_room=payload.get("poem_room") if isinstance(payload.get("poem_room"), str) else None,
        room_generation=payload.get("room_generation") if isinstance(payload.get("room_generation"), int) else None,
        writer_dids=writer_dids,
        x_accounts=[x for x in payload.get("x_accounts", []) if isinstance(x, str)] if isinstance(payload.get("x_accounts"), list) else [],
        prestart_evidence_claims=[x for x in payload.get("prestart_evidence_claims", []) if isinstance(x, str)] if isinstance(payload.get("prestart_evidence_claims"), list) else [],
        last_activity_seq=int(message.get("seq", 0) or 0),
        last_activity_ts=message.get("ts") if isinstance(message.get("ts"), str) else None,
        roster_status=payload.get("roster_status") if isinstance(payload.get("roster_status"), str) else None,
        capabilities=[x for x in payload.get("capabilities", []) if isinstance(x, str)] if isinstance(payload.get("capabilities"), list) else [],
    )


def normalize_llm_candidates(items: Any, source_messages: list[dict[str, Any]]) -> list[TeamCandidate]:
    if not isinstance(items, list):
        return []
    latest_seq = max((int(x.get("seq", 0) or 0) for x in source_messages), default=0)
    senders = {x.get("from") for x in source_messages if isinstance(x.get("from"), str)}
    out = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("game_id"), str) or not GAME_ID.fullmatch(item["game_id"]):
            continue
        members = item.get("members", [])
        if not isinstance(members, list) or not all(isinstance(x, str) and x.startswith("did:key:") for x in members):
            continue
        lead = next(iter(senders)) if len(senders) == 1 else None
        out.append(TeamCandidate(
            game_id=item["game_id"], lead_did=lead, members=members,
            writer_dids=members, open_seats=item.get("open_seats") if isinstance(item.get("open_seats"), int) else None,
            target_size=item.get("target_size") if isinstance(item.get("target_size"), int) else None,
            capabilities=[x for x in item.get("capabilities", []) if isinstance(x, str)][:20],
            warnings=["LLM-extracted claim; not authenticated fact"] + [x for x in item.get("warnings", []) if isinstance(x, str)][:20],
            last_activity_seq=latest_seq,
        ))
    return out
