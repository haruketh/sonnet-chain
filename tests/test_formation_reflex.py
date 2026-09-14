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
    with state.db:
        state.db.execute(
            "INSERT INTO formation_reflex_frontiers(source_room,source_generation,last_seq) "
            "VALUES(?,?,0)", (ROOMS.discovery, 2),
        )
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


def test_irrelevant_roster_does_not_starve_later_targeted_invite(tmp_path):
    state = _store(tmp_path)
    _formation_event(state, seq=10, kind="ROSTER_CONSENT", members=["did:key:zOther"])
    _formation_event(state, seq=11)
    llm = FakeLLM()
    result = _responder(state, llm, []).run_once(2)
    assert result.processed and len(llm.calls) == 1
    assert state.db.execute(
        "SELECT last_seq FROM formation_reflex_frontiers WHERE source_room=? AND source_generation=2",
        (ROOMS.discovery,),
    ).fetchone()["last_seq"] == 11


def test_first_activation_baselines_history_then_accepts_new_event(tmp_path):
    state = _store(tmp_path)
    state.db.execute("DELETE FROM formation_reflex_frontiers")
    _formation_event(state, seq=10)
    llm = FakeLLM()
    assert not _responder(state, llm, []).run_once(2).processed
    assert llm.calls == []
    _formation_event(state, seq=11)
    assert _responder(state, llm, []).run_once(2).processed
    assert len(llm.calls) == 1


def test_llm_failure_uses_safe_note_fallback_without_protocol_transition(tmp_path):
    state = _store(tmp_path); _formation_event(state)
    result = _responder(state, FakeLLM(error=RuntimeError("offline")), []).run_once(2)
    assert result.payload["type"] == "sonnet.note.v1"
    assert state.phase == Phase.DISCOVERY and state.active_team() is None


def test_team_room_direct_alias_is_incremental_and_unrelated_is_ignored(tmp_path):
    state = _store(tmp_path); room = ROOMS.team("g")
    with state.db:
        state.db.execute(
            "INSERT INTO formation_reflex_frontiers(source_room,source_generation,last_seq) "
            "VALUES(?,?,0)", (room, 7),
        )
    unrelated = {"seq": 1, "from": "did:key:zWriter", "text": "hello"}
    direct = {"seq": 2, "from": "did:key:zWriter", "text": "@Saruku, are you available?"}
    state.persist_team_source(room, 7, 1, unrelated["from"], unrelated, None, None, "semantic")
    state.persist_team_source(room, 7, 2, direct["from"], direct, None, None, "semantic")
    llm = FakeLLM(); result = _responder(state, llm, []).run_once(7, team_game_id="g", team_room=room)
    assert result.payload is not None and len(llm.calls) == 1
    assert result.payload["target_did"] == direct["from"]
    assert not _responder(state, FakeLLM(), []).run_once(7, team_game_id="g", team_room=room).processed


def test_capability_facts_are_system_controlled_llm_input(tmp_path):
    state = _store(tmp_path); _formation_event(state)
    llm = FakeLLM({"action": "reply", "text": "I can help with syllables and turn coordination.",
                   "reason_code": "capability_question"})
    result = _responder(state, llm, []).run_once(2)
    capabilities = llm.calls[0][1]["capabilities"]["authoritative_text"]
    assert "coordinate turns" in capabilities and "syllables" in capabilities
    assert "DID-letter constraints" in capabilities and "registered X account" in capabilities
    assert result.payload["text"] == "I can help with syllables and turn coordination."


def test_team_room_50k_history_is_baselined_and_only_new_direct_is_parsed(tmp_path):
    state = _store(tmp_path); room = ROOMS.team("g"); raw = json.dumps(
        {"seq": 1, "from": "did:key:zWriter", "text": "ordinary chatter"}
    )
    with state.db:
        state.db.executemany(
            "INSERT INTO team_source_events(room,generation,seq,sender_did,payload,verified) "
            "VALUES(?,?,?,?,?,1)",
            [(room, 7, seq, "did:key:zWriter", raw) for seq in range(1, 50001)],
        )
    llm = FakeLLM(); responder = _responder(state, llm, [])
    assert not responder.run_once(7, team_game_id="g", team_room=room).processed
    direct = {"seq": 50001, "from": "did:key:zWriter", "text": "Saruku, can you help?"}
    state.persist_team_source(room, 7, 50001, direct["from"], direct, None, None, "semantic")
    assert responder.run_once(7, team_game_id="g", team_room=room).processed
    assert len(llm.calls) == 1


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


def test_lead_verified_survives_durable_opportunity_round_trip(tmp_path):
    state = _store(tmp_path); store = TeamFormationStore(state)
    store.save_opportunity(FormationOpportunity(
        "lead-game", "did:key:zLead", 9, datetime.now(timezone.utc),
        lead_verified=True,
    ))
    assert store.opportunities(game_id="lead-game")[0].lead_verified is True


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
