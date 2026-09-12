import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import Config, CONTEST_ID, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.rosters import roster_consensus, signed_roster, team_room_is_open
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import Phase, StateStore


def signed(key: Ed25519PrivateKey, room: str, payload: dict, seq: int) -> dict:
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = json.dumps(payload, separators=(",", ":"))
    stored, signature = signer.sign_room(room, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "sig": signature, "text": stored}


def roster_payload(game_id: str, members: list[str], request_id: str, order: list[str] | None = None) -> dict:
    return {
        "type": "sonnet.roster.v1",
        "contest_id": CONTEST_ID,
        "game_id": game_id,
        "poem_room": ROOMS.team(game_id),
        "room_generation": 3,
        "members": order or members,
        "request_id": request_id,
    }


def roster_fixture():
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    return keys, members


def test_four_member_roster_is_joinable_after_other_three_sign():
    keys, members = roster_fixture()
    records = [signed(key, ROOMS.discovery, roster_payload("four", members, f"r-{i}"), i)
               for i, key in enumerate(keys, 1)]
    result = roster_consensus(records)
    assert len(result) == 1
    assert result[0].roster.members == tuple(members)
    assert result[0].signers == frozenset(members[:-1])


def test_roster_with_only_two_other_signatures_is_not_joinable():
    keys, members = roster_fixture()
    records = [signed(key, ROOMS.discovery, roster_payload("partial", members, f"r-{i}"), i)
               for i, key in enumerate(keys[:2], 1)]
    assert roster_consensus(records) == []


def test_different_game_ids_are_not_same_roster():
    keys, members = roster_fixture()
    records = [
        signed(keys[0], ROOMS.discovery, roster_payload("game-a", members, "r-1"), 1),
        signed(keys[1], ROOMS.discovery, roster_payload("game-b", members, "r-2"), 2),
        signed(keys[2], ROOMS.discovery, roster_payload("game-a", members, "r-3"), 3),
    ]
    assert roster_consensus(records) == []


def test_request_id_is_not_part_of_roster_identity():
    keys, members = roster_fixture()
    records = [signed(key, ROOMS.discovery, roster_payload("same", members, f"unique-{i}"), i)
               for i, key in enumerate(keys, 1)]
    assert len(roster_consensus(records)) == 1


def test_later_withdrawal_removes_prior_roster_consent():
    keys, members = roster_fixture()
    records = [signed(key, ROOMS.discovery, roster_payload("withdrawn", members, f"r-{i}"), i)
               for i, key in enumerate(keys, 1)]
    records.append(signed(keys[2], ROOMS.discovery, {
        "type": "sonnet.withdraw.v1", "contest_id": CONTEST_ID,
        "game_id": "withdrawn", "request_id": "withdraw-1",
    }, 4))
    assert roster_consensus(records) == []


def test_member_order_is_part_of_roster_identity():
    keys, members = roster_fixture()
    first = signed(keys[0], ROOMS.discovery, roster_payload("ordered", members, "r-1"), 1)
    reordered = [members[1], members[0], members[2], members[3]]
    second = signed(keys[1], ROOMS.discovery, roster_payload("ordered", members, "r-2", reordered), 2)
    assert signed_roster(first)[0] != signed_roster(second)[0]
    assert roster_consensus([first, second]) == []


def test_nonmember_signer_and_invalid_signature_do_not_count():
    keys, members = roster_fixture()
    outsider = Ed25519PrivateKey.generate()
    outsider_record = signed(outsider, ROOMS.discovery, roster_payload("bad", members, "r-o"), 1)
    invalid = signed(keys[0], ROOMS.discovery, roster_payload("bad", members, "r-i"), 2)
    invalid["text"] = invalid["text"].replace('"game_id":"bad"', '"game_id":"tampered"')
    assert signed_roster(outsider_record) is None
    assert signed_roster(invalid) is None


def test_roster_without_saruku_is_not_joinable():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    members = [did_of(key) for key in keys]
    records = [signed(key, ROOMS.discovery, roster_payload("other", members, f"r-{i}"), i)
               for i, key in enumerate(keys, 1)]
    assert roster_consensus(records) == []


@pytest.mark.parametrize("member_count", [3, 9])
def test_roster_member_count_outside_four_to_eight_is_invalid(member_count):
    keys = [Ed25519PrivateKey.generate() for _ in range(member_count)]
    members = [did_of(key) for key in keys]
    record = signed(keys[0], ROOMS.discovery, roster_payload("size", members, "r"), 1)
    assert signed_roster(record) is None


