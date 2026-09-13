from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pytest

from sonnet_chain.config import Config, ROOMS
from sonnet_chain.daemon import Daemon
from sonnet_chain.journal import Journal
from sonnet_chain.state import Phase, StateStore
from sonnet_chain.technocore import RoomRecord
from sonnet_chain.team_formation import (
    ApplicationDelivery, ConsentDelivery, FormationOpportunity, TeamFormationStore,
    formation_decision, start_epoch,
)


def epoch(now: datetime):
    item = start_epoch(FormationOpportunity("game", "did:key:zInviter", 7, now), "app-1", now)
    item.application_delivery_state = ApplicationDelivery.CONFIRMED
    item.application_observed_at = now.isoformat()
    return item


def daemon_fixture(tmp_path: Path) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config("https://example.test", None, None, None, None, tmp_path, "commit",
                        state_db=tmp_path / "state.db")
    daemon.state = StateStore(daemon.cfg.state_db)
    daemon.journal = Journal(tmp_path / "journal" / "sonnet.jsonl")
    return daemon


def test_crash_after_consent_intent_never_recovers_preconsent(tmp_path: Path):
    path = tmp_path / "state.db"; now = datetime.now(timezone.utc)
    state = StateStore(path); formation = TeamFormationStore(state); item = epoch(now)
    payload = {"type": "sonnet.roster.v1", "request_id": "consent-1", "game_id": "game"}
    state.persist_protocol_intent("consent-1", "roster", ROOMS.discovery, payload,
                                  ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
                                  game_id="game", roster_fingerprint="abc", created_at=now.isoformat())
    item.consent_delivery_state = ConsentDelivery.CONSENT_POSTED_UNCONFIRMED
    item.consent_request_id = "consent-1"; item.consent_roster_fingerprint = "abc"
    formation.save_epoch(item); state.close()

    state = StateStore(path); recovered = TeamFormationStore(state).active_epoch()
    assert recovered.possibly_consented
    assert state.unresolved_protocol_intent("roster")["request_id"] == "consent-1"
    assert formation_decision(recovered, history_complete=True, challenger_stronger=True,
                              now=now + timedelta(hours=2)) == "RECONCILE_CONSENT"
    state.close()


def test_crash_before_send_preserves_exact_same_payload(tmp_path: Path):
    path = tmp_path / "state.db"; now = datetime.now(timezone.utc)
    payload = {"type": "sonnet.roster.v1", "request_id": "same", "members": ["a", "b"]}
    state = StateStore(path)
    state.persist_protocol_intent("same", "roster", ROOMS.discovery, payload,
                                  ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
                                  roster_fingerprint="fp", created_at=now.isoformat())
    state.close(); state = StateStore(path)
    assert state.unresolved_protocol_intent("roster")["payload"] == payload
    state.close()


def test_withdraw_pending_survives_restart(tmp_path: Path):
    path = tmp_path / "state.db"; now = datetime.now(timezone.utc)
    state = StateStore(path); formation = TeamFormationStore(state); item = epoch(now)
    payload = {"type": "sonnet.withdraw.v1", "request_id": "withdraw-1", "game_id": "game"}
    state.persist_protocol_intent("withdraw-1", "withdraw", ROOMS.discovery, payload,
                                  "POSTED_UNCONFIRMED", game_id="game", created_at=now.isoformat())
    item.withdraw_pending = True; item.withdraw_request_id = "withdraw-1"
    formation.save_epoch(item); state.close()
    state = StateStore(path); recovered = TeamFormationStore(state).active_epoch()
    assert recovered.withdraw_pending and recovered.withdraw_request_id == "withdraw-1"
    assert state.unresolved_protocol_intent("withdraw")["payload"] == payload
    state.close()


def test_legacy_pending_roster_bootstraps_fail_closed(tmp_path: Path):
    path = tmp_path / "state.db"; state = StateStore(path)
    payload = {"type": "sonnet.roster.v1", "request_id": "legacy", "game_id": "game"}
    state.reserve_request("legacy", "roster", payload); state.close()
    state = StateStore(path)
    assert state.unresolved_protocol_intent("roster")["delivery_state"] == "CONSENT_DELIVERY_UNKNOWN"
    state.close()


