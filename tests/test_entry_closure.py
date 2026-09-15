from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.entry_closure import (
    EntryClosureStore, FrozenPoemInput, PeerFinalCoordinator, PublicationDelivery,
    PublicationIntegrity, SubmissionDelivery, assess_publication_integrity,
    frozen_input_from_state, plan_publication, reconcile_frozen_poem,
)
from sonnet_chain.state import StateStore
from sonnet_chain.receipts import receipt_candidate
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import Phase
from sonnet_chain.x_oauth import XIdentity


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
LINES = tuple(f"line {index} word" for index in range(1, 15))


def frozen(*, final=SARUKU_DID, state_hash="state-final", poem_hash=None,
           version=14, ledger=None):
    values = tuple({
        "version": index, "state_hash": state_hash if index == version else f"state-{index}",
        "contributor_did": final if index == version else "did:key:zPeer",
        "complete": index == version, "lines": list(LINES) if index == version else ["partial"],
    } for index in range(1, version + 1)) if ledger is None else tuple(ledger)
    return FrozenPoemInput("game", "d-sonnet-2-team-game", 3, version, state_hash,
                           final, values, poem_hash, "roster-hash")


def ready_store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    store = EntryClosureStore(state)
    source = frozen()
    result = reconcile_frozen_poem(source)
    store.save_reconciliation(source, result, NOW)
    parts = store.prepare_publication(source, result, NOW)
    return state, store, source, result, parts


def confirm_all(store, parts, account="account"):
    for part in parts:
        intent = store.persist_publication_intent(part.publication_id, part.part_index, NOW)
        assert intent is not None
        store.publication_result(part.publication_id, part.part_index,
                                 PublicationDelivery.CONFIRMED, NOW,
                                 post_id=f"post-{part.part_index}", account_id=account,
                                 returned_text=part.exact_content,
                                 returned_parent_post_id=intent["parent_post_id"],
                                 expected_account_id=account)


def signed(key, room, payload, seq):
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = __import__("json").dumps(payload, separators=(",", ":"))
    stored, signature = signer.sign_room(room, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "sig": signature, "text": stored}


def daemon_for(tmp_path, state, referee, *, x_publish_cmd=None):
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config(
        "https://example.test", None, None, None, None, tmp_path, "commit",
        state_db=state.path, x_publish_cmd=x_publish_cmd,
    )
    daemon.state = state
    daemon.live = True
    daemon.wait = 0
    daemon.journal = None
    daemon.state.set("referee_did", referee)
    daemon._read = lambda room: []
    return daemon


def test_frozen_reconciliation_keeps_state_and_poem_hash_independent():
    source = frozen(state_hash="not-a-poem-hash")
    result = reconcile_frozen_poem(source)
    assert result.ok
    assert result.poem_sha256 != source.final_state_hash


@pytest.mark.parametrize("mutation", ["missing", "version", "hash", "contributor"])
def test_frozen_reconciliation_rejects_incomplete_or_conflicting_state(mutation):
    source = frozen()
    ledger = list(source.accepted_word_ledger)
    if mutation == "missing": ledger.pop(3)
    if mutation == "version": ledger[-1] = {**ledger[-1], "version": 99}
    if mutation == "hash": ledger[-1] = {**ledger[-1], "state_hash": "wrong"}
    if mutation == "contributor": ledger[-1] = {**ledger[-1], "contributor_did": "did:key:zOther"}
    assert not reconcile_frozen_poem(frozen(ledger=ledger)).ok


def test_optional_authoritative_poem_hash_only_blocks_when_present_and_wrong():
    result = reconcile_frozen_poem(frozen())
    assert result.ok
    assert reconcile_frozen_poem(frozen(poem_hash=result.poem_sha256)).ok
    assert not reconcile_frozen_poem(frozen(poem_hash="0" * 64)).ok


def test_publication_plan_preserves_line_order_and_attribution_outside_hash():
    source = frozen()
    result = reconcile_frozen_poem(source)
    parts = plan_publication(source, result.canonical_poem, result.poem_sha256, limit=90)
    assert len(parts) > 1
    rendered = "\n".join(part.exact_content for part in parts)
    assert all(rendered.index(line) < rendered.index(LINES[index + 1])
               for index, line in enumerate(LINES[:-1]))
    assert source.final_contributor_did in parts[-1].exact_content


