from __future__ import annotations

from dataclasses import dataclass

from .config import SARUKU_DID
from .discovery import TeamCandidate
from .poetry import did_letters


@dataclass(frozen=True)
class TeamScore:
    game_id: str
    score: float
    reasons: tuple[str, ...]


def score_team(team: TeamCandidate, saruku_did: str = SARUKU_DID) -> TeamScore:
    members = list(dict.fromkeys(team.writer_dids or team.members))
    before = set().union(*(did_letters(d) for d in members)) if members else set()
    saruku = did_letters(saruku_did)
    after = before | saruku
    complement = len((set("abcdefghijklmnopqrstuvwxyz") - saruku) & before)
    added = len(saruku - before)
    score = 30 * len(after) / 26 + min(15, added * 3) + min(10, complement * 2.5)
    projected = len(set(members) | {saruku_did})
    if 4 <= projected <= 6:
        score += 10
    elif projected <= 8:
        score += 4
    if team.open_seats is not None and team.open_seats <= 0 and saruku_did not in members:
        score -= 50
    score += min(10, len(team.prestart_evidence_claims) * 2)
    score += min(5, len(team.capabilities))
    score -= min(20, len(team.warnings) * 5)
    return TeamScore(team.game_id, round(score, 2), (f"alphabet coverage {len(after)}/26", f"adds {added} letters", f"covers {complement} Saruku gaps"))


def valid_roster(
    members: list[str], game_id: str, poem_room: str, room_generation: int,
    expected_game_id: str, expected_room: str, expected_generation: int,
    registered: bool, active_team: str | None,
) -> bool:
    return (
        registered and active_team == game_id == expected_game_id
        and poem_room == expected_room and room_generation == expected_generation
        and 4 <= len(members) <= 8 and len(set(members)) == len(members)
        and SARUKU_DID in members and all(x.startswith("did:key:") for x in members)
    )
