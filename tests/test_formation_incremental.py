from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.journal import Journal
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import StateStore


def _daemon(tmp_path: Path) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config("https://example.test", None, None, None, None, tmp_path, "commit",
                        state_db=tmp_path / "state.db")
    daemon.state = StateStore(daemon.cfg.state_db)
    daemon.journal = Journal(tmp_path / "journal.jsonl")
    return daemon


def _signed_roster(key: Ed25519PrivateKey, seq: int) -> dict:
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    members = [signer.did, SARUKU_DID]
    members.extend(did_of(Ed25519PrivateKey.generate()) for _ in range(2))
    payload = {"type": "sonnet.roster.v1", "contest_id": CONTEST_ID,
               "game_id": "incremental", "poem_room": ROOMS.team("incremental"),
               "room_generation": 1, "members": members, "request_id": f"r-{seq}"}
    text = json.dumps(payload, separators=(",", ":"))
    stored, sig = signer.sign_room(ROOMS.discovery, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "sig": sig, "text": stored}


def test_discovery_history_is_verified_only_incrementally(monkeypatch, tmp_path: Path):
    daemon = _daemon(tmp_path)
    for seq in range(1, 36001):
        daemon.state.record_event(ROOMS.discovery, seq, 4, {
            "seq": seq, "from": "noise", "text": "not-json",
        })
    key = Ed25519PrivateKey.generate()
    daemon.state.record_event(ROOMS.discovery, 36001, 4, _signed_roster(key, 36001))

    from sonnet_chain import daemon as daemon_module
    original = daemon_module.signed_roster
    calls = 0

    def counted(raw):
        nonlocal calls
        calls += 1
        return original(raw)

    monkeypatch.setattr(daemon_module, "signed_roster", counted)
    daemon._sync_formation_events()
    assert calls == 1
    daemon._sync_formation_events()
    assert calls == 1

    daemon.state.record_event(ROOMS.discovery, 36002, 4, {
        "seq": 36002, "from": "noise", "text": "{}",
    })
    daemon._sync_formation_events()
    assert calls == 1
    daemon.state.record_event(ROOMS.discovery, 36003, 4, _signed_roster(key, 36003))
    daemon._sync_formation_events()
    assert calls == 2
    path = daemon.state.path
    daemon.state.close()

    restarted = _daemon(tmp_path)
    restarted._sync_formation_events()
    assert calls == 2
    assert len(restarted.state.formation_events(ROOMS.discovery, 4)) == 2
    restarted.state.close()


def test_untrusted_raw_marker_cannot_bypass_signature_verification(tmp_path: Path):
    daemon = _daemon(tmp_path)
    raw = _signed_roster(Ed25519PrivateKey.generate(), 1)
    raw["_formation_verified"] = True
    raw["sig"] = "invalid"
    daemon.state.record_event(ROOMS.discovery, 1, 1, raw)
    daemon._sync_formation_events()
    assert daemon.state.formation_events(ROOMS.discovery, 1) == []
    daemon.state.close()
