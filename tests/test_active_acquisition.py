from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.active_acquisition import ActiveAcquisition, ACTIVE_SEARCH_BOOTSTRAP_MAX_EVENTS
from sonnet_chain.config import CONTEST_ID, ROOMS, SARUKU_DID
from sonnet_chain.protocol import team_application
from sonnet_chain.rosters import CanonicalRoster, roster_consensus
from sonnet_chain.signing import did_of
from sonnet_chain.state import Phase, StateStore
from sonnet_chain.team_formation import (
    ApplicationDelivery, ConsentDelivery, FormationHistoryState, FormationOpportunity,
    TeamFormationStore, start_epoch,
)
from test_invites import _daemon, _signed, _invite
from test_formation_reflex import FakeLLM


@pytest.fixture
def scene(tmp_path):
    daemon = _daemon(tmp_path)
    daemon.live = True
    daemon._post = lambda *args: None
    daemon._team_room_open = lambda roster, referee: True
    keys = [Ed25519PrivateKey.generate() for _ in range(6)]
    members = tuple(did_of(key) for key in keys[:4])
    roster = CanonicalRoster("vacancy", ROOMS.team("vacancy"), 3, members)
    now = datetime.now(timezone.utc)
    daemon._trusted_writer_dids = lambda: set(members)
    return daemon, keys, roster, now


def add(scene, key_index, seq, *, roster=None, withdraw=False, stamp=None):
    daemon, keys, default, now = scene
    target = roster or default
    payload = (
        {"type": "sonnet.withdraw.v1", "contest_id": CONTEST_ID,
         "game_id": target.game_id, "request_id": f"w-{seq}"}
        if withdraw else target.payload(f"r-{seq}")
    )
    raw = _signed(keys[key_index], payload, seq)
    raw["created_at"] = (stamp or now).isoformat()
    daemon.state.record_event(ROOMS.discovery, seq, 1, raw)
    daemon._sync_formation_events()
    history = FormationHistoryState(ROOMS.discovery, 1)
    history.observe(range(1, seq + 1))
    TeamFormationStore(daemon.state).save_history(history)
    return raw


def vacancy(scene):
    add(scene, 0, 1)
    add(scene, 3, 2)
    add(scene, 3, 3, withdraw=True)


def search(scene, **kwargs):
    daemon, _, roster, _ = scene
    return ActiveAcquisition(
        daemon.state, 1, kwargs.get("trusted", set(roster.members)),
        kwargs.get("room_open", lambda candidate: True), daemon._journal,
    )


def test_exact_current_four_member_withdrawal(scene):
    vacancy(scene)
    service = search(scene)
    assert service.scan(scene[3]) == 1
    opportunity = service.formation.opportunities()[0]
    assert opportunity.opportunity_kind == "ACTIVE_VACANCY"
    assert opportunity.inviter_did == scene[2].members[0]
    assert opportunity.source_seq == 3
    assert opportunity.observed_at == scene[3]


@pytest.mark.parametrize("kind,active,expected_calls", [
    ("ACTIVE_VACANCY", False, 0),
    ("TARGETED_INVITE", False, 1),
    ("ACTIVE_VACANCY", True, 1),
])
def test_team_reflex_requires_invitation_or_active_epoch(scene, monkeypatch, kind, active, expected_calls):
    from sonnet_chain import daemon as module
    daemon, _, roster, now = scene
    daemon.cfg = replace(daemon.cfg, openai_api_key_file=daemon.cfg.state_db.parent / "unused-key")
    # Isolate pre-application Reflex from the subsequent acquisition policy.
    daemon.state.set("registered", False)
    daemon.state.reset_cursor(ROOMS.discovery, 1)
    opportunity = FormationOpportunity(
        roster.game_id, roster.members[0], 1, now, roster.poem_room, 3,
        opportunity_kind=kind, source_generation=1,
    )
    formation = TeamFormationStore(daemon.state)
    formation.save_opportunity(opportunity)
    if kind == "TARGETED_INVITE" and not active:
        # A newer vacancy must not hide a genuine invitation further down
        # the existing descending-sequence opportunity list.
        formation.save_opportunity(FormationOpportunity(
            "unapplied", roster.members[0], 2, now, ROOMS.team("unapplied"), 3,
            opportunity_kind="ACTIVE_VACANCY", source_generation=1,
        ))
    if active:
        formation.save_epoch(start_epoch(opportunity, "existing", now))
    with daemon.state.db:
        daemon.state.db.execute(
            "INSERT INTO formation_reflex_frontiers(source_room,source_generation,last_seq) VALUES(?,?,0)",
            (roster.poem_room, 3),
        )
    raw = {"seq": 1, "from": roster.members[0], "text": "Saruku, are you available?"}
    daemon.state.persist_team_source(roster.poem_room, 3, 1, raw["from"], raw, None, None, "semantic")
    llm = FakeLLM()
    monkeypatch.setattr(module, "LLMClient", lambda *args, **kwargs: llm)
    syncs, opens, posts = [], [], []
    monkeypatch.setattr(module.TeamIntelligence, "sync_sources", lambda *args: syncs.append(args))
    daemon._team_room_open = lambda *args: opens.append(args) or True
    daemon._post = lambda room, payload: posts.append((room, payload))
    daemon._discovery()
    assert len(llm.calls) == len(syncs) == len(opens) == len(posts) == expected_calls
    if expected_calls:
        assert posts[0][0] == roster.poem_room
        assert posts[0][1]["type"] == "sonnet.note.v1"
    else:
        assert formation.active_epoch() is None


