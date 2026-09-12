from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain.config import CONTEST_ID, Config, ROOMS, SARUKU_DID
from sonnet_chain.daemon import Daemon
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import Phase, StateStore
from sonnet_chain.team_intelligence import (
    CAPABILITY_TEXT,
    SEMANTIC_SCHEMA_VERSION,
    TEAM_MESSAGE_SCHEMA,
    TeamIntelligence,
    capability_announcement,
)

GAME = "intel"
ROOM = ROOMS.team(GAME)
GENERATION = 3


def _signed(key: Ed25519PrivateKey, payload: Any, seq: int) -> dict[str, Any]:
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    stored, sig = signer.sign_room(ROOM, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "text": stored, "sig": sig}


def _empty() -> dict[str, list[Any]]:
    return {
        "claims": [], "proposals": [], "questions": [], "constraints": [],
        "retractions": [], "unclassified": [],
    }


class FakeLLM:
    def __init__(self, outputs: dict[str, dict[str, Any]] | None = None, fail: bool = False):
        self.outputs = outputs or {}
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = []

    def structured(self, task, external_data, schema_name, schema):
        self.calls.append((task, external_data, schema_name, schema))
        if self.fail:
            raise RuntimeError("offline")
        return self.outputs.get(external_data["text"], _empty())


def _note(text: str) -> dict[str, Any]:
    return {"type": "sonnet.note.v1", "contest_id": CONTEST_ID, "text": text}


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def _sync(store: StateStore, members: list[str], llm: FakeLLM | None, referee: str | None = None):
    return TeamIntelligence(store, llm).sync(  # type: ignore[arg-type]
        GAME, ROOM, GENERATION, members, referee
    )


def test_step2_tables_and_schema_exist(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tables = {
            row["name"] for row in store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"team_events", "team_message_analysis", "team_context_snapshots"} <= tables
        assert SEMANTIC_SCHEMA_VERSION == "team-intelligence.v1"
    finally:
        store.close()


def test_im_bruce_keeps_verified_speaker_did(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["claims"] = [{
        "predicate": "self_declared_alias", "value": "Bruce", "scope": "poem", "confidence": 0.9,
    }]
    llm = FakeLLM({"I'm Bruce": output})
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("I'm Bruce"), 1))
    try:
        _sync(store, [speaker], llm)
        row = store.db.execute("SELECT * FROM team_events WHERE event_type='claim'").fetchone()
        assert row["actor_did"] == speaker
        assert row["subject_did"] == speaker
        provenance = json.loads(row["payload_json"])["source"]
        assert provenance["verified_speaker_did"] == speaker
        assert "speaker_did" not in TEAM_MESSAGE_SCHEMA["properties"]
    finally:
        store.close()


def test_invalid_signature_creates_no_semantic_event(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    raw = _signed(key, _note("I cannot publish to X"), 1)
    raw["text"] = raw["text"].replace("cannot", "can")
    llm = FakeLLM()
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, raw)
    try:
        _sync(store, [did_of(key)], llm)
        assert store.db.execute(
            "SELECT count(*) FROM team_events WHERE source_seq>0"
        ).fetchone()[0] == 0
        assert llm.calls == []
    finally:
        store.close()


def test_structured_word_action_is_deterministic_and_skips_llm(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    payload = {
        "type": "sonnet.word.v1", "contest_id": CONTEST_ID, "game_id": GAME,
        "room_generation": GENERATION, "version": 0, "previous_state_hash": "hash",
        "word": "the", "request_id": "word-1",
    }
    llm = FakeLLM(fail=True)
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, payload, 1))
    try:
        _sync(store, [speaker], llm)
        row = store.db.execute(
            "SELECT evidence_class,event_type,actor_did FROM team_events "
            "WHERE event_type='protocol_word_proposed'"
        ).fetchone()
        assert tuple(row) == ("FACT", "protocol_word_proposed", speaker)
        assert llm.calls == []
    finally:
        store.close()