def test_publication_intent_is_durable_before_send_and_parent_is_confirmed(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path)
    first = store.persist_publication_intent(parts[0].publication_id, 0, NOW)
    assert first["state"] == PublicationDelivery.SEND_INTENT_PERSISTED
    if len(parts) > 1:
        assert store.persist_publication_intent(parts[0].publication_id, 1, NOW) is None
    state.close()


def test_publication_ambiguity_survives_restart_and_never_becomes_retryable(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path)
    store.persist_publication_intent(parts[0].publication_id, 0, NOW)
    store.publication_result(parts[0].publication_id, 0,
                             PublicationDelivery.DELIVERY_UNKNOWN, NOW)
    state.close()
    state = StateStore(tmp_path / "state.db"); store = EntryClosureStore(state)
    assert store.parts("game")[0]["state"] == PublicationDelivery.DELIVERY_UNKNOWN
    assert store.persist_publication_intent(parts[0].publication_id, 0, NOW) is None
    state.close()


def test_definitely_not_sent_can_retry_same_frozen_part(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path)
    store.persist_publication_intent(parts[0].publication_id, 0, NOW)
    store.publication_result(parts[0].publication_id, 0,
                             PublicationDelivery.DEFINITELY_NOT_SENT, NOW)
    retry = store.persist_publication_intent(parts[0].publication_id, 0, NOW + timedelta(seconds=1))
    assert retry["content_sha256"] == parts[0].content_sha256
    state.close()


def test_unknown_exact_match_recovers_but_absence_remains_unknown(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path)
    part = parts[0]
    store.persist_publication_intent(part.publication_id, 0, NOW)
    store.publication_result(part.publication_id, 0, PublicationDelivery.DELIVERY_UNKNOWN, NOW)
    assert not store.recover_unknown(part.publication_id, 0, [], "account", NOW)
    assert store.parts("game")[0]["state"] == PublicationDelivery.DELIVERY_UNKNOWN
    assert store.recover_unknown(part.publication_id, 0, [{
        "id": "recovered", "author_id": "account", "text": part.exact_content,
        "parent_post_id": None, "created_at": NOW.isoformat(),
    }], "account", NOW)
    assert store.parts("game")[0]["x_post_id"] == "recovered"
    state.close()


@pytest.mark.parametrize("integrity,strong,allowed", [
    (PublicationIntegrity.MATCH, True, True),
    (PublicationIntegrity.CONFLICT, True, False),
    (PublicationIntegrity.UNRESOLVED, True, True),
    (PublicationIntegrity.UNRESOLVED, False, False),
])
def test_submission_integrity_gate(tmp_path, integrity, strong, allowed):
    state, store, source, result, parts = ready_store(tmp_path)
    confirm_all(store, parts)
    if not strong:
        with state.db:
            state.db.execute("UPDATE publication_parts SET strong_confirmation=0")
    packet = store.prepare_submission(source, result.poem_sha256, integrity, NOW,
                                      NOW + timedelta(hours=1), "submit-fixed")
    assert (packet is not None) is allowed
    state.close()


def test_returned_text_mismatch_is_not_prior_strong_confirmation(tmp_path):
    state, store, source, result, parts = ready_store(tmp_path)
    for part in parts:
        assert store.persist_publication_intent(part.publication_id, part.part_index, NOW)
        store.publication_result(
            part.publication_id, part.part_index, PublicationDelivery.CONFIRMED, NOW,
            post_id=f"post-{part.part_index}", account_id="account",
            returned_text="different X response text",
            returned_parent_post_id=store.parts("game")[part.part_index]["parent_post_id"],
            expected_account_id="account",
        )
    assert not store.strong_confirmation("game")
    assert store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.UNRESOLVED,
        NOW, NOW + timedelta(hours=1), "submit-fixed",
    ) is None
    state.close()


@pytest.mark.parametrize("account,parent", [
    ("wrong-account", None),
    ("account", "wrong-parent"),
])
def test_wrong_author_or_parent_is_not_prior_strong_confirmation(tmp_path, account, parent):
    state, store, source, result, parts = ready_store(tmp_path)
    part = parts[0]
    assert store.persist_publication_intent(part.publication_id, 0, NOW)
    store.publication_result(
        part.publication_id, 0, PublicationDelivery.CONFIRMED, NOW,
        post_id="post-1", account_id=account, returned_text=part.exact_content,
        returned_parent_post_id=parent, expected_account_id="account",
    )
    assert not store.strong_confirmation("game")
    assert store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.UNRESOLVED,
        NOW, NOW + timedelta(hours=1), "submit-fixed",
    ) is None
    state.close()


