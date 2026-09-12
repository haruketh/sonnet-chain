from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.invites import direct_invite
from sonnet_chain.journal import Journal
from sonnet_chain.protocol import team_application
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import Phase, StateStore


def _signed(key: Ed25519PrivateKey, payload: dict, seq: int = 1) -> dict:
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = json.dumps(payload, separators=(",", ":"))
    stored, signature = signer.sign_room(ROOMS.discovery, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "sig": signature, "text": stored}


def _invite(
    key: Ed25519PrivateKey,
    *,
    target: str = SARUKU_DID,
    game_id: str | None = "deftink",
    kind: str = "sonnet.note.v1",
) -> dict:
    payload = {
        "type": kind,
        "contest_id": CONTEST_ID,
        "target_did": target,
        "role": "writer",
    }
    if game_id is not None:
        payload["game_id"] = game_id
    return _signed(key, payload)


def _daemon(tmp_path: Path) -> Daemon:
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config(
        "https://example.test", None, "https://x.com/sarukubt", None, None, tmp_path, "commit",
        state_db=tmp_path / "state.db",
    )
    daemon.state = StateStore(daemon.cfg.state_db)
    daemon.state.set("referee_did", referee)
    daemon.state.set("registered", True)
    daemon.state.set("discovery_advertised", True)
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")
    daemon.state.phase = Phase.DISCOVERY
    daemon.journal = Journal(tmp_path / "journal" / "sonnet.jsonl")
    daemon._read = lambda room: []
    return daemon


@pytest.mark.parametrize("kind", ["sonnet.note.v1", "sonnet.invite.v1", "sonnet.invite.v2"])
def test_detects_signed_direct_invite_for_saruku(kind: str) -> None:
    key = Ed25519PrivateKey.generate()

    invite = direct_invite(_invite(key, kind=kind))

    assert invite is not None
    assert invite.game_id == "deftink"
    assert invite.from_did == did_of(key)


def test_invite_for_another_did_is_ignored() -> None:
    other = did_of(Ed25519PrivateKey.generate())
    assert direct_invite(_invite(Ed25519PrivateKey.generate(), target=other)) is None


def test_invite_without_game_id_is_ignored() -> None:
    assert direct_invite(_invite(Ed25519PrivateKey.generate(), game_id=None)) is None


def test_unsigned_or_tampered_invite_is_ignored() -> None:
    record = _invite(Ed25519PrivateKey.generate())
    record["text"] = record["text"].replace("deftink", "other")
    assert direct_invite(record) is None


def test_active_team_prevents_application(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.state.set("active_team", "existing")
    daemon.state.record_event(
        ROOMS.discovery, 1, 1, _invite(Ed25519PrivateKey.generate())
    )
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert posted == []
    finally:
        daemon.state.close()


def test_prior_journal_application_prevents_duplicate(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.journal.append(
        "team_application_sent", game_id="deftink", target_from_did="did:key:zLead",
        request_id="application-old",
    )
    daemon.state.record_event(
        ROOMS.discovery, 1, 1, _invite(Ed25519PrivateKey.generate())
    )
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert posted == []
    finally:
        daemon.state.close()


def test_application_payload_is_exact() -> None:
    payload = team_application("deftink", "https://x.com/sarukubt", "application-1")

    assert payload == {
        "type": "sonnet.application.v1",
        "contest_id": "sonnet-2",
        "game_id": "deftink",
        "did": SARUKU_DID,
        "role": "writer",
        "x_account_url": "https://x.com/sarukubt",
        "request_id": "application-1",
        "text": (
            "YES deftink. Registered Sonnet-2 writer. No live roster consent. "
            "Ready to countersign the exact canonical roster when posted."
        ),
    }


def test_application_posts_once_and_stays_in_discovery(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    inviter = Ed25519PrivateKey.generate()
    daemon.state.record_event(ROOMS.discovery, 1, 1, _invite(inviter))
    posted = []
    daemon._post = lambda room, payload: posted.append((room, payload))
    daemon.live = True
    try:
        daemon._discovery()
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0][0] == ROOMS.discovery
        assert posted[0][1]["type"] == "sonnet.application.v1"
        assert daemon.state.phase == Phase.DISCOVERY
        assert daemon.state.active_team() is None
        records = json.loads(daemon.journal.path.read_text().splitlines()[-1])
        assert records["event"] == "team_application_sent"
        assert records["game_id"] == "deftink"
        assert records["target_from_did"] == did_of(inviter)
    finally:
        daemon.state.close()
