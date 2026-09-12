from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.invites import application_should_expire, direct_invite, pending_application
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
    seq: int = 1,
    created_at: str | None = None,
    with_room: bool = False,
) -> dict:
    payload = {
        "type": kind,
        "contest_id": CONTEST_ID,
        "target_did": target,
        "role": "writer",
    }
    if game_id is not None:
        payload["game_id"] = game_id
        if with_room:
            payload["poem_room"] = ROOMS.team(game_id)
            payload["room_generation"] = 3
    record = _signed(key, payload, seq)
    if created_at is not None:
        record["created_at"] = created_at
    return record


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


def _record_application(
    daemon: Daemon,
    game_id: str,
    age_minutes: int,
    invite_seq: int | None = None,
) -> None:
    daemon.journal.path.parent.mkdir(parents=True, exist_ok=True)
    sent_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    record = {
        "ts": sent_at.isoformat().replace("+00:00", "Z"),
        "event": "team_application_sent",
        "game_id": game_id,
        "target_from_did": "did:key:zLead",
        "request_id": f"application-{game_id}",
    }
    if invite_seq is not None:
        record["invite_seq"] = invite_seq
    daemon.journal.path.write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )


def _ready_roster(
    daemon: Daemon,
    game_id: str,
    start_seq: int = 1,
) -> None:
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    payload = {
        "type": "sonnet.roster.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "poem_room": ROOMS.team(game_id),
        "room_generation": 3,
        "members": members,
        "request_id": "roster",
    }
    for seq, key in enumerate(keys, start_seq):
        record = _signed(key, payload, seq)
        record["ts"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        daemon.state.record_event(ROOMS.discovery, seq, 1, record)


def _partial_roster(
    daemon: Daemon,
    game_id: str,
    *,
    age_minutes: int,
    seq: int = 1,
) -> None:
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    payload = {
        "type": "sonnet.roster.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "poem_room": ROOMS.team(game_id),
        "room_generation": 3,
        "members": members,
        "request_id": f"roster-{seq}",
    }
    record = _signed(keys[0], payload, seq)
    record["ts"] = (
        datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    ).isoformat().replace("+00:00", "Z")
    daemon.state.record_event(ROOMS.discovery, seq, 1, record)


def _team_room(daemon: Daemon):
    referee = daemon.state.get("referee_did")

    class TeamRoom:
        def owner_note(self, room):
            return referee

        def read_page(self, room, since, wait):
            return [], 3

    return TeamRoom()


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


def test_multiple_invites_select_only_newest_candidate(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.state.record_event(
        ROOMS.discovery, 1, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="older", seq=1,
                created_at="2026-01-01T00:00:00Z"),
    )
    daemon.state.record_event(
        ROOMS.discovery, 2, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="newer", seq=2,
                created_at="2026-01-01T00:01:00Z"),
    )
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["game_id"] == "newer"
    finally:
        daemon.state.close()


def test_verified_room_outranks_newer_unverified_invite(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    referee = daemon.state.get("referee_did")
    daemon.state.record_event(
        ROOMS.discovery, 1, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="verified", seq=1,
                created_at="2026-01-01T00:00:00Z", with_room=True),
    )
    daemon.state.record_event(
        ROOMS.discovery, 2, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="newer", seq=2,
                created_at="2026-01-01T00:01:00Z"),
    )

    class TeamRoom:
        def owner_note(self, room):
            return referee

        def read_page(self, room, since, wait):
            return [], 3

    daemon.tc = TeamRoom()
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["game_id"] == "verified"
    finally:
        daemon.state.close()


def test_pending_application_blocks_another_game(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.journal.append(
        "team_application_sent", game_id="hayes", target_from_did="did:key:zLead",
        request_id="application-hayes",
    )
    daemon.state.record_event(
        ROOMS.discovery, 1, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="deftink"),
    )
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert posted == []
        assert daemon.state.phase == Phase.DISCOVERY
        assert daemon.state.active_team() is None
    finally:
        daemon.state.close()


def test_application_under_twenty_minutes_is_pending(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 19)
    try:
        pending = pending_application(daemon.journal.path)
        assert pending is not None
        assert pending.game_id == "hayes"
        assert not application_should_expire(
            pending,
            now=datetime.now(timezone.utc),
            active_team=None,
            signed_games=set(),
            progressed_games=set(),
        )
    finally:
        daemon.state.close()


def test_stale_application_expires_once(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61)
    daemon.live = True
    daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        daemon._discovery()
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        expiries = [record for record in records if record["event"] == "team_application_expired"]
        assert len(expiries) == 1
        assert expiries[0]["game_id"] == "hayes"
        assert expiries[0]["request_id"] == "application-hayes"
        assert expiries[0]["reason"] == "hard_timeout"
        assert pending_application(daemon.journal.path) is None
    finally:
        daemon.state.close()


