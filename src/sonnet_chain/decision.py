from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .discovery import TeamCandidate
from .teams import score_team

Action = Literal["join", "wait", "ignore", "start_own_team"]


@dataclass(frozen=True)
class TeamDecision:
    action: Action
    reason: str
    game_id: str | None = None


def deterministic_team_decision(candidates: list[TeamCandidate], minimum: float = 45) -> TeamDecision:
    # Unknown capacity is not evidence that a team is joinable.
    # Autonomous joins require an explicit positive open_seats claim.
    viable = [
        c for c in candidates
        if isinstance(c.open_seats, int) and c.open_seats > 0
    ]
    if not viable:
        return TeamDecision("wait", "no viable observed team")
    ranked = sorted(((score_team(c), c) for c in viable), key=lambda x: x[0].score, reverse=True)
    best_score, best = ranked[0]
    if best_score.score < minimum:
        return TeamDecision("wait", f"best deterministic score is {best_score.score}")
    return TeamDecision("join", "; ".join(best_score.reasons), best.game_id)
