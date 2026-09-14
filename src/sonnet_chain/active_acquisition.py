"""Bounded acquisition from durable verified facts; never from peer prose.

ACTIVE_TEAM_REQUEST is deferred to v0.2 pending authoritative current binding.
The existing STEP1 executor remains the only application/consent authority.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Callable

from .config import ROOMS, SARUKU_DID
from .formation_records import VerifiedFormationRecord
from .invites import record_time
from .rosters import CanonicalRoster, signed_roster, signed_withdrawal
from .state import StateStore
from .team_formation import FormationOpportunity, TeamFormationStore

ACTIVE_SEARCH_FRESHNESS = timedelta(minutes=60)
ACTIVE_SEARCH_BOOTSTRAP_LOOKBACK = timedelta(minutes=60)
ACTIVE_SEARCH_BOOTSTRAP_MAX_EVENTS = 256
# Overflow is unknown, not an invitation to reconstruct from a truncated tail.
ACTIVE_SEARCH_GAME_MAX_EVENTS = 1024


class ActiveAcquisition:
    def __init__(
        self, state: StateStore, generation: int, trusted_writers: set[str],
        room_open: Callable[[CanonicalRoster], bool], journal: Callable[..., None],
    ):
        self.state = state
        self.generation = generation
        self.trusted = trusted_writers
        self.room_open = room_open
        self.journal = journal
        self.formation = TeamFormationStore(state)

    def reconstruct(self, game_id: str, source_seq: int, now: datetime) -> FormationOpportunity | None:
        rows = self.state.db.execute(
            "SELECT normalized_payload FROM formation_events WHERE room=? AND generation=? "
            "AND game_id=? AND verified=1 ORDER BY seq LIMIT ?",
            (ROOMS.discovery, self.generation, game_id, ACTIVE_SEARCH_GAME_MAX_EVENTS + 1),
        ).fetchall()
        if not rows or len(rows) > ACTIVE_SEARCH_GAME_MAX_EVENTS:
            return None
        records = [VerifiedFormationRecord(json.loads(row[0])) for row in rows]
        history = self.formation.load_history(ROOMS.discovery, self.generation)
        if history is None or not history.is_complete(
            int(records[0]["seq"]), history.highest_observed_seq,
        ):
            return None
        current: dict[str, CanonicalRoster] = {}
        target = None
        withdrawer = None
        stamp = None
        original_anchors: set[str] = set()
        for record in records:
            seq = int(record["seq"])
            parsed = signed_roster(record)
            if parsed is not None:
                roster, signer = parsed
                # A successor structure involving this formation removes the
                # old vacancy. Never silently reinterpret it as a fresh offer.
                if target is not None and (
                    signer == withdrawer or
                    roster != target and set(roster.members) & set(target.members)
                ):
                    return None
                current[signer] = roster
                continue
            withdrawal = signed_withdrawal(record)
            if withdrawal is None:
                continue
            _, signer = withdrawal
            before = current.pop(signer, None)
            if seq != source_seq:
                continue
            stamp = record_time(record)
            if (
                before is None or len(before.members) != 4 or signer not in before.members
                or SARUKU_DID in before.members or stamp is None
                or not timedelta(0) <= now - stamp <= ACTIVE_SEARCH_FRESHNESS
            ):
                return None
            target, withdrawer = before, signer
            original_anchors = {
                member for member, roster in current.items()
                if roster == target and member in self.trusted
            }
            if not original_anchors:
                return None
        if target is None or stamp is None:
            return None
        anchors = sorted(
            signer for signer, roster in current.items()
            if roster == target and signer in original_anchors
        )
        if not anchors or not self.room_open(target):
            return None
        return FormationOpportunity(
            game_id, anchors[0], source_seq, stamp, target.poem_room,
            target.room_generation, False, "ACTIVE_VACANCY", self.generation,
        )

    def scan(self, now: datetime) -> int:
        key = f"formation_active_search_frontier:{self.generation}"
        frontier = self.state.get(key)
        bootstrap = frontier is None
        rows = self.state.db.execute(
            "SELECT seq,game_id,normalized_payload FROM formation_events "
            "WHERE room=? AND generation=? AND event_kind='ROSTER_WITHDRAWAL' "
            "AND verified=1 AND seq>? ORDER BY seq "
            + ("DESC" if bootstrap else "ASC") + " LIMIT ?",
            (ROOMS.discovery, self.generation, int(frontier or 0), ACTIVE_SEARCH_BOOTSTRAP_MAX_EVENTS),
        ).fetchall()
        if not rows:
            if bootstrap:
                self.state.set(key, 0)
            return 0
        observed = 0
        for row in reversed(rows) if bootstrap else rows:
            raw = json.loads(row["normalized_payload"])
            stamp = record_time(raw)
            lookback = ACTIVE_SEARCH_BOOTSTRAP_LOOKBACK if bootstrap else ACTIVE_SEARCH_FRESHNESS
            if stamp is None or not timedelta(0) <= now - stamp <= lookback:
                continue
            opportunity = self.reconstruct(row["game_id"], row["seq"], now)
            if opportunity is not None and self.formation.save_opportunity(opportunity):
                observed += 1
                self.journal(
                    "formation_active_search_candidate_observed", game_id=opportunity.game_id,
                    opportunity_kind=opportunity.opportunity_kind, source_seq=opportunity.source_seq,
                    reason_code="verified_four_member_vacancy", room_verified=True,
                )
        self.state.set(key, max(row["seq"] for row in rows))
        return observed

    def revalidate(self, opportunity: FormationOpportunity, now: datetime) -> bool:
        current = self.reconstruct(opportunity.game_id, opportunity.source_seq, now)
        return current == opportunity