@pytest.mark.parametrize("case", ["historical", "withdrawn", "five", "nonmember", "no_survivor", "saruku"])
def test_non_vacancies_rejected(scene, case):
    _, keys, roster, now = scene
    if case == "five":
        roster = CanonicalRoster(roster.game_id, roster.poem_room, 3,
                                 roster.members + (did_of(keys[4]),))
    if case == "saruku":
        roster = CanonicalRoster(roster.game_id, roster.poem_room, 3,
                                 (SARUKU_DID,) + roster.members[1:])
    if case not in {"no_survivor", "saruku"}:
        add(scene, 0, 1, roster=roster)
    add(scene, 3, 2, roster=roster)
    seq = 3
    if case == "historical":
        replacement = CanonicalRoster(roster.game_id, roster.poem_room, 3,
                                     roster.members + (did_of(keys[4]),))
        add(scene, 3, seq, roster=replacement)
        seq += 1
    if case == "withdrawn":
        add(scene, 3, seq, withdraw=True)
        seq += 1
    add(scene, 4 if case == "nonmember" else 3, seq, roster=roster, withdraw=True)
    assert search(scene).reconstruct("vacancy", seq, now) is None


def test_no_trusted_current_anchor(scene):
    vacancy(scene)
    assert search(scene, trusted={scene[2].members[3]}).scan(scene[3]) == 0


def test_later_signer_cannot_invent_survivor_at_withdrawal(scene):
    add(scene, 3, 1)
    add(scene, 3, 2, withdraw=True)
    add(scene, 0, 3)
    assert search(scene).reconstruct("vacancy", 2, scene[3]) is None


@pytest.mark.parametrize("retention", [False, True])
def test_incomplete_history_fails_closed(scene, retention):
    vacancy(scene)
    history = FormationHistoryState(ROOMS.discovery, 1)
    history.observe([1, 3], available_from_seq=3 if retention else 1, available_through_seq=3)
    TeamFormationStore(scene[0].state).save_history(history)
    assert search(scene).scan(scene[3]) == 0


@pytest.mark.parametrize("reason", ["generation", "owner", "started", "frozen", "closed"])
def test_room_authority_failure_rejects(scene, reason):
    vacancy(scene)
    calls = []
    def closed(candidate):
        calls.append((candidate.poem_room, candidate.room_generation, reason))
        return False
    assert search(scene, room_open=closed).scan(scene[3]) == 0
    assert calls == [(scene[2].poem_room, 3, reason)]


def test_later_replacement_invalidates_before_application(scene):
    vacancy(scene)
    service = search(scene)
    service.scan(scene[3])
    opportunity = service.formation.opportunities()[0]
    old = scene[2]
    new = CanonicalRoster(old.game_id, old.poem_room, 3, old.members[:3] + (SARUKU_DID,))
    add(scene, 0, 4, roster=new)
    assert not service.revalidate(opportunity, scene[3])


@pytest.mark.parametrize("minutes", [60.01, 120, -1])
def test_trusted_timestamp_not_local_processing_time(scene, minutes):
    vacancy(scene)
    assert search(scene).scan(scene[3] + timedelta(minutes=minutes)) == 0


def test_freshness_boundary_and_missing_timestamp(scene):
    vacancy(scene)
    service = search(scene)
    assert service.reconstruct("vacancy", 3, scene[3] + timedelta(minutes=60))
    with scene[0].state.db:
        row = scene[0].state.db.execute("SELECT normalized_payload FROM formation_events WHERE seq=3").fetchone()
        raw = json.loads(row[0]); raw.pop("created_at")
        scene[0].state.db.execute("UPDATE formation_events SET normalized_payload=? WHERE seq=3", (json.dumps(raw),))
    assert service.reconstruct("vacancy", 3, scene[3]) is None


def test_application_wording():
    payload = team_application("vacancy", "https://x.com/sarukubt", "fixed", active_vacancy=True)
    assert payload["text"] == (
        "Saruku is available to join vacancy if you're still forming. "
        "Registered Sonnet-2 writer. No live roster consent. "
        "Ready to countersign the exact canonical roster when posted."
    )
    assert payload["type"] == "sonnet.application.v1"
    assert not any(word in payload["text"] for word in ["invited", "joined", "member", "team ready"])