def test_peer_final_cannot_prepare_publication_or_submission(tmp_path):
    state = StateStore(tmp_path / "state.db")
    store = EntryClosureStore(state)
    source = frozen(final="did:key:zPeerFinal")
    result = reconcile_frozen_poem(source)
    store.save_reconciliation(source, result, NOW)
    assert store.prepare_publication(source, result, NOW) == []
    assert store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.MATCH,
        NOW, NOW + timedelta(hours=1), "forbidden",
    ) is None
    state.close()


def test_publication_integrity_match_conflict_and_unresolved(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path); confirm_all(store, parts)
    expected = store.parts("game")
    observed = [{
        "id": row["x_post_id"], "author_id": "account", "text": row["exact_content"],
        "parent_post_id": row["parent_post_id"], "created_at": NOW.isoformat(),
        "edit_history_tweet_ids": [row["x_post_id"]],
    } for row in expected]
    assert assess_publication_integrity(expected, observed, "account", NOW + timedelta(hours=1)) == (
        PublicationIntegrity.MATCH
    )
    assert assess_publication_integrity(expected, None, "account", NOW + timedelta(hours=1)) == (
        PublicationIntegrity.UNRESOLVED
    )
    observed[0]["text"] = "edited"
    assert assess_publication_integrity(expected, observed, "account", NOW + timedelta(hours=1)) == (
        PublicationIntegrity.CONFLICT
    )
    state.close()


def test_partial_x_lookup_distinguishes_definitive_and_unknown_missing_ids(tmp_path):
    state, store, _, _, parts = ready_store(tmp_path)
    confirm_all(store, parts)
    expected = store.parts("game")
    missing_id = expected[0]["x_post_id"]
    deadline = NOW + timedelta(hours=1)
    definitive = [{
        "resource_id": missing_id,
        "type": "https://api.twitter.com/2/problems/resource-not-found",
        "title": "Not Found",
    }]
    temporary = [{
        "resource_id": missing_id,
        "type": "https://api.twitter.com/2/problems/service-unavailable",
        "title": "Service Unavailable",
    }]
    assert assess_publication_integrity(
        expected, [], "account", deadline, definitive,
    ) == PublicationIntegrity.CONFLICT
    assert assess_publication_integrity(
        expected, [], "account", deadline, temporary,
    ) == PublicationIntegrity.UNRESOLVED
    assert assess_publication_integrity(
        expected, [], "account", deadline, [],
    ) == PublicationIntegrity.UNRESOLVED
    state.close()


@pytest.mark.parametrize("unresolved_first", [True, False])
def test_known_conflict_wins_over_unresolved_part_regardless_of_order(unresolved_first):
    unresolved_part = {
        "x_post_id": "missing", "exact_content": "expected missing",
        "parent_post_id": None,
    }
    conflict_part = {
        "x_post_id": "present", "exact_content": "expected present",
        "parent_post_id": "parent",
    }
    expected = (
        [unresolved_part, conflict_part]
        if unresolved_first else [conflict_part, unresolved_part]
    )
    observed = [{
        "id": "present", "author_id": "wrong-account", "text": "wrong text",
        "parent_post_id": "wrong-parent", "created_at": NOW.isoformat(),
        "edit_history_tweet_ids": ["edited-id"],
    }]
    errors = [{
        "resource_id": "missing",
        "type": "https://api.twitter.com/2/problems/service-unavailable",
        "title": "Service Unavailable",
    }]
    assert assess_publication_integrity(
        expected, observed, "account", NOW + timedelta(hours=1), errors,
    ) == PublicationIntegrity.CONFLICT


def test_submission_packet_is_exact_durable_and_reused_after_deadline(tmp_path):
    state, store, source, result, parts = ready_store(tmp_path); confirm_all(store, parts)
    packet = store.prepare_submission(source, result.poem_sha256, PublicationIntegrity.MATCH,
                                      NOW, NOW + timedelta(minutes=1), "submit-fixed")
    row = state.db.execute("SELECT * FROM submission_intents").fetchone()
    assert row["request_id"] == "submit-fixed"
    assert row["packet_sha256"] == hashlib.sha256(row["packet_json"].encode()).hexdigest()
    store.mark_submission_unknown("submit-fixed", NOW + timedelta(minutes=2))
    same = store.prepare_submission(source, result.poem_sha256, PublicationIntegrity.MATCH,
                                    NOW + timedelta(minutes=3), NOW + timedelta(minutes=1))
    assert same == packet
    state.close()