def test_duplicate_member_is_invalid():
    keys, members = roster_fixture()
    members[-1] = members[0]
    record = signed(keys[0], ROOMS.discovery, roster_payload("dupe", members, "r"), 1)
    assert signed_roster(record) is None


def test_team_room_owner_and_generation_must_match():
    _, members = roster_fixture()
    key = Ed25519PrivateKey.generate()
    members[0] = did_of(key)
    roster = signed_roster(signed(key, ROOMS.discovery, roster_payload("checks", members, "r"), 1))[0]
    referee = did_of(Ed25519PrivateKey.generate())
    assert team_room_is_open(roster, referee, referee, 3, [])
    assert not team_room_is_open(roster, referee, did_of(Ed25519PrivateKey.generate()), 3, [])
    assert not team_room_is_open(roster, referee, referee, 4, [])


def test_accepted_word_freezes_roster():
    key, *rest = [Ed25519PrivateKey.generate() for _ in range(4)]
    members = [did_of(key)] + [did_of(item) for item in rest[:2]] + [SARUKU_DID]
    roster = signed_roster(signed(key, ROOMS.discovery, roster_payload("frozen", members, "r"), 1))[0]
    referee_key = Ed25519PrivateKey.generate()
    referee = did_of(referee_key)
    receipt = signed(referee_key, roster.poem_room, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID, "request_id": "word-1",
        "sender_did": members[0], "status": "accepted", "version": 1, "state_hash": "hash",
    }, 1)
    assert not team_room_is_open(roster, referee, referee, 3, [receipt])


def daemon_fixture(tmp_path: Path, referee: str) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config("https://example.test", None, None, None, None, tmp_path, "commit",
                        state_db=tmp_path / "state.db")
    daemon.state = StateStore(tmp_path / "state.db")
    daemon.state.set("referee_did", referee)
    daemon._read = lambda room: []
    return daemon


def pending_roster(daemon: Daemon, members: list[str], request_id: str = "roster-own") -> dict:
    payload = roster_payload("ours", members, request_id)
    daemon.state.reserve_request(request_id, "roster", payload)
    daemon.state.set("active_team", "ours")
    daemon.state.phase = Phase.WAIT_ROSTER_READY
    return payload


def add_generic_receipt(daemon: Daemon, referee_key: Ed25519PrivateKey, request_id: str,
                        roster_ready: bool, seq: int = 1) -> None:
    raw = signed(referee_key, ROOMS.discovery, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID, "request_id": request_id,
        "sender_did": SARUKU_DID, "status": "accepted", "roster_ready": roster_ready,
        "state_hash": "initial-state",
    }, seq)
    daemon.state.record_event(ROOMS.discovery, seq, 1, raw)


def test_own_generic_roster_receipt_false_does_not_enter_writing(tmp_path: Path):
    referee_key = Ed25519PrivateKey.generate()
    daemon = daemon_fixture(tmp_path, did_of(referee_key))
    _, members = roster_fixture()
    pending_roster(daemon, members)
    add_generic_receipt(daemon, referee_key, "roster-own", False)
    try:
        daemon._receipts(ROOMS.discovery)
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        assert daemon.state.get("roster_consent_accepted") is True
    finally:
        daemon.state.close()


def test_own_generic_roster_ready_uses_pending_context(tmp_path: Path):
    referee_key = Ed25519PrivateKey.generate()
    daemon = daemon_fixture(tmp_path, did_of(referee_key))
    _, members = roster_fixture()
    payload = pending_roster(daemon, members)
    add_generic_receipt(daemon, referee_key, "roster-own", True)
    try:
        daemon._receipts(ROOMS.discovery)
        assert daemon.state.phase == Phase.WRITING
        assert daemon.state.get("current_roster") == payload["members"]
        assert daemon.state.get("poem_room") == payload["poem_room"]
        assert daemon.state.get("room_generation") == payload["room_generation"]
        assert daemon.state.get("poem_state_hash") == "initial-state"
    finally:
        daemon.state.close()