def test_daemon_uses_existing_writeahead_and_no_membership(scene):
    vacancy(scene)
    daemon = scene[0]
    posted = []
    def post(room, payload):
        epoch = TeamFormationStore(daemon.state).active_epoch()
        assert epoch.application_request_id == payload["request_id"]
        assert daemon.state.pending_request("application:vacancy") == payload
        assert daemon.state.unresolved_protocol_intent("application")["request_id"] == payload["request_id"]
        posted.append(payload)
    daemon._post = post
    daemon._discovery()
    assert len(posted) == 1 and posted[0]["text"].startswith("Saruku is available")
    assert daemon.state.phase == Phase.DISCOVERY
    assert daemon.state.active_team() is None
    daemon._discovery()
    assert len(posted) == 1
    assert TeamFormationStore(daemon.state).active_epoch().application_delivery_state == ApplicationDelivery.POSTED_UNCONFIRMED


def test_consumed_and_frontier_survive_restart(scene, monkeypatch):
    vacancy(scene)
    service = search(scene)
    service.scan(scene[3])
    opportunity = service.formation.opportunities()[0]
    assert service.formation.consume_opportunity(opportunity, "fixed", scene[3])
    path = scene[0].state.path
    scene[0].state.close()
    scene[0].state = StateStore(path)
    restarted = search(scene)
    monkeypatch.setattr(restarted, "reconstruct", lambda *args: pytest.fail("historical reconstruction"))
    assert restarted.scan(scene[3]) == 0
    assert restarted.formation.opportunities() == []


def test_terminal_source_not_reused_new_source_possible(scene):
    vacancy(scene)
    service = search(scene)
    service.scan(scene[3])
    old = service.formation.opportunities()[0]
    epoch = start_epoch(old, "old", scene[3]); epoch.status = "HARD_STALLED"
    service.formation.save_epoch(epoch, active=False)
    assert not service.formation.opportunity_is_fresh_after_terminal(old)
    add(scene, 3, 4)
    add(scene, 3, 5, withdraw=True)
    assert service.scan(scene[3]) == 1
    new = service.formation.opportunities()[0]
    assert new.source_seq == 5
    assert service.formation.opportunity_is_fresh_after_terminal(new)


@pytest.mark.parametrize("conflict", ["consent", "withdraw", "application"])
def test_existing_conflict_blocks_new_application(scene, conflict):
    vacancy(scene)
    daemon = scene[0]
    epoch = start_epoch(FormationOpportunity("other", "anchor", 1, scene[3]), "old", scene[3])
    if conflict == "consent":
        epoch.consent_delivery_state = ConsentDelivery.CONSENT_DELIVERY_UNKNOWN
    if conflict == "withdraw":
        epoch.withdraw_pending = True
    TeamFormationStore(daemon.state).save_epoch(epoch)
    daemon._post = lambda *args: pytest.fail("conflicting application")
    daemon._discovery()
    assert TeamFormationStore(daemon.state).active_epoch().game_id == "other"


def test_targeted_invite_priority(scene):
    vacancy(scene)
    daemon, keys, _, now = scene
    raw = _invite(keys[0], game_id="targeted", seq=4, created_at=now.isoformat())
    daemon.state.record_event(ROOMS.discovery, 4, 1, raw)
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon._discovery()
    assert len(posted) == 1 and posted[0]["game_id"] == "targeted"


def test_targeted_does_not_preempt_active_vacancy(scene):
    vacancy(scene)
    daemon, keys, _, now = scene
    daemon._discovery()
    raw = _invite(keys[0], game_id="targeted", seq=4, created_at=now.isoformat())
    daemon.state.record_event(ROOMS.discovery, 4, 1, raw)
    daemon._post = lambda *args: pytest.fail("priority is not preemption authority")
    daemon._discovery()
    assert TeamFormationStore(daemon.state).active_epoch().game_id == "vacancy"


def test_verified_readback_confirms_application(scene):
    vacancy(scene)
    daemon = scene[0]
    daemon._discovery()
    payload = daemon.state.pending_request("application:vacancy")
    raw = {"seq": 4, "from": SARUKU_DID, "text": json.dumps(payload),
           "created_at": scene[3].isoformat()}
    # This is the durable trust boundary output, not a raw self-asserted marker.
    daemon.state.persist_formation_event(
        ROOMS.discovery, 1, 4, "APPLICATION_READBACK", "vacancy", SARUKU_DID,
        payload["request_id"], None, raw, raw["created_at"],
    )
    daemon._reconcile_formation_transport()
    epoch = TeamFormationStore(daemon.state).active_epoch()
    assert epoch.application_delivery_state == ApplicationDelivery.CONFIRMED
    assert epoch.application_observed_at == raw["created_at"]
    assert daemon.state.active_team() is None


