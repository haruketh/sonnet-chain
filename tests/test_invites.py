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
from sonnet_chain.rosters import CanonicalRoster
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import Phase, StateStore
from sonnet_chain.team_formation import (
    ApplicationDelivery, FormationOpportunity, StructuralKey, TeamFormationStore,
    roster_fingerprint, start_epoch,
)


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
    record["created_at"] = created_at or datetime.now(timezone.utc).isoformat()
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
    # Legacy flow fixtures treat their generated senders as referee-accepted
    # writers; v0.2 production code obtains this set from registration facts.
    daemon._trusted_writer_dids = lambda: {
        item.get("from") for item in daemon.state.events(ROOMS.discovery)
        if isinstance(item.get("from"), str)
    }
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
    payload = team_application(
        game_id, "https://x.com/sarukubt", f"application-{game_id}"
    )
    daemon.state.reserve_request(
        f"application-{game_id}", f"application:{game_id}", payload
    )
    sent_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    epoch = start_epoch(
        FormationOpportunity(
            game_id, "did:key:zLead", invite_seq or 0, sent_at
        ), f"application-{game_id}", sent_at,
    )
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = sent_at.isoformat()
    epoch.starting_invite_seq = invite_seq
    TeamFormationStore(daemon.state).save_epoch(epoch)


def _ready_roster(
    daemon: Daemon,
    game_id: str,
    start_seq: int = 1,
    first_key: Ed25519PrivateKey | None = None,
) -> None:
    keys = [first_key or Ed25519PrivateKey.generate()] + [
        Ed25519PrivateKey.generate() for _ in range(2)
    ]
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


def test_detects_signed_direct_invite_for_saruku() -> None:
    key = Ed25519PrivateKey.generate()

    invite = direct_invite(_invite(key), trusted_writer_dids={did_of(key)})

    assert invite is not None
    assert invite.game_id == "deftink"
    assert invite.from_did == did_of(key)


@pytest.mark.parametrize("kind", ["sonnet.invite.v1", "sonnet.invite.v2"])
def test_vote_or_undefined_invite_is_ignored_for_team_formation(kind: str) -> None:
    key = Ed25519PrivateKey.generate()
    assert direct_invite(_invite(key, kind=kind), trusted_writer_dids={did_of(key)}) is None


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