def test_claim_is_not_promoted_to_fact_and_creates_risk(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["claims"] = [{
        "predicate": "can_publish_x", "value": False, "scope": "poem", "confidence": 0.99,
    }]
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("I cannot publish to X"), 1))
    try:
        snapshot = _sync(store, [speaker], FakeLLM({"I cannot publish to X": output}))
        rows = store.db.execute(
            "SELECT evidence_class,event_type FROM team_events ORDER BY event_type"
        ).fetchall()
        assert ("CLAIM", "claim") in [tuple(row) for row in rows]
        assert ("INFERENCE", "terminal_publish_risk") in [tuple(row) for row in rows]
        assert not any(
            row["evidence_class"] == "FACT" and row["event_type"] == "claim" for row in rows
        )
        assert snapshot["terminal_risks"][0]["active"] is True
        assert snapshot["final_contributor_reserved"] is None
    finally:
        store.close()


def test_conflicting_claims_are_both_preserved(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    cannot, can = _empty(), _empty()
    cannot["claims"] = [{"predicate": "can_publish_x", "value": False,
                         "scope": "poem", "confidence": 1.0}]
    can["claims"] = [{"predicate": "can_publish_x", "value": True,
                      "scope": "poem", "confidence": 1.0}]
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("cannot"), 1))
    store.record_event(ROOM, 2, GENERATION, _signed(key, _note("fixed"), 2))
    try:
        llm = FakeLLM({"cannot": cannot, "fixed": can})
        _sync(store, [speaker], llm)
        snapshot = _sync(store, [speaker], llm)
        assert store.db.execute("SELECT count(*) FROM team_events WHERE event_type='claim'").fetchone()[0] == 2
        claim = snapshot["members"][speaker]["claims"]["can_publish_x"]
        assert claim["latest"]["value"] is True
        assert len(claim["conflicting_claim_event_ids"]) == 1
        assert snapshot["terminal_risks"][0]["active"] is False
    finally:
        store.close()


def test_conflicting_proposals_preserved_and_stale_after_version(tmp_path: Path) -> None:
    first_key, second_key, referee_key = [Ed25519PrivateKey.generate() for _ in range(3)]
    first, second, referee = did_of(first_key), did_of(second_key), did_of(referee_key)
    outputs = {}
    for text, target in (("first goes", first), ("second goes", second)):
        value = _empty()
        value["proposals"] = [{
            "proposal_type": "next_writer", "target_text": target, "value": None,
            "scope": "next_word", "confidence": 0.9,
        }]
        outputs[text] = value
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(first_key, _note("first goes"), 1))
    store.record_event(ROOM, 2, GENERATION, _signed(second_key, _note("second goes"), 2))
    receipt = {
        "type": "sonnet.word-accepted.v1", "contest_id": CONTEST_ID,
        "version": 1, "contributor_did": second,
    }
    store.record_event(ROOM, 3, GENERATION, _signed(referee_key, receipt, 3))
    try:
        llm = FakeLLM(outputs)
        _sync(store, [first, second], llm, referee)
        snapshot = _sync(store, [first, second], llm, referee)
        assert store.db.execute("SELECT count(*) FROM team_events WHERE event_type='proposal'").fetchone()[0] == 2
        assert snapshot["active_proposals"] == []
        assert len(snapshot["stale_proposals"]) == 2
        assert all(item["observed_at_version"] == 0 for item in snapshot["stale_proposals"])
    finally:
        store.close()


def test_unresolved_alias_remains_null(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["proposals"] = [{
        "proposal_type": "next_writer", "target_text": "Bruce", "value": None,
        "scope": "next_word", "confidence": 0.9,
    }]
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("Bruce should go"), 1))
    try:
        snapshot = _sync(store, [speaker], FakeLLM({"Bruce should go": output}))
        assert snapshot["active_proposals"][0]["resolved_target_did"] is None
        assert snapshot["unresolved_mentions"][0]["target_text"] == "Bruce"
    finally:
        store.close()