def test_submission_retry_schedule_is_bounded_capped_and_restart_safe(tmp_path):
    state, store, source, result, parts = ready_store(tmp_path)
    confirm_all(store, parts)
    store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.MATCH,
        NOW, NOW + timedelta(minutes=1), "submit-fixed",
    )
    store.mark_submission_attempt("submit-fixed", NOW)
    first = store.pending_submission("game")
    assert first["attempt_count"] == 1
    assert datetime.fromisoformat(first["next_retry_at"]) == NOW + timedelta(seconds=30)
    assert store.submission_retry_due("game", NOW + timedelta(seconds=29)) is None
    assert store.submission_retry_due("game", NOW + timedelta(seconds=30)) is not None
    state.close()

    state = StateStore(tmp_path / "state.db")
    store = EntryClosureStore(state)
    persisted = store.pending_submission("game")
    assert persisted["attempt_count"] == 1
    assert datetime.fromisoformat(persisted["next_retry_at"]) == NOW + timedelta(seconds=30)
    at = NOW + timedelta(seconds=30)
    expected_delays = [60, 120, 240, 300, 300]
    for delay in expected_delays:
        store.mark_submission_attempt("submit-fixed", at)
        row = store.pending_submission("game")
        assert datetime.fromisoformat(row["next_retry_at"]) == at + timedelta(seconds=delay)
        at += timedelta(seconds=delay)
    assert store.pending_submission("game")["attempt_count"] == store.MAX_SUBMISSION_ATTEMPTS
    assert store.submission_retry_due("game", at + timedelta(days=1)) is None
    # Deadline crossing does not erase the same-ID reconciliation record.
    assert store.pending_submission("game")["request_id"] == "submit-fixed"
    state.close()


def test_submission_acceptance_releases_only_matching_game_and_replay_is_idempotent(tmp_path):
    state, store, source, result, parts = ready_store(tmp_path); confirm_all(store, parts)
    store.prepare_submission(source, result.poem_sha256, PublicationIntegrity.MATCH,
                             NOW, NOW + timedelta(hours=1), "submit-fixed")
    with state.db:
        state.db.execute("INSERT INTO participant_commitments(contest_id,game_id,state,updated_at) "
                         "VALUES('sonnet-2','other','ACTIVE',?)", (NOW.isoformat(),))
    assert store.accept_submission("game", "submit-fixed", "entry-1", 44,
                                   "did:key:zReferee", NOW)
    assert store.accept_submission("game", "submit-fixed", "entry-1", 44,
                                   "did:key:zReferee", NOW)
    assert store.commitment("game")["state"] == "RELEASED"
    assert store.commitment("other")["state"] == "ACTIVE"
    state.close()


def test_peer_final_never_creates_publication_and_deadline_is_reconciling(tmp_path):
    state = StateStore(tmp_path / "state.db"); store = EntryClosureStore(state)
    source = frozen(final="did:key:zPeerFinal")
    coordinator = PeerFinalCoordinator(store); coordinator.observe(source, NOW)
    assert store.parts("game") == []
    assert coordinator.reminder("game", NOW + timedelta(minutes=19), NOW + timedelta(hours=1)) is None
    assert coordinator.reminder("game", NOW + timedelta(minutes=21), NOW + timedelta(hours=1))
    assert not coordinator.meaningful_progress("game", "team_chatter", NOW + timedelta(minutes=22))
    assert coordinator.reminder("game", NOW + timedelta(minutes=22), NOW + timedelta(hours=1)) is None
    assert coordinator.reminder("game", NOW + timedelta(hours=2), NOW + timedelta(hours=1)) is None
    row = state.db.execute("SELECT state FROM peer_endgame_state WHERE game_id='game'").fetchone()
    assert row["state"] == "PEER_DEADLINE_RECONCILING"
    state.close()


def test_peer_trusted_acceptance_releases_without_saruku_submission(tmp_path):
    state = StateStore(tmp_path / "state.db"); store = EntryClosureStore(state)
    source = frozen(final="did:key:zPeerFinal")
    result = reconcile_frozen_poem(source)
    store.save_reconciliation(source, result, NOW)
    assert store.release_from_peer_receipt(
        "game", "peer-submit", "peer-entry", 91, "did:key:zReferee", NOW
    )
    assert store.commitment("game")["state"] == "RELEASED"
    assert store.parts("game") == []
    state.close()


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_signed_generic_submission_receipt_uses_observed_schema(status):
    key = Ed25519PrivateKey.generate()
    payload = {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID,
        "request_id": "submit-1", "sender_did": SARUKU_DID,
        "intake_seq": 1234, "received_at": 1789403052.0,
        "status": status, "reason": "" if status == "accepted" else "publication: unverified",
    }
    if status == "accepted":
        payload.update(entry_id="game", eligibility="pending")
    receipt = receipt_candidate(ROOMS.submissions, signed(key, ROOMS.submissions, payload, 9), did_of(key))
    assert receipt is not None
    assert receipt.kind == f"submission_{status}"