def test_legacy_pending_withdraw_reconstructs_possible_consent(tmp_path: Path):
    daemon = daemon_fixture(tmp_path)
    daemon.state.phase = Phase.DISCOVERY
    payload = {"type": "sonnet.withdraw.v1", "request_id": "legacy-w", "game_id": "game"}
    daemon.state.reserve_request("legacy-w", "withdraw", payload)
    try:
        daemon._bootstrap_formation_state()
        recovered = TeamFormationStore(daemon.state).active_epoch()
        assert recovered.withdraw_pending
        assert recovered.possibly_consented
    finally:
        daemon.state.close()


def test_replay_produces_same_decision(tmp_path: Path):
    path = tmp_path / "state.db"; now = datetime.now(timezone.utc)
    state = StateStore(path); item = epoch(now); TeamFormationStore(state).save_epoch(item)
    first = formation_decision(item, history_complete=True, now=now + timedelta(minutes=20))
    state.close(); state = StateStore(path)
    rebuilt = TeamFormationStore(state).active_epoch()
    second = formation_decision(rebuilt, history_complete=True, now=now + timedelta(minutes=20))
    assert first == second == "STAY"
    state.close()


def test_gap_at_hard_stall_uses_uncertain_preconsent_exit():
    now = datetime.now(timezone.utc); item = epoch(now)
    assert formation_decision(item, history_complete=False,
                              now=now + timedelta(minutes=60)) == "ABANDON_UNCERTAIN_HISTORY"


def test_wait_roster_ready_withdraws_only_when_authoritatively_legal():
    now = datetime.now(timezone.utc); item = epoch(now)
    item.consent_delivery_state = ConsentDelivery.CONSENT_CONFIRMED
    assert formation_decision(item, history_complete=True, wait_roster_ready=True,
                              withdrawal_legal=False, now=now + timedelta(minutes=60)) == "RECONCILE"
    assert formation_decision(item, history_complete=True, wait_roster_ready=True,
                              withdrawal_legal=True, now=now + timedelta(minutes=60)) == "SAFE_WITHDRAW"


def test_daemon_gap_reconciliation_fills_interval_and_reruns_safely(tmp_path: Path):
    daemon = daemon_fixture(tmp_path)
    daemon.state.record_event("room", 1, 1, {"seq": 1, "text": "one"})
    daemon.state.record_event("room", 3, 1, {"seq": 3, "text": "three"})
    from sonnet_chain.team_formation import FormationHistoryState
    history = FormationHistoryState("room", 1); history.observe([1, 3])
    TeamFormationStore(daemon.state).save_history(history)
    missing = RoomRecord(2, None, "two", {"seq": 2, "text": "two"})
    daemon.tc = type("TC", (), {"export_history": lambda self, room: ([missing], 1)})()
    try:
        assert daemon._reconcile_history("room", 1, 1, 3)
        assert TeamFormationStore(daemon.state).load_history("room", 1).is_complete(1, 3)
        events = [json.loads(line)["event"] for line in daemon.journal.path.read_text().splitlines()]
        assert "formation_history_reconciled" in events
    finally:
        daemon.state.close()


def test_daemon_gap_reconciliation_failure_remains_fail_closed(tmp_path: Path):
    daemon = daemon_fixture(tmp_path)
    from sonnet_chain.team_formation import FormationHistoryState
    history = FormationHistoryState("room", 1); history.observe([1, 3])
    TeamFormationStore(daemon.state).save_history(history)
    daemon.tc = type("TC", (), {"export_history": lambda self, room: (_ for _ in ()).throw(RuntimeError("no export"))})()
    try:
        assert not daemon._reconcile_history("room", 1, 1, 3)
        assert TeamFormationStore(daemon.state).load_history("room", 1).reconciliation_required
    finally:
        daemon.state.close()