def test_replay_is_idempotent_and_restart_rebuild_matches(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["claims"] = [{"predicate": "can_publish_x", "value": False,
                         "scope": "poem", "confidence": 0.9}]
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("cannot"), 1))
    intel = TeamIntelligence(store, FakeLLM({"cannot": output}))  # type: ignore[arg-type]
    first = intel.sync(GAME, ROOM, GENERATION, [speaker], None)
    second = intel.sync(GAME, ROOM, GENERATION, [speaker], None)
    count = store.db.execute("SELECT count(*) FROM team_events").fetchone()[0]
    store.close()

    restarted = StateStore(db_path)
    try:
        rebuilt = TeamIntelligence(restarted).rebuild_snapshot(GAME, ROOM, GENERATION)
        assert count == 6
        assert first == second == rebuilt
    finally:
        restarted.close()


def test_llm_failure_and_unknown_text_are_isolated(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, "unknown words", 1))
    try:
        start = datetime.now(timezone.utc)
        snapshot = TeamIntelligence(store, FakeLLM(fail=True)).sync(
            GAME, ROOM, GENERATION, [speaker], None, now=start
        )
        assert snapshot["unparsed_messages"]["count"] == 1
        snapshot = TeamIntelligence(store, FakeLLM({"unknown words": _empty()})).sync(
            GAME, ROOM, GENERATION, [speaker], None, now=start + timedelta(seconds=61)
        )
        status = store.db.execute("SELECT status FROM team_message_analysis").fetchone()[0]
        assert status == "parsed_empty"
        assert snapshot["game_id"] == GAME
    finally:
        store.close()


def test_sync_analyzes_at_most_one_planning_message(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("one"), 1))
    store.record_event(ROOM, 2, GENERATION, _signed(key, _note("two"), 2))
    llm = FakeLLM()
    try:
        _sync(store, [speaker], llm)
        assert len(llm.calls) == 1
        assert store.db.execute(
            "SELECT count(*) FROM team_message_analysis"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_retryable_analysis_is_bounded_and_backed_off(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, "unknown", 1))
    llm = FakeLLM(fail=True)
    start = datetime.now(timezone.utc)
    intel = TeamIntelligence(store, llm)  # type: ignore[arg-type]
    try:
        intel.sync(GAME, ROOM, GENERATION, [speaker], None, now=start)
        intel.sync(GAME, ROOM, GENERATION, [speaker], None, now=start + timedelta(seconds=30))
        intel.sync(GAME, ROOM, GENERATION, [speaker], None, now=start + timedelta(seconds=61))
        intel.sync(GAME, ROOM, GENERATION, [speaker], None, now=start + timedelta(seconds=362))
        intel.sync(GAME, ROOM, GENERATION, [speaker], None, now=start + timedelta(hours=2))
        assert len(llm.calls) == 3
        row = store.db.execute("SELECT attempts,status FROM team_message_analysis").fetchone()
        assert tuple(row) == (3, "retryable_error")
    finally:
        store.close()