def test_prior_journal_application_without_durable_evidence_does_not_block(tmp_path: Path) -> None:
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
        assert len(posted) == 1
        assert posted[0]["game_id"] == "deftink"
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
                created_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
    )
    daemon.state.record_event(
        ROOMS.discovery, 2, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="newer", seq=2,
                created_at=datetime.now(timezone.utc).isoformat()),
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
                created_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), with_room=True),
    )
    daemon.state.record_event(
        ROOMS.discovery, 2, 1,
        _invite(Ed25519PrivateKey.generate(), game_id="newer", seq=2,
                created_at=datetime.now(timezone.utc).isoformat()),
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


def test_journal_only_application_is_not_safety_authority(tmp_path: Path) -> None:
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
        assert len(posted) == 1
        assert posted[0]["game_id"] == "deftink"
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


def test_soft_timeout_does_not_switch_merely_for_new_candidate(tmp_path: Path) -> None:
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
        assert posted == []
        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        expiries = [record for record in records if record["event"] == "team_application_expired"]
        assert expiries == []
        assert pending_application(daemon.journal.path).game_id == "hayes"
    finally:
        daemon.state.close()


def test_soft_stall_switches_to_cross_game_structurally_stronger_roster(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    now = datetime.now(timezone.utc)
    current_inviter = Ed25519PrivateKey.generate()
    epoch = start_epoch(
        FormationOpportunity("current", did_of(current_inviter), 1, now),
        "application-current", now - timedelta(minutes=21),
    )
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(minutes=21)).isoformat()
    TeamFormationStore(daemon.state).save_epoch(epoch)

    lead = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    fourth = Ed25519PrivateKey.generate()
    members = [did_of(lead), did_of(other), did_of(fourth), SARUKU_DID]
    daemon.state.record_event(ROOMS.discovery, 1, 1, _invite(lead, game_id="better", seq=1))
    proposal = {
        "type": "sonnet.roster.v1", "contest_id": CONTEST_ID,
        "game_id": "better", "poem_room": ROOMS.team("better"),
        "room_generation": 3, "members": members, "request_id": "better-r",
    }
    daemon.state.record_event(ROOMS.discovery, 2, 1, _signed(lead, proposal, 2))
    daemon.state.record_event(ROOMS.discovery, 3, 1, _signed(other, proposal, 3))
    daemon.tc = _team_room(daemon)
    posted = []; daemon._post = lambda room, payload: posted.append(payload); daemon.live = True
    try:
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        assert posted[0]["game_id"] == "better"
    finally:
        daemon.state.close()


def test_soft_stall_current_three_of_four_beats_fresh_note(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    now = datetime.now(timezone.utc)
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    epoch = start_epoch(
        FormationOpportunity("current", did_of(keys[0]), 1, now),
        "application-current", now - timedelta(minutes=21),
    )
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(minutes=21)).isoformat()
    TeamFormationStore(daemon.state).save_epoch(epoch)
    proposal = {
        "type": "sonnet.roster.v1", "contest_id": CONTEST_ID,
        "game_id": "current", "poem_room": ROOMS.team("current"),
        "room_generation": 3, "members": members, "request_id": "current-r",
    }
    for seq, key in enumerate(keys, 2):
        daemon.state.record_event(ROOMS.discovery, seq, 1, _signed(key, proposal, seq))
    challenger = Ed25519PrivateKey.generate()
    daemon.state.record_event(ROOMS.discovery, 9, 1, _invite(challenger, game_id="fresh", seq=9))
    daemon.tc = _team_room(daemon)
    posted = []; daemon._post = lambda room, payload: posted.append(payload); daemon.live = True
    try:
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.roster.v1"
        assert posted[0]["game_id"] == "current"
        assert TeamFormationStore(daemon.state).active_epoch().game_id == "current"
    finally:
        daemon.state.close()


def test_daemon_starts_one_fixed_replacement_grace(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path); now = datetime.now(timezone.utc)
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    old = CanonicalRoster("current", ROOMS.team("current"), 3, tuple(members))
    epoch = start_epoch(FormationOpportunity("current", did_of(keys[0]), 1, now),
                        "application-current", now - timedelta(minutes=61))
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(minutes=61)).isoformat()
    epoch.active_roster_fingerprint = roster_fingerprint(old)
    epoch.epoch_high_watermark_key = StructuralKey(4, True, True, True, 1, 3).comparison()
    epoch.epoch_high_watermark_at = (now - timedelta(minutes=61)).isoformat()
    TeamFormationStore(daemon.state).save_epoch(epoch)
    replacement_members = [did_of(key) for key in keys[::-1]] + [SARUKU_DID]
    proposal = {"type": "sonnet.roster.v1", "contest_id": CONTEST_ID,
                "game_id": "current", "poem_room": ROOMS.team("current"),
                "room_generation": 3, "members": replacement_members, "request_id": "replacement"}
    record = _signed(keys[0], proposal, 10)
    record["created_at"] = now.isoformat()
    daemon.state.record_event(ROOMS.discovery, 10, 1, record)
    daemon.tc = _team_room(daemon); daemon.live = True; daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        recovered = TeamFormationStore(daemon.state).active_epoch()
        assert recovered.replacement_recovery_used
        deadline = recovered.replacement_recovery_deadline
        daemon._discovery()
        assert TeamFormationStore(daemon.state).active_epoch().replacement_recovery_deadline == deadline
    finally:
        daemon.state.close()


def test_daemon_fixed_replacement_grace_expiry_hard_stalls(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path); now = datetime.now(timezone.utc)
    inviter = Ed25519PrivateKey.generate()
    epoch = start_epoch(FormationOpportunity("current", did_of(inviter), 1, now),
                        "application-current", now - timedelta(minutes=90))
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(minutes=90)).isoformat()
    epoch.epoch_high_watermark_at = (now - timedelta(minutes=90)).isoformat()
    epoch.replacement_recovery_used = True
    epoch.replacement_recovery_deadline = (now - timedelta(seconds=1)).isoformat()
    TeamFormationStore(daemon.state).save_epoch(epoch)
    daemon.tc = _team_room(daemon); daemon.live = True; daemon._post = lambda room, payload: None
    try:
        daemon._discovery()
        assert TeamFormationStore(daemon.state).active_epoch() is None
    finally:
        daemon.state.close()