def test_daemon_generic_receipt_releases_own_and_peer_final_games(tmp_path):
    referee_key = Ed25519PrivateKey.generate()
    referee = did_of(referee_key)

    (tmp_path / "own").mkdir()
    own_state, own_store, source, result, parts = ready_store(tmp_path / "own")
    confirm_all(own_store, parts)
    packet = own_store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.MATCH,
        NOW, NOW + timedelta(hours=1), "own-submit",
    )
    own_state.reserve_request("own-submit", "submit", packet)
    own_state.set("active_team", "game")
    own_state.phase = Phase.WAIT_SUBMISSION_RECEIPT
    own_state.record_event(ROOMS.submissions, 1, 1, signed(referee_key, ROOMS.submissions, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID,
        "request_id": "own-submit", "sender_did": SARUKU_DID,
        "intake_seq": 100, "received_at": 1789403052.0, "status": "accepted",
        "reason": "", "entry_id": "game", "eligibility": "pending",
    }, 1))
    own_daemon = daemon_for(tmp_path / "own", own_state, referee)
    own_daemon._receipts(ROOMS.submissions)
    assert own_store.commitment("game")["state"] == "RELEASED"
    assert own_state.phase == Phase.DONE
    own_state.close()

    (tmp_path / "peer").mkdir()
    peer_state = StateStore(tmp_path / "peer" / "state.db")
    peer_store = EntryClosureStore(peer_state)
    peer = frozen(final="did:key:zPeerFinal")
    peer_store.save_reconciliation(peer, reconcile_frozen_poem(peer), NOW)
    peer_state.set("active_team", "game")
    peer_state.phase = Phase.WAIT_SUBMISSION_RECEIPT
    peer_state.record_event(ROOMS.submissions, 2, 1, signed(referee_key, ROOMS.submissions, {
        "type": "sonnet.receipt.v1", "contest_id": CONTEST_ID,
        "request_id": "peer-submit", "sender_did": "did:key:zPeerFinal",
        "intake_seq": 101, "received_at": 1789403053.0, "status": "accepted",
        "reason": "", "entry_id": "game", "eligibility": "pending",
    }, 2))
    peer_daemon = daemon_for(tmp_path / "peer", peer_state, referee)
    peer_daemon._receipts(ROOMS.submissions)
    assert peer_store.commitment("game")["state"] == "RELEASED"
    assert peer_state.phase == Phase.DONE
    peer_state.close()


def test_daemon_submission_unknown_restart_reuses_exact_packet_and_request_id(tmp_path):
    state, store, source, result, parts = ready_store(tmp_path)
    confirm_all(store, parts)
    packet = store.prepare_submission(
        source, result.poem_sha256, PublicationIntegrity.MATCH,
        NOW, NOW + timedelta(hours=1), "stable-submit",
    )
    store.mark_submission_unknown("stable-submit", NOW - timedelta(minutes=1))
    state.reserve_request("stable-submit", "submit", packet)
    state.set("active_team", "game")
    state.phase = Phase.WAIT_SUBMISSION_RECEIPT
    path = state.path
    state.close()

    restarted = StateStore(path)
    daemon = daemon_for(tmp_path, restarted, "did:key:zReferee")
    sent = []
    daemon._post = lambda room, payload: sent.append((room, payload.copy()))
    assert daemon._reconcile_submission_delivery()
    assert sent == [(ROOMS.submissions, packet)]
    assert sent[0][1]["request_id"] == "stable-submit"
    assert not daemon._reconcile_submission_delivery()
    assert len(sent) == 1
    restarted.close()