def test_soft_timeout_moves_to_one_better_candidate(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 21)
    daemon.state.record_event(
        ROOMS.discovery, 1, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="deftink"),
    )
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        assert posted[0]["game_id"] == "deftink"
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        expiries = [record for record in records if record["event"] == "team_application_expired"]
        assert len(expiries) == 1
        assert expiries[0]["reason"] == "better_candidate_available"
    finally:
        daemon.state.close()


def test_pending_application_rejects_other_game_roster(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 5)
    _ready_roster(daemon, "deftink")
    daemon.tc = _team_room(daemon)
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert posted == []
        assert daemon.state.active_team() is None
        assert daemon.state.phase == Phase.DISCOVERY
    finally:
        daemon.state.close()


def test_pending_application_allows_matching_safe_roster(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61)
    _ready_roster(daemon, "hayes")
    daemon.tc = _team_room(daemon)
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.roster.v1"
        assert posted[0]["game_id"] == "hayes"
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        assert not any(record["event"] == "team_application_expired" for record in records)
    finally:
        daemon.state.close()


def test_expired_application_roster_is_never_signed(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61)
    daemon.tc = _team_room(daemon)
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    daemon.live = True
    try:
        daemon._discovery()
        _ready_roster(daemon, "hayes")
        daemon._discovery()
        assert posted == []
        assert daemon.state.active_team() is None
    finally:
        daemon.state.close()


@pytest.mark.parametrize("age_minutes", [21, 59])
def test_soft_timeout_without_candidate_keeps_pending(
    tmp_path: Path, age_minutes: int
) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", age_minutes)
    daemon.live = True
    daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        pending = pending_application(daemon.journal.path)
        assert pending is not None and pending.game_id == "hayes"
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        assert not any(record["event"] == "team_application_expired" for record in records)
    finally:
        daemon.state.close()



def test_step1a1_progress_resets_timeout_clock(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61)
    _partial_roster(daemon, "hayes", age_minutes=5)
    daemon.live = True
    daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        pending = pending_application(daemon.journal.path)
        assert pending is not None and pending.game_id == "hayes"
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        assert not any(record["event"] == "team_application_expired" for record in records)
    finally:
        daemon.state.close()


def test_step1a1_old_progress_eventually_hard_expires(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 120)
    _partial_roster(daemon, "hayes", age_minutes=61)
    daemon.live = True
    daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        assert pending_application(daemon.journal.path) is None
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        expiry = next(record for record in records if record["event"] == "team_application_expired")
        assert expiry["reason"] == "hard_timeout"
    finally:
        daemon.state.close()



def test_step1a2_fresh_reinvite_same_game_is_allowed(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61, invite_seq=10)
    daemon.state.reserve_request(
        "application-hayes",
        "application:hayes",
        team_application("hayes", "https://x.com/sarukubt", "application-hayes"),
    )
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []
        assert daemon.state.pending_request("application:hayes") is None

        daemon.state.record_event(
            ROOMS.discovery,
            20,
            1,
            _invite(
                Ed25519PrivateKey.generate(),
                game_id="hayes",
                seq=20,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        daemon._discovery()

        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        assert posted[0]["game_id"] == "hayes"

        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        sent = [
            record for record in records
            if record["event"] == "team_application_sent" and record["game_id"] == "hayes"
        ]
        assert len(sent) == 2
        assert sent[-1]["invite_seq"] == 20
    finally:
        daemon.state.close()


def test_step1a2_same_old_invite_does_not_loop(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61, invite_seq=10)
    daemon.state.record_event(
        ROOMS.discovery,
        10,
        1,
        _invite(
            Ed25519PrivateKey.generate(),
            game_id="hayes",
            seq=10,
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat().replace("+00:00", "Z"),
        ),
    )
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        daemon._discovery()
        assert posted == []

        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        expiries = [record for record in records if record["event"] == "team_application_expired"]
        assert len(expiries) == 1
    finally:
        daemon.state.close()


def test_step1a2_old_roster_is_not_revived_by_reinvite(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61, invite_seq=4)
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []

        _ready_roster(daemon, "hayes", start_seq=1)
        daemon.state.record_event(
            ROOMS.discovery,
            10,
            1,
            _invite(
                Ed25519PrivateKey.generate(),
                game_id="hayes",
                seq=10,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        posted.clear()

        daemon.tc = _team_room(daemon)
        daemon._discovery()

        assert posted == []
        assert daemon.state.active_team() is None
        assert daemon.state.phase == Phase.DISCOVERY
    finally:
        daemon.state.close()


def test_step1a2_new_roster_after_reinvite_can_be_signed(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61, invite_seq=4)
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []

        daemon.state.record_event(
            ROOMS.discovery,
            10,
            1,
            _invite(
                Ed25519PrivateKey.generate(),
                game_id="hayes",
                seq=10,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        posted.clear()

        _ready_roster(daemon, "hayes", start_seq=11)
        daemon.tc = _team_room(daemon)
        daemon._discovery()

        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.roster.v1"
        assert posted[0]["game_id"] == "hayes"
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
    finally:
        daemon.state.close()