def test_ambiguous_post_does_not_duplicate(scene):
    vacancy(scene)
    daemon = scene[0]
    def ambiguous(*args):
        raise TimeoutError("fixture transport ambiguity")
    daemon._post = ambiguous
    with pytest.raises(TimeoutError):
        daemon._discovery()
    intent = daemon.state.unresolved_protocol_intent("application")
    daemon._post = lambda *args: pytest.fail("second application")
    daemon._discovery()
    assert daemon.state.unresolved_protocol_intent("application")["request_id"] == intent["request_id"]


def test_active_team_blocks_vacancy(scene):
    vacancy(scene)
    scene[0].state.select_team("existing")
    scene[0]._post = lambda *args: pytest.fail("already has a team")
    scene[0]._discovery()
    assert TeamFormationStore(scene[0].state).active_epoch() is None


def test_dry_run_no_consumption_or_post(scene):
    vacancy(scene)
    scene[0].live = False
    scene[0]._post = lambda *args: pytest.fail("dry run")
    scene[0]._discovery()
    assert scene[0].state.get("dry_run_action")["text"].startswith("Saruku is available")
    assert TeamFormationStore(scene[0].state).active_epoch() is None
    assert len(TeamFormationStore(scene[0].state).opportunities()) == 1


def test_changed_room_at_final_revalidation_no_post(scene):
    vacancy(scene)
    daemon = scene[0]
    calls = 0
    def room_open(*args):
        nonlocal calls
        calls += 1
        return calls < 4
    daemon._team_room_open = room_open
    daemon._post = lambda *args: pytest.fail("changed binding")
    daemon._discovery()
    assert calls == 4
    assert TeamFormationStore(daemon.state).active_epoch() is None


def test_discovery_generation_change_rejects_old_opportunity(scene):
    vacancy(scene)
    service = search(scene)
    service.scan(scene[3])
    opportunity = service.formation.opportunities()[0]
    newer = ActiveAcquisition(scene[0].state, 2, set(scene[2].members), lambda r: True, lambda *a, **k: None)
    assert not newer.revalidate(opportunity, scene[3])


def test_legacy_opportunity_default_and_same_game_priority(scene):
    vacancy(scene)
    store = TeamFormationStore(scene[0].state)
    old = FormationOpportunity("vacancy", scene[2].members[0], 1, scene[3])
    store.save_opportunity(old)
    # Model an actual pre-field JSON document.
    with scene[0].state.db:
        scene[0].state.db.execute(
            "UPDATE formation_opportunities SET payload_json=json_remove(payload_json,'$.opportunity_kind')"
        )
    search(scene).scan(scene[3])
    assert {item.opportunity_kind for item in store.opportunities()} == {"TARGETED_INVITE", "ACTIVE_VACANCY"}


def test_anchor_must_sign_new_roster_after_source(scene):
    vacancy(scene)
    old = scene[2]
    new = CanonicalRoster(old.game_id, old.poem_room, 3, old.members[:3] + (SARUKU_DID,))
    add(scene, 1, 4, roster=new)
    add(scene, 2, 5, roster=new)
    records = scene[0].state.formation_events(ROOMS.discovery, 1, game_id="vacancy")
    assert roster_consensus(records, anchor_signer=old.members[0], min_anchor_seq=3) == []
    add(scene, 0, 6, roster=new)
    records = scene[0].state.formation_events(ROOMS.discovery, 1, game_id="vacancy")
    assert len(roster_consensus(records, anchor_signer=old.members[0], min_anchor_seq=3)) == 1


def test_bootstrap_bounded_and_incremental(scene, monkeypatch):
    daemon, _, _, now = scene
    with daemon.state.db:
        daemon.state.db.executemany(
            "INSERT INTO formation_events VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            [(ROOMS.discovery, 1, seq, "ROSTER_WITHDRAWAL", "noise", "sender", None, None,
              json.dumps({"seq": seq, "created_at": now.isoformat()}), now.isoformat())
             for seq in range(1, 50001)],
        )
    service = search(scene)
    calls = []
    monkeypatch.setattr(service, "reconstruct", lambda game, seq, now: calls.append(seq))
    service.scan(now)
    assert len(calls) == ACTIVE_SEARCH_BOOTSTRAP_MAX_EVENTS
    calls.clear()
    assert service.scan(now) == 0 and calls == []
    plan = daemon.state.db.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM formation_events WHERE room=? AND generation=? "
        "AND event_kind='ROSTER_WITHDRAWAL' AND seq>? ORDER BY seq LIMIT 256",
        (ROOMS.discovery, 1, 50000),
    ).fetchall()
    assert any("formation_events_kind_seq" in str(tuple(row)) for row in plan)