def test_unrelated_generic_receipt_never_calls_llm(monkeypatch, tmp_path: Path):
    referee_key = Ed25519PrivateKey.generate()
    daemon = daemon_fixture(tmp_path, did_of(referee_key))
    raw = signed(referee_key, ROOMS.discovery, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID, "request_id": "someone-else",
        "sender_did": "did:key:zOther", "status": "accepted", "roster_ready": True,
        "state_hash": "hash",
    }, 1)
    daemon.state.record_event(ROOMS.discovery, 1, 1, raw)
    monkeypatch.setattr("sonnet_chain.daemon.LLMClient.structured",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM called")))
    try:
        daemon._receipts(ROOMS.discovery)
    finally:
        daemon.state.close()


def test_discovery_advertisement_posts_once_and_stays_in_discovery(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    daemon.live = True
    daemon.state.phase = Phase.DISCOVERY
    daemon.state.set("registered", True)
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")
    posted = []
    daemon._post = lambda room, payload: posted.append((room, payload))
    try:
        daemon._discovery()
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0][0] == ROOMS.discovery
        assert posted[0][1]["type"] == "sonnet.note.v1"
        assert "text" in posted[0][1] and "message" not in posted[0][1]
        assert daemon.state.get("discovery_advertised") is True
        assert daemon.state.phase == Phase.DISCOVERY
    finally:
        daemon.state.close()


def test_ambiguous_advertisement_attempt_is_not_reposted(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    daemon.live = True
    daemon.state.phase = Phase.DISCOVERY
    daemon.state.set("registered", True)
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")
    calls = 0

    def fail_once(room, payload):
        nonlocal calls
        calls += 1
        raise RuntimeError("ambiguous transport failure")

    daemon._post = fail_once
    try:
        with pytest.raises(RuntimeError, match="ambiguous"):
            daemon._discovery()
        daemon._discovery()
        assert calls == 1
        assert daemon.state.get("discovery_advertised") is False
        assert "refusing a duplicate" in daemon.state.get("last_error")
    finally:
        daemon.state.close()


def test_discovery_dry_run_selects_only_fully_gated_roster(tmp_path: Path):
    referee_key = Ed25519PrivateKey.generate()
    referee = did_of(referee_key)
    daemon = daemon_fixture(tmp_path, referee)
    daemon.live = False
    daemon.state.phase = Phase.DISCOVERY
    daemon.state.set("registered", True)
    daemon.state.set("discovery_advertised", True)
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")
    keys, members = roster_fixture()
    for i, key in enumerate(keys, 1):
        raw = signed(key, ROOMS.discovery, roster_payload("gated", members, f"r-{i}"), i)
        daemon.state.record_event(ROOMS.discovery, i, 1, raw)

    class TeamRoom:
        def owner_note(self, room):
            return referee

        def read_page(self, room, since, wait):
            return [], 3

    daemon.tc = TeamRoom()
    try:
        daemon._discovery()
        action = daemon.state.get("dry_run_action")
        assert action["type"] == "sonnet.roster.v1"
        assert action["game_id"] == "gated"
        assert action["members"] == members
        assert daemon.state.active_team() is None
    finally:
        daemon.state.close()



def _set_b1_clock(
    daemon: Daemon,
    *,
    minutes_ago: int,
    signers: list[str],
) -> None:
    from datetime import datetime, timedelta, timezone

    stamp = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    daemon.state.set("roster_wait_started_at", stamp)
    daemon.state.set("roster_wait_last_progress_at", stamp)
    daemon.state.set("roster_wait_signers", signers)
    daemon.state.set("roster_wait_soft_timeout_noted", False)


def _open_team_room(daemon: Daemon, records=None):
    referee = daemon.state.get("referee_did")
    records = list(records or [])

    class TeamRoom:
        def owner_note(self, room):
            return referee

        def read_page(self, room, since, wait):
            return records, 3

    return TeamRoom()


def test_b1_recent_incomplete_roster_keeps_waiting(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    keys, members = roster_fixture()
    payload = pending_roster(daemon, members)
    daemon.state.set("team_setup", {
        "game_id": payload["game_id"],
        "poem_room": payload["poem_room"],
        "room_generation": payload["room_generation"],
        "members": payload["members"],
    })
    raw = signed(
        keys[0],
        ROOMS.discovery,
        roster_payload("ours", members, "r-1"),
        1,
    )
    daemon.state.record_event(ROOMS.discovery, 1, 1, raw)
    _set_b1_clock(
        daemon,
        minutes_ago=5,
        signers=[SARUKU_DID, members[0]],
    )
    daemon.tc = _open_team_room(daemon)
    daemon.live = True
    posted = []
    daemon._post = lambda room, body: posted.append(body)
    try:
        daemon._wait_roster_ready()
        assert posted == []
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        assert daemon.state.active_team() == "ours"
    finally:
        daemon.state.close()


def test_b1_new_signature_resets_wait_clock(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    keys, members = roster_fixture()
    payload = pending_roster(daemon, members)
    daemon.state.set("team_setup", {
        "game_id": payload["game_id"],
        "poem_room": payload["poem_room"],
        "room_generation": payload["room_generation"],
        "members": payload["members"],
    })
    for seq, key in enumerate(keys[:2], 1):
        raw = signed(
            key,
            ROOMS.discovery,
            roster_payload("ours", members, f"r-{seq}"),
            seq,
        )
        daemon.state.record_event(ROOMS.discovery, seq, 1, raw)
    _set_b1_clock(
        daemon,
        minutes_ago=61,
        signers=[SARUKU_DID, members[0]],
    )
    daemon.tc = _open_team_room(daemon)
    daemon.live = True
    posted = []
    daemon._post = lambda room, body: posted.append(body)
    try:
        daemon._wait_roster_ready()
        assert posted == []
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        assert members[1] in daemon.state.get("roster_wait_signers")
        progress_at = daemon.state.get("roster_wait_last_progress_at")
        assert isinstance(progress_at, str)
    finally:
        daemon.state.close()


def test_b1_fully_signed_roster_never_auto_withdraws(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    keys, members = roster_fixture()
    payload = pending_roster(daemon, members)
    daemon.state.set("team_setup", {
        "game_id": payload["game_id"],
        "poem_room": payload["poem_room"],
        "room_generation": payload["room_generation"],
        "members": payload["members"],
    })
    for seq, key in enumerate(keys, 1):
        raw = signed(
            key,
            ROOMS.discovery,
            roster_payload("ours", members, f"r-{seq}"),
            seq,
        )
        daemon.state.record_event(ROOMS.discovery, seq, 1, raw)
    _set_b1_clock(
        daemon,
        minutes_ago=120,
        signers=[SARUKU_DID] + members[:-1],
    )
    daemon.tc = _open_team_room(daemon)
    daemon.live = True
    posted = []
    daemon._post = lambda room, body: posted.append(body)
    try:
        daemon._wait_roster_ready()
        assert posted == []
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        assert daemon.state.active_team() == "ours"
    finally:
        daemon.state.close()


def test_b1_stale_incomplete_roster_auto_withdraws(tmp_path: Path):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    keys, members = roster_fixture()
    payload = pending_roster(daemon, members)
    daemon.state.set("team_setup", {
        "game_id": payload["game_id"],
        "poem_room": payload["poem_room"],
        "room_generation": payload["room_generation"],
        "members": payload["members"],
    })
    raw = signed(
        keys[0],
        ROOMS.discovery,
        roster_payload("ours", members, "r-1"),
        1,
    )
    daemon.state.record_event(ROOMS.discovery, 1, 1, raw)
    _set_b1_clock(
        daemon,
        minutes_ago=61,
        signers=[SARUKU_DID, members[0]],
    )
    daemon.tc = _open_team_room(daemon)
    daemon.live = True
    posted = []
    daemon._post = lambda room, body: posted.append(body)
    try:
        daemon._wait_roster_ready()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.withdraw.v1"
        assert posted[0]["game_id"] == "ours"
        assert daemon.state.pending_request("roster") is None
        assert daemon.state.active_team() is None
        assert daemon.state.phase == Phase.DISCOVERY
    finally:
        daemon.state.close()


def test_b1_frozen_team_room_refuses_auto_withdraw(tmp_path: Path):
    referee_key = Ed25519PrivateKey.generate()
    referee = did_of(referee_key)
    daemon = daemon_fixture(tmp_path, referee)
    keys, members = roster_fixture()
    payload = pending_roster(daemon, members)
    daemon.state.set("team_setup", {
        "game_id": payload["game_id"],
        "poem_room": payload["poem_room"],
        "room_generation": payload["room_generation"],
        "members": payload["members"],
    })
    raw = signed(
        keys[0],
        ROOMS.discovery,
        roster_payload("ours", members, "r-1"),
        1,
    )
    daemon.state.record_event(ROOMS.discovery, 1, 1, raw)
    _set_b1_clock(
        daemon,
        minutes_ago=61,
        signers=[SARUKU_DID, members[0]],
    )

    accepted_word = signed(
        referee_key,
        ROOMS.team("ours"),
        {
            "type": "sonnet.receipt.v1",
            "contest_id": CONTEST_ID,
            "request_id": "word-1",
            "sender_did": members[0],
            "status": "accepted",
            "version": 1,
            "state_hash": "frozen",
        },
        1,
    )
    daemon.tc = _open_team_room(daemon, [accepted_word])
    daemon.live = True
    posted = []
    daemon._post = lambda room, body: posted.append(body)
    try:
        daemon._wait_roster_ready()
        assert posted == []
        assert daemon.state.phase == Phase.WAIT_ROSTER_READY
        assert daemon.state.active_team() == "ours"
        assert "refusing automatic withdrawal" in daemon.state.get("last_error")
    finally:
        daemon.state.close()



def test_b2_inviter_signature_is_enough_for_anchored_consensus():
    keys, members = roster_fixture()
    inviter = did_of(keys[0])
    record = signed(
        keys[0],
        ROOMS.discovery,
        roster_payload("anchored", members, "r-1"),
        1,
    )

    result = roster_consensus(
        [record],
        anchor_signer=inviter,
    )

    assert len(result) == 1
    assert result[0].roster.members == tuple(members)
    assert result[0].signers == frozenset({inviter})


def test_b2_non_inviter_signature_is_not_enough_for_anchored_consensus():
    keys, members = roster_fixture()
    inviter = did_of(keys[0])
    other_record = signed(
        keys[1],
        ROOMS.discovery,
        roster_payload("anchored", members, "r-2"),
        2,
    )

    assert roster_consensus(
        [other_record],
        anchor_signer=inviter,
    ) == []


def test_b2_inviter_withdrawal_removes_anchor_readiness():
    keys, members = roster_fixture()
    inviter = did_of(keys[0])
    records = [
        signed(
            keys[0],
            ROOMS.discovery,
            roster_payload("anchored", members, "r-1"),
            1,
        ),
        signed(
            keys[0],
            ROOMS.discovery,
            {
                "type": "sonnet.withdraw.v1",
                "contest_id": CONTEST_ID,
                "game_id": "anchored",
                "request_id": "withdraw-1",
            },
            2,
        ),
    ]

    assert roster_consensus(
        records,
        anchor_signer=inviter,
    ) == []


def test_b2_daemon_countersigns_when_inviter_signs_exact_roster(
    tmp_path: Path,
):
    referee = did_of(Ed25519PrivateKey.generate())
    daemon = daemon_fixture(tmp_path, referee)
    daemon.live = False
    daemon.state.phase = Phase.DISCOVERY
    daemon.state.set("registered", True)
    daemon.state.set("discovery_advertised", True)
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")

    keys, members = roster_fixture()
    inviter = did_of(keys[0])

    daemon.journal = __import__(
        "sonnet_chain.journal",
        fromlist=["Journal"],
    ).Journal(tmp_path / "journal" / "sonnet.jsonl")
    daemon.journal.append(
        "team_application_sent",
        game_id="anchored",
        target_from_did=inviter,
        request_id="application-anchored",
        invite_seq=1,
    )

    # The inviter signs after the invite/application epoch began.
    raw = signed(
        keys[0],
        ROOMS.discovery,
        roster_payload("anchored", members, "r-inviter"),
        2,
    )
    daemon.state.record_event(ROOMS.discovery, 2, 1, raw)

    class TeamRoom:
        def owner_note(self, room):
            return referee

        def read_page(self, room, since, wait):
            return [], 3

    daemon.tc = TeamRoom()

    try:
        daemon._discovery()
        action = daemon.state.get("dry_run_action")
        assert action["type"] == "sonnet.roster.v1"
        assert action["game_id"] == "anchored"
        assert action["members"] == members
    finally:
        daemon.state.close()



def test_b2_anchor_signature_must_be_after_invite_epoch():
    keys, members = roster_fixture()
    inviter = did_of(keys[0])

    records = [
        signed(
            keys[0],
            ROOMS.discovery,
            roster_payload("epoch", members, "r-old-anchor"),
            5,
        ),
        signed(
            keys[1],
            ROOMS.discovery,
            roster_payload("epoch", members, "r-new-other"),
            11,
        ),
    ]

    assert roster_consensus(
        records,
        anchor_signer=inviter,
        min_anchor_seq=10,
    ) == []

    records.append(
        signed(
            keys[0],
            ROOMS.discovery,
            roster_payload("epoch", members, "r-new-anchor"),
            12,
        )
    )
    result = roster_consensus(
        records,
        anchor_signer=inviter,
        min_anchor_seq=10,
    )
    assert len(result) == 1
    assert inviter in result[0].signers
