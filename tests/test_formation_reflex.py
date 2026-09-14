from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sonnet_chain.config import ROOMS, SARUKU_DID
from sonnet_chain.formation_reflex import FormationReflexResponder
from sonnet_chain.state import Phase, StateStore
from sonnet_chain.team_formation import (
    FormationOpportunity, TeamFormationStore, start_epoch,
)


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result = result or {"action": "reply", "text": "Thanks — I saw this.", "reason_code": "direct"}
        self.error = error
        self.calls = []

    def structured(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


def _store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    state.phase = Phase.DISCOVERY
    return state


def _formation_event(state, seq=1, kind="TARGETED_RECRUITMENT_NOTE", members=None, sender="did:key:zWriter"):
    payload = {"type": "sonnet.note.v1", "game_id": "g", "target_did": SARUKU_DID,
               "text": "Please consent now"}
    if kind == "ROSTER_CONSENT":
        payload = {"type": "sonnet.roster.v1", "game_id": "g", "members": members or []}
    raw = {"seq": seq, "from": sender, "text": json.dumps(payload), "created_at": datetime.now(timezone.utc).isoformat()}
    state.persist_formation_event(ROOMS.discovery, 2, seq, kind, "g", sender, None, None, raw, raw["created_at"])


def _responder(state, llm, posts, live=False):
    return FormationReflexResponder(state, llm, lambda *a, **k: None,
                                    lambda room, payload: posts.append((room, payload)), live)


def test_targeted_invite_calls_llm_once_and_survives_restart(tmp_path):
    state = _store(tmp_path); _formation_event(state)
    llm = FakeLLM(); posts = []
    first = _responder(state, llm, posts).run_once(2)
    assert first.processed and first.payload["type"] == "sonnet.note.v1"
    assert len(llm.calls) == 1 and posts == []
    state.close()
    reopened = StateStore(tmp_path / "state.db"); reopened.phase = Phase.DISCOVERY
    again = FakeLLM()
    assert not _responder(reopened, again, []).run_once(2).processed
    assert again.calls == []


def test_protocol_state_is_deterministic_and_reply_is_note_only(tmp_path):
    state = _store(tmp_path); _formation_event(state)
    epoch = start_epoch(FormationOpportunity("other", "did:key:zLead", 4, datetime.now(timezone.utc)),
                        "application-other", datetime.now(timezone.utc))
    TeamFormationStore(state).save_epoch(epoch)
    llm = FakeLLM(); result = _responder(state, llm, []).run_once(2)
    assert llm.calls[0][1]["protocol_state"] == "FORMING_OTHER_GAME"
    assert result.payload["type"] == "sonnet.note.v1"
    assert result.payload["purpose"] == "formation_reflex"
    assert state.active_team() is None


def test_roster_requires_saruku_and_self_is_ignored(tmp_path):
    state = _store(tmp_path); _formation_event(state, kind="ROSTER_CONSENT", members=["did:key:zOther"])
    _formation_event(state, seq=2, sender=SARUKU_DID)
    llm = FakeLLM()
    assert not _responder(state, llm, []).run_once(2).processed
    assert llm.calls == []


def test_llm_failure_uses_safe_note_fallback_without_protocol_transition(tmp_path):
    state = _store(tmp_path); _formation_event(state)
    result = _responder(state, FakeLLM(error=RuntimeError("offline")), []).run_once(2)
    assert result.payload["type"] == "sonnet.note.v1"
    assert state.phase == Phase.DISCOVERY and state.active_team() is None


def test_team_room_direct_alias_is_incremental_and_unrelated_is_ignored(tmp_path):
    state = _store(tmp_path); room = ROOMS.team("g")
    unrelated = {"seq": 1, "from": "did:key:zWriter", "text": "hello"}
    direct = {"seq": 2, "from": "did:key:zWriter", "text": "@Saruku, are you available?"}
    state.persist_team_source(room, 7, 1, unrelated["from"], unrelated, None, None, "semantic")
    state.persist_team_source(room, 7, 2, direct["from"], direct, None, None, "semantic")
    llm = FakeLLM(); result = _responder(state, llm, []).run_once(7, team_game_id="g", team_room=room)
    assert result.payload is not None and len(llm.calls) == 1
    assert result.payload["target_did"] == direct["from"]
    assert not _responder(state, FakeLLM(), []).run_once(7, team_game_id="g", team_room=room).processed


def test_writing_disables_reflex(tmp_path):
    state = _store(tmp_path); _formation_event(state); state.phase = Phase.WRITING
    llm = FakeLLM()
    assert not _responder(state, llm, []).run_once(2).processed
    assert llm.calls == []


def test_consumed_and_terminal_stale_opportunities_are_durable(tmp_path):
    state = _store(tmp_path); store = TeamFormationStore(state); now = datetime.now(timezone.utc)
    old = FormationOpportunity("g", "did:key:zLead", 10, now - timedelta(hours=1))
    fresh = FormationOpportunity("g", "did:key:zLead", 20, now + timedelta(seconds=1))
    store.save_opportunity(old)
    assert store.consume_opportunity(old, "application-old", now)
    assert store.opportunities() == []
    epoch = start_epoch(old, "application-old", now); epoch.status = "hard_stalled"
    store.save_epoch(epoch, active=False)
    assert not store.opportunity_is_fresh_after_terminal(old)
    store.save_opportunity(fresh)
    assert store.opportunity_is_fresh_after_terminal(fresh)
    state.close(); reopened = StateStore(tmp_path / "state.db")
    assert [x.source_seq for x in TeamFormationStore(reopened).opportunities()] == [20]


def test_scoped_formation_query_does_not_decode_unrelated_history(tmp_path):
    state = _store(tmp_path)
    raw = json.dumps({"seq": 1, "from": "did:key:z", "text": "{}"})
    with state.db:
        state.db.executemany(
            "INSERT INTO formation_events(room,generation,seq,event_kind,game_id,sender_did,"
            "normalized_payload,verified) VALUES(?,?,?,?,?,?,?,1)",
            [(ROOMS.discovery, 2, i, "ROSTER_CONSENT", f"old-{i}", "did:key:z", raw)
             for i in range(1, 50001)],
        )
        state.db.executemany(
            "INSERT INTO formation_events(room,generation,seq,event_kind,game_id,sender_did,"
            "normalized_payload,verified) VALUES(?,?,?,?,?,?,?,1)",
            [(ROOMS.discovery, 2, 50000 + i, "ROSTER_CONSENT", "current", "did:key:z", raw)
             for i in range(1, 6)],
        )
    rows = state.formation_events(ROOMS.discovery, 2, game_id="current", min_seq=50001)
    assert len(rows) == 5
