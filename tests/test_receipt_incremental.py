from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS
from sonnet_chain.daemon import Daemon
from sonnet_chain.journal import Journal
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import StateStore


def signed(key: Ed25519PrivateKey, room: str, payload: dict, seq: int) -> dict:
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = json.dumps(payload, separators=(",", ":"))
    stored, signature = signer.sign_room(room, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "sig": signature, "text": stored}


def daemon_fixture(tmp_path: Path, referee: str) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config(
        "https://example.test", None, None, None, None, tmp_path, "commit",
        state_db=tmp_path / "state.db",
    )
    daemon.state = StateStore(daemon.cfg.state_db)
    daemon.state.set("referee_did", referee)
    daemon.journal = Journal(tmp_path / "journal.jsonl")
    daemon._read = lambda room: []
    return daemon


def test_trusted_writer_lookup_never_rescans_registration_events(monkeypatch, tmp_path: Path):
    daemon = daemon_fixture(tmp_path, "did:key:zReferee")
    writer = "did:key:zTrustedWriter"
    with daemon.state.db:
        daemon.state.db.execute(
            "INSERT INTO receipts(room,generation,seq,kind,payload,accepted) VALUES(?,?,?,?,?,1)",
            (ROOMS.registration, 1, 1, "registration_accepted", json.dumps({
                "participant_did": writer, "sender_did": writer, "role": "writer",
            })),
        )
    for seq in range(1, 1001):
        daemon.state.record_event(ROOMS.registration, seq, 1, {
            "seq": seq, "from": f"participant-{seq}", "text": "irrelevant",
        })
    monkeypatch.setattr(
        "sonnet_chain.daemon.receipt_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("history rescanned")),
    )
    try:
        assert daemon._trusted_writer_dids() == {writer}
    finally:
        daemon.state.close()


def test_new_registration_receipt_verified_once_persisted_and_restart_safe(
    monkeypatch, tmp_path: Path,
):
    referee_key = Ed25519PrivateKey.generate()
    writer_key = Ed25519PrivateKey.generate()
    referee = did_of(referee_key)
    writer = did_of(writer_key)
    daemon = daemon_fixture(tmp_path, referee)
    raw = signed(referee_key, ROOMS.registration, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID,
        "request_id": "register-peer", "participant_did": writer,
        "sender_did": writer, "role": "writer", "status": "accepted",
    }, 7)
    daemon.state.record_event(ROOMS.registration, 7, 3, raw)

    from sonnet_chain import daemon as daemon_module
    original = daemon_module.receipt_candidate
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon_module, "receipt_candidate", counted)
    path = daemon.state.path
    daemon._receipts(ROOMS.registration)
    daemon._receipts(ROOMS.registration)
    assert calls == 1
    assert writer in daemon.state.trusted_writer_dids()
    daemon.state.close()

    restarted = StateStore(path)
    try:
        assert writer in restarted.trusted_writer_dids()
    finally:
        restarted.close()


def test_irrelevant_historical_events_are_cheaply_marked_once(monkeypatch, tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    for seq in range(1, 501):
        daemon.state.record_event(ROOMS.discovery, seq, 1, {
            "seq": seq, "from": "not-the-referee", "text": "noise",
        })
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr("sonnet_chain.daemon.receipt_candidate", counted)
    try:
        daemon._receipts(ROOMS.discovery)
        daemon._receipts(ROOMS.discovery)
        assert calls == 0
        assert daemon.state.unprocessed_receipt_events(ROOMS.discovery) == []
    finally:
        daemon.state.close()