def test_trusted_referee_rejection_resolves_application_uncertainty(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path); now = datetime.now(timezone.utc)
    referee_key = Ed25519PrivateKey.generate()
    daemon.state.set("referee_did", did_of(referee_key))
    inviter = Ed25519PrivateKey.generate()
    payload = team_application("current", "https://x.com/sarukubt", "application-current")
    daemon.state.reserve_request("application-current", "application:current", payload)
    daemon.state.persist_protocol_intent(
        "application-current", "application", ROOMS.discovery, payload,
        "POSTED_UNCONFIRMED", game_id="current", created_at=now.isoformat(),
    )
    epoch = start_epoch(FormationOpportunity("current", did_of(inviter), 1, now),
                        "application-current", now)
    TeamFormationStore(daemon.state).save_epoch(epoch)
    rejection = _signed(referee_key, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID,
        "request_id": "application-current", "sender_did": SARUKU_DID,
        "action_type": "sonnet.application.v1", "status": "rejected",
    }, 5)
    daemon.state.record_event(ROOMS.discovery, 5, 1, rejection)
    try:
        daemon._receipts(ROOMS.discovery)
        assert daemon.state.protocol_intent("application-current")["delivery_state"] == "EXPLICITLY_NOT_PERSISTED"
        assert TeamFormationStore(daemon.state).active_epoch() is None
    finally:
        daemon.state.close()


def test_unreconciled_history_hard_stall_abandons_only_local_application(tmp_path: Path) -> None:
    from sonnet_chain.team_formation import FormationHistoryState

    daemon = _daemon(tmp_path); now = datetime.now(timezone.utc)
    inviter = Ed25519PrivateKey.generate()
    epoch = start_epoch(FormationOpportunity("current", did_of(inviter), 1, now),
                        "application-current", now - timedelta(minutes=61))
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(minutes=61)).isoformat()
    TeamFormationStore(daemon.state).save_epoch(epoch)
    daemon.state.record_event(ROOMS.discovery, 1, 1, {"seq": 1, "text": "unknown"})
    daemon.state.record_event(ROOMS.discovery, 3, 1, {"seq": 3, "text": "unknown"})
    history = FormationHistoryState(ROOMS.discovery, 1); history.observe([1, 3])
    TeamFormationStore(daemon.state).save_history(history)
    daemon.tc = type("TC", (), {
        "export_history": lambda self, room: (_ for _ in ()).throw(RuntimeError("unavailable")),
    })()
    daemon.live = True; posted = []; daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []
        assert TeamFormationStore(daemon.state).active_epoch() is None
        events = [json.loads(line)["event"] for line in daemon.journal.path.read_text().splitlines()]
        assert "formation_preconsent_uncertain_abandon" in events
    finally:
        daemon.state.close()


def test_possible_consent_blocks_apply_switch_expiry_and_second_countersign(tmp_path: Path) -> None:
    from sonnet_chain.team_formation import ConsentDelivery

    daemon = _daemon(tmp_path); now = datetime.now(timezone.utc)
    inviter = Ed25519PrivateKey.generate()
    epoch = start_epoch(FormationOpportunity("current", did_of(inviter), 1, now),
                        "application-current", now - timedelta(hours=2))
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = (now - timedelta(hours=2)).isoformat()
    epoch.consent_delivery_state = ConsentDelivery.CONSENT_DELIVERY_UNKNOWN
    epoch.consent_request_id = "roster-current"
    TeamFormationStore(daemon.state).save_epoch(epoch)
    payload = {"type": "sonnet.roster.v1", "contest_id": CONTEST_ID,
               "game_id": "current", "poem_room": ROOMS.team("current"),
               "room_generation": 3, "members": [SARUKU_DID, "did:key:zA", "did:key:zB", "did:key:zC"],
               "request_id": "roster-current"}
    daemon.state.persist_protocol_intent(
        "roster-current", "roster", ROOMS.discovery, payload,
        "CONSENT_DELIVERY_UNKNOWN", game_id="current", created_at=now.isoformat(),
    )
    daemon.state.record_event(ROOMS.discovery, 5, 1,
                              _invite(Ed25519PrivateKey.generate(), game_id="other", seq=5))
    posted = []; daemon._post = lambda room, body: posted.append(body); daemon.live = True
    try:
        daemon._discovery()
        assert posted == []
        assert TeamFormationStore(daemon.state).active_epoch().game_id == "current"
        assert daemon.state.phase == Phase.DISCOVERY
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



