from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS
from sonnet_chain.daemon import Daemon
from sonnet_chain.journal import Journal
from sonnet_chain.rosters import CanonicalRoster
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import StateStore
from sonnet_chain.team_intelligence import TeamIntelligence
from sonnet_chain.technocore import RoomPage, RoomRecord


def _signed(key: Ed25519PrivateKey, room: str, payload: dict, seq: int) -> dict:
    signer = Signer.__new__(Signer); signer._key = key; signer.did = did_of(key); signer._last_nonce = 0
    text = json.dumps(payload, separators=(",", ":"))
    stored, sig = signer.sign_room(room, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "text": stored, "sig": sig}


def _daemon(tmp_path: Path) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config("x", None, None, None, None, tmp_path, "c", state_db=tmp_path / "state.db")
    daemon.state = StateStore(daemon.cfg.state_db); daemon.journal = Journal(tmp_path / "j")
    daemon.wait = 0
    return daemon


def test_team_sources_verify_cold_once_then_zero_warm_and_restart(monkeypatch, tmp_path: Path):
    room = ROOMS.team("incremental"); generation = 3
    member_key = Ed25519PrivateKey.generate(); member = did_of(member_key)
    daemon = _daemon(tmp_path)
    for seq in range(1, 4951):
        daemon.state.record_event(room, seq, generation, {"seq": seq, "from": "noise", "text": "noise"})
    for seq in range(4951, 5001):
        daemon.state.record_event(room, seq, generation, _signed(member_key, room, {
            "type": "sonnet.word.v1", "contest_id": CONTEST_ID, "game_id": "incremental",
            "version": 0, "word": "moon", "request_id": f"w-{seq}",
        }, seq))
    from sonnet_chain import team_intelligence as module
    original = module.verify_room_signature; calls = 0
    def counted(*args, **kwargs):
        nonlocal calls; calls += 1; return original(*args, **kwargs)
    monkeypatch.setattr(module, "verify_room_signature", counted)
    intelligence = TeamIntelligence(daemon.state)
    intelligence.sync_sources(room, generation, {member})
    assert calls == 50
    for _ in range(5): intelligence.sync_sources(room, generation, {member})
    assert calls == 50
    daemon.state.record_event(room, 5001, generation, _signed(member_key, room, {
        "type": "sonnet.word.v1", "contest_id": CONTEST_ID, "game_id": "incremental",
        "version": 0, "word": "night", "request_id": "w-new",
    }, 5001))
    intelligence.sync_sources(room, generation, {member}); assert calls == 51
    daemon.state.close()
    restarted = StateStore(tmp_path / "state.db")
    TeamIntelligence(restarted).sync_sources(room, generation, {member})
    assert calls == 51
    restarted.close()


def test_room_terminal_fact_persists_and_same_cycle_room_read_is_cached(tmp_path: Path):
    daemon = _daemon(tmp_path); room = ROOMS.team("closed"); generation = 2
    referee_key = Ed25519PrivateKey.generate(); referee = did_of(referee_key)
    terminal = _signed(referee_key, room, {
        "type": "sonnet.word-accepted.v1", "contest_id": CONTEST_ID,
        "game_id": "closed", "request_id": "accepted", "version": 1,
    }, 1)
    class TC:
        reads = 0
        def owner_note(self, value): return referee
        def read_page(self, value, since, wait):
            self.reads += 1
            records = [] if since else [RoomRecord(1, referee, terminal["text"], terminal)]
            return RoomPage(records, generation, 1, 1)
    daemon.tc = TC(); daemon._team_room_open_cache = {}
    roster = CanonicalRoster("closed", room, generation, (referee,))
    assert not daemon._team_room_open(roster, referee)
    assert not daemon._team_room_open(roster, referee)
    assert daemon.tc.reads == 1
    assert daemon.state.team_room_closed(room, generation)
    daemon.state.close()
    restarted = StateStore(tmp_path / "state.db")
    assert restarted.team_room_closed(room, generation)
    restarted.close()


def test_room_open_fails_closed_on_internal_gap(tmp_path: Path):
    daemon = _daemon(tmp_path); room = ROOMS.team("gap"); generation = 1
    referee = did_of(Ed25519PrivateKey.generate())
    class TC:
        def owner_note(self, value): return referee
        def read_page(self, value, since, wait):
            return RoomPage([
                RoomRecord(1, None, "x", {"seq": 1, "text": "x"}),
                RoomRecord(3, None, "x", {"seq": 3, "text": "x"}),
            ], generation, 1, 3)
    daemon.tc = TC(); daemon._team_room_open_cache = {}
    assert not daemon._team_room_open(CanonicalRoster("gap", room, generation, (referee,)), referee)
    daemon.state.close()


def test_referee_probe_preserves_peer_event_for_later_semantic_sync(monkeypatch, tmp_path: Path):
    daemon = _daemon(tmp_path); room = ROOMS.team("scopes"); generation = 1
    referee = did_of(Ed25519PrivateKey.generate()); member_key = Ed25519PrivateKey.generate()
    member = did_of(member_key)
    daemon.state.record_event(room, 1, generation, _signed(member_key, room, {
        "type": "sonnet.word.v1", "contest_id": CONTEST_ID, "game_id": "scopes",
        "version": 0, "word": "moon", "request_id": "scope-word",
    }, 1))
    from sonnet_chain import team_intelligence as module
    original = module.verify_room_signature; calls = 0
    def counted(*args, **kwargs):
        nonlocal calls; calls += 1; return original(*args, **kwargs)
    monkeypatch.setattr(module, "verify_room_signature", counted)
    intelligence = TeamIntelligence(daemon.state)
    intelligence.sync_sources(room, generation, {referee}, referee)
    assert calls == 0
    intelligence.sync_sources(room, generation, {member, referee}, referee)
    assert calls == 1
    intelligence.sync_sources(room, generation, {member, referee}, referee)
    assert calls == 1
    daemon.state.close()