def test_member_facts_and_generation_isolation(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    store = _store(tmp_path)
    try:
        first = TeamIntelligence(store).sync(GAME, ROOM, GENERATION, [speaker], None)
        second = TeamIntelligence(store).sync(GAME, ROOM, GENERATION + 1, [], None)
        facts = first["members"][speaker]["facts"]
        assert facts["roster_member"] is True
        assert facts["has_contributed"] is False
        assert facts["available_letters"]
        assert isinstance(facts["missing_letters"], list)
        assert second["members"] == {}
    finally:
        store.close()


def test_current_line_proposal_stales_and_constraints_are_preserved(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["proposals"] = [{"proposal_type": "line_shape", "target_text": None,
                            "value": "short", "scope": "current_line", "confidence": 0.8}]
    output["constraints"] = [{"predicate": "rhyme", "value": "night",
                              "scope": "current_line", "confidence": 0.9}]
    output["unclassified"] = ["perhaps later"]
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("plan"), 1))
    try:
        intel = TeamIntelligence(store, FakeLLM({"plan": output}))  # type: ignore[arg-type]
        initial = intel.sync(GAME, ROOM, GENERATION, [speaker], None, current_line=2)
        later = intel.rebuild_snapshot(GAME, ROOM, GENERATION, current_line=3)
        assert initial["active_proposals"][0]["observed_at_line"] == 2
        assert later["active_proposals"] == []
        assert len(later["stale_proposals"]) == 1
        assert later["constraints"][0]["predicate"] == "rhyme"
        assert later["unclassified"][0]["value"] == "perhaps later"
        source = later["unclassified"][0]["source"]
        assert (source["room"], source["room_generation"], source["seq"],
                source["verified_speaker_did"], source["source_text_sha256"]) == (
                    ROOM, GENERATION, 1, speaker,
                    hashlib.sha256(
                        json.dumps(_note("plan"), separators=(",", ":")).encode()
                    ).hexdigest(),
                )
    finally:
        store.close()


def test_source_ordinal_orders_semantics_within_one_message(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    speaker = did_of(key)
    output = _empty()
    output["claims"] = [
        {"predicate": "first", "value": 1, "scope": "poem", "confidence": 1.0},
        {"predicate": "second", "value": 2, "scope": "poem", "confidence": 1.0},
    ]
    store = _store(tmp_path)
    store.record_event(ROOM, 1, GENERATION, _signed(key, _note("ordered"), 1))
    try:
        _sync(store, [speaker], FakeLLM({"ordered": output}))
        rows = store.db.execute(
            "SELECT source_ordinal,predicate FROM team_events WHERE source_seq=1 "
            "ORDER BY source_seq,source_ordinal"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, "first"), (2, "second")]
    finally:
        store.close()


def _writing_daemon(tmp_path: Path) -> Daemon:
    daemon = Daemon.__new__(Daemon)
    daemon.cfg = Config(
        "https://example.test", None, "https://x.com/sarukubt", None, None,
        tmp_path, "commit", state_db=tmp_path / "state.db",
    )
    daemon.state = StateStore(daemon.cfg.state_db)
    daemon.state.phase = Phase.WRITING
    daemon.state.set("deadline", "2099-01-01T00:00:00Z")
    daemon.state.set("active_team", GAME)
    daemon.state.set("poem_room", ROOM)
    daemon.state.set("team_setup", {"room_generation": GENERATION})
    daemon.state.set("current_roster", [SARUKU_DID])
    daemon.state.set("previous_contributor", SARUKU_DID)
    daemon._receipts = lambda room: None
    return daemon


def test_capability_announcement_is_at_most_once_per_game(tmp_path: Path) -> None:
    daemon = _writing_daemon(tmp_path)
    posted = []
    daemon._post = lambda room, payload: posted.append((room, payload))
    daemon.live = True
    try:
        daemon._writing()
        daemon._writing()
        assert len(posted) == 1
        assert posted[0][0] == ROOM
        assert posted[0][1]["text"] == CAPABILITY_TEXT
        assert "If I become the final contributor" in posted[0][1]["text"]
    finally:
        daemon.state.close()


def test_team_intelligence_failure_does_not_stop_legacy_writing(monkeypatch, tmp_path: Path) -> None:
    daemon = _writing_daemon(tmp_path)
    intro = capability_announcement(GAME, "capabilities-1")
    daemon.state.reserve_request("capabilities-1", f"team_capability_announcement:{GAME}", intro)
    monkeypatch.setattr(
        "sonnet_chain.daemon.TeamIntelligence.sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    daemon.live = False
    try:
        daemon._writing()
        assert daemon.state.phase == Phase.WRITING
        assert daemon.state.get("last_team_intelligence_error") == "RuntimeError"
    finally:
        daemon.state.close()