def test_daemon_uses_actual_x_response_text_for_strong_confirmation(monkeypatch, tmp_path):
    state, store, _, _, _ = ready_store(tmp_path)
    state.set("active_team", "game")
    state.set("deadline", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    state.phase = Phase.PUBLISH_IF_FINAL_CONTRIBUTOR
    daemon = daemon_for(tmp_path, state, "did:key:zReferee", x_publish_cmd="safe-adapter")

    class Manager:
        def __init__(self, *args): pass
        def get_valid_access_token(self): return "token"
        def verify_identity(self, token): return XIdentity("sarukubt", "account")
        def close(self): pass
    class Publisher:
        def __init__(self, command): pass
        def publish_part(self, text, reply_to):
            return {"id": "post-1", "text": "different", "author_id": "account"}

    monkeypatch.setattr("sonnet_chain.daemon.XTokenManager", Manager)
    monkeypatch.setattr("sonnet_chain.daemon.CommandPublisher", Publisher)
    daemon._publish()
    assert store.parts("game")[0]["confirmed_text"] == "different"
    assert store.parts("game")[0]["strong_confirmation"] == 0
    state.close()


def test_daemon_known_auth_failure_remains_retryable_after_repair(monkeypatch, tmp_path):
    state, store, _, _, _ = ready_store(tmp_path)
    state.set("active_team", "game")
    state.set("deadline", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    state.phase = Phase.PUBLISH_IF_FINAL_CONTRIBUTOR
    daemon = daemon_for(tmp_path, state, "did:key:zReferee", x_publish_cmd="safe-adapter")

    class Manager:
        def __init__(self, *args): pass
        def get_valid_access_token(self): return "token"
        def verify_identity(self, token): return XIdentity("sarukubt", "account")
        def close(self): pass
    calls = 0
    class Publisher:
        def __init__(self, command): pass
        def publish_part(self, text, reply_to):
            nonlocal calls
            calls += 1
            if calls == 1:
                from sonnet_chain.publisher import PublisherAuthRequired
                raise PublisherAuthRequired("auth")
            return {"id": "post-1", "text": text, "author_id": "account"}

    monkeypatch.setattr("sonnet_chain.daemon.XTokenManager", Manager)
    monkeypatch.setattr("sonnet_chain.daemon.CommandPublisher", Publisher)
    daemon._publish()
    assert store.parts("game")[0]["state"] == PublicationDelivery.DEFINITELY_NOT_SENT
    assert state.phase == Phase.X_AUTH_REQUIRED
    daemon.cycle()
    assert store.parts("game")[0]["state"] == PublicationDelivery.CONFIRMED
    assert calls == 2
    state.close()


def test_step5a_migration_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    StateStore(path).close()
    reopened = StateStore(path)
    tables = {row[0] for row in reopened.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"entry_closures", "publication_parts", "submission_intents",
            "participant_commitments", "peer_endgame_state"} <= tables
    publication_columns = {row[1] for row in reopened.db.execute(
        "PRAGMA table_info(publication_parts)"
    )}
    submission_columns = {row[1] for row in reopened.db.execute(
        "PRAGMA table_info(submission_intents)"
    )}
    assert "confirmed_parent_post_id" in publication_columns
    assert {"attempt_count", "next_retry_at"} <= submission_columns
    reopened.close()


def test_frozen_input_carries_formation_roster_fingerprint_into_commitment(tmp_path):
    state = StateStore(tmp_path / "state.db")
    source = frozen()
    state.set("active_team", source.game_id)
    state.set("poem_room", source.poem_room)
    state.set("team_setup", {"room_generation": source.room_generation})
    state.set("poem_version", source.final_version)
    state.set("poem_state_hash", source.final_state_hash)
    state.set("final_contributor", source.final_contributor_did)
    with state.db:
        state.db.execute(
            "INSERT INTO formation_epochs(epoch_id,game_id,inviter_did,payload_json,status,active) "
            "VALUES('epoch','game','did:key:zLead',?,'team_ready',0)",
            (__import__("json").dumps({"consent_roster_fingerprint": "roster-fp"}),),
        )
        for index, payload in enumerate(source.accepted_word_ledger, 1):
            state.db.execute(
                "INSERT INTO receipts(room,generation,seq,kind,payload,accepted) "
                "VALUES(?,?,?,?,?,1)",
                (source.poem_room, source.room_generation, index, "word_accepted",
                 __import__("json").dumps({**payload, "game_id": "game",
                                           "room_generation": source.room_generation})),
            )
    rebuilt = frozen_input_from_state(state)
    assert rebuilt is not None and rebuilt.roster_fingerprint == "roster-fp"
    result = reconcile_frozen_poem(rebuilt)
    EntryClosureStore(state).save_reconciliation(rebuilt, result, NOW)
    assert EntryClosureStore(state).commitment("game")["roster_fingerprint"] == "roster-fp"
    state.close()