def test_unconfirmed_application_is_not_replaced_from_journal_age(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.journal.append(
        "team_application_sent", game_id="hayes", target_from_did="did:key:zLead",
        request_id="application-hayes", invite_seq=10,
    )
    daemon.state.reserve_request(
        "application-hayes",
        "application:hayes",
        team_application("hayes", "https://x.com/sarukubt", "application-hayes"),
    )
    now = datetime.now(timezone.utc)
    pending_epoch = start_epoch(
        FormationOpportunity("hayes", "did:key:zLead", 10, now),
        "application-hayes", now,
    )
    TeamFormationStore(daemon.state).save_epoch(pending_epoch)
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []
        assert daemon.state.pending_request("application:hayes") is not None

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

        assert posted == []

        records = [json.loads(line) for line in daemon.journal.path.read_text().splitlines()]
        sent = [
            record for record in records
            if record["event"] == "team_application_sent" and record["game_id"] == "hayes"
        ]
        assert len(sent) == 1
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


def test_new_roster_waits_until_application_delivery_is_reconciled(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 61, invite_seq=4)
    daemon.live = True
    posted = []
    daemon._post = lambda room, payload: posted.append(payload)
    try:
        daemon._discovery()
        assert posted == []

        fresh_inviter = Ed25519PrivateKey.generate()
        daemon.state.record_event(
            ROOMS.discovery,
            10,
            1,
            _invite(
                fresh_inviter,
                game_id="hayes",
                seq=10,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        daemon._discovery()
        assert len(posted) == 1
        assert posted[0]["type"] == "sonnet.application.v1"
        posted.clear()

        _ready_roster(daemon, "hayes", start_seq=11, first_key=fresh_inviter)
        daemon.tc = _team_room(daemon)
        daemon._discovery()

        assert posted == []
        epoch = TeamFormationStore(daemon.state).active_epoch()
        assert epoch.application_delivery_state == "POSTED_UNCONFIRMED"
        assert daemon.state.phase == Phase.DISCOVERY
    finally:
        daemon.state.close()



def test_b1_withdrawn_roster_epoch_requires_fresh_invite(tmp_path: Path) -> None:
    from sonnet_chain.invites import application_blocks_invite

    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 70, invite_seq=10)
    daemon.journal.append(
        "roster_wait_withdrawn",
        game_id="hayes",
        request_id="withdraw-1",
        minutes_since_progress=61.0,
    )
    old_key = Ed25519PrivateKey.generate()
    old_invite = direct_invite(
        _invite(
            old_key,
            game_id="hayes",
            seq=10,
            created_at=(
                datetime.now(timezone.utc) - timedelta(minutes=80)
            ).isoformat().replace("+00:00", "Z"),
        ), trusted_writer_dids={did_of(old_key)}
    )
    fresh_key = Ed25519PrivateKey.generate()
    fresh_invite = direct_invite(
        _invite(
            fresh_key,
            game_id="hayes",
            seq=20,
            created_at=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        ), trusted_writer_dids={did_of(fresh_key)}
    )
    try:
        assert old_invite is not None
        assert fresh_invite is not None
        assert application_blocks_invite(
            daemon.journal.path, old_invite
        )
        assert not application_blocks_invite(
            daemon.journal.path, fresh_invite
        )
    finally:
        daemon.state.close()


def test_b1_withdrawn_game_blocks_stale_roster_until_new_application(
    tmp_path: Path,
) -> None:
    from sonnet_chain.invites import expired_application_games

    daemon = _daemon(tmp_path)
    _record_application(daemon, "hayes", 70, invite_seq=10)
    daemon.journal.append(
        "roster_wait_withdrawn",
        game_id="hayes",
        request_id="withdraw-1",
        minutes_since_progress=61.0,
    )
    try:
        assert "hayes" in expired_application_games(
            daemon.journal.path
        )

        daemon.journal.append(
            "team_application_sent",
            game_id="hayes",
            target_from_did="did:key:zLead",
            request_id="application-fresh",
            invite_seq=20,
        )
        assert "hayes" not in expired_application_games(
            daemon.journal.path
        )
    finally:
        daemon.state.close()



def test_b2_pending_application_remembers_inviter(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    inviter = did_of(Ed25519PrivateKey.generate())
    daemon.journal.append(
        "team_application_sent",
        game_id="hayes",
        target_from_did=inviter,
        request_id="application-hayes",
        invite_seq=10,
    )
    try:
        pending = pending_application(daemon.journal.path)
        assert pending is not None
        assert pending.game_id == "hayes"
        assert pending.inviter_did == inviter
        assert pending.invite_seq == 10
    finally:
        daemon.state.close()
