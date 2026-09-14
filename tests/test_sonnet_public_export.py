from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from sonnet_chain.config import ROOMS, SARUKU_DID
from sonnet_chain.sonnet_public_export import (
    PublicExportError, build_public_document, publish_document, stage_document,
)
from sonnet_chain.state import Phase, StateStore


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _state(tmp_path):
    state = StateStore(tmp_path / "state.db"); state.phase = Phase.DISCOVERY
    with state.db:
        state.db.execute(
            "INSERT INTO formation_reflex_frontiers(source_room,source_generation,last_seq,activated_at) "
            "VALUES(?,?,?,?)", (ROOMS.discovery, 2, 100, "2026-09-14T10:00:00Z"),
        )
    return state


def _formation(state, seq, kind, payload, sender="did:key:zOther", game="g",
               observed="2026-09-14T11:00:00Z"):
    raw = {"seq": seq, "from": sender, "nonce": "private", "sig": "private-signature",
           "text": json.dumps(payload), "created_at": observed}
    state.persist_formation_event(ROOMS.discovery, 2, seq, kind, game, sender,
                                  "private-request-id", "fingerprint", raw, raw["created_at"])


def test_empty_discovery_is_seeking_team(tmp_path):
    state = _state(tmp_path); document = build_public_document(state.path, NOW)
    assert document["mission"]["status"] == "seeking_team"
    assert document["mission"]["current_stage"] == "LOOKING"


def test_baseline_omits_old_invite_and_new_invite_is_public_safe(tmp_path):
    state = _state(tmp_path)
    _formation(state, 90, "TARGETED_RECRUITMENT_NOTE", {"text": "old secret invitation"},
               observed="2026-09-14T09:00:00Z")
    _formation(state, 101, "TARGETED_RECRUITMENT_NOTE", {"text": "new secret invitation"})
    document = build_public_document(state.path, NOW); encoded = json.dumps(document)
    assert document["counts"]["targeted_invites"] == 1
    assert [x["title"] for x in document["recent_activity"]] == ["Team invitation"]
    assert "secret invitation" not in encoded and "private-request-id" not in encoded
    assert "private-signature" not in encoded and "did:key" not in encoded


def test_direct_mention_reply_and_conflict_reason(tmp_path):
    state = _state(tmp_path)
    with state.db:
        state.db.execute(
            "INSERT INTO formation_reflex_processing(source_room,source_generation,source_seq,game_id,"
            "trigger_kind,protocol_state,status,created_at,processed_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ROOMS.team("g"), 4, 8, "g", "team_direct_message", "FORMING_OTHER_GAME",
             "REPLIED", "2026-09-14T11:30:00Z", "2026-09-14T11:30:00Z"),
        )
    document = build_public_document(state.path, NOW)
    assert {x["type"] for x in document["recent_activity"]} == {"called_by_name", "replied"}
    detail = next(x["detail"] for x in document["recent_activity"] if x["type"] == "replied")
    assert "conflicting commitment" in detail and "FORMING_OTHER_GAME" not in detail


def test_application_roster_countersign_and_terminal_states(tmp_path):
    state = _state(tmp_path)
    state.reserve_request("secret-request", "application:g", {"game_id": "g"})
    state.db.execute("UPDATE requests SET created_at='2026-09-14T11:00:00Z'")
    members = [SARUKU_DID, "did:key:zOther"]
    _formation(state, 102, "ROSTER_CONSENT", {"members": members}, sender="did:key:zOther")
    _formation(state, 103, "ROSTER_CONSENT", {"members": members}, sender=SARUKU_DID)
    document = build_public_document(state.path, NOW)
    assert {"applied", "roster", "countersigned"} <= {x["type"] for x in document["recent_activity"]}
    state.phase = Phase.WAIT_ROSTER_READY
    waiting = build_public_document(state.path, NOW)
    assert (waiting["mission"]["status"], waiting["mission"]["current_stage"]) == ("forming_team", "SIGNED")
    state.set("roster_ready", True)
    ready = build_public_document(state.path, NOW)
    assert (ready["mission"]["status"], ready["mission"]["current_stage"]) == ("team_ready", "TEAM")
    for phase, status, stage in [
        (Phase.WRITING, "writing", "WRITING"),
        (Phase.POEM_COMPLETE, "poem_complete", "COMPLETE"),
        (Phase.WAIT_SUBMISSION_RECEIPT, "submission_pending", "COMPLETE"),
        (Phase.DONE, "submitted", "SUBMITTED"),
    ]:
        state.phase = phase
        if phase == Phase.DONE: state.set("submission_state", "accepted")
        current = build_public_document(state.path, NOW)
        assert (current["mission"]["status"], current["mission"]["current_stage"]) == (status, stage)


def test_export_is_stable_except_checked_at_and_invalid_does_not_replace(tmp_path):
    state = _state(tmp_path)
    one = build_public_document(state.path, NOW)
    two = build_public_document(state.path, NOW.replace(minute=1))
    one.pop("checked_at"); two.pop("checked_at")
    assert one == two
    target = tmp_path / "latest.json"; target.write_text("last-good", encoding="utf-8")
    with pytest.raises(PublicExportError): stage_document({"schema_version": 99}, target)
    assert target.read_text(encoding="utf-8") == "last-good"


def test_failed_kv_publish_does_not_change_staged_document(tmp_path, monkeypatch):
    state = _state(tmp_path); document = build_public_document(state.path, NOW)
    target = tmp_path / "latest.json"; stage_document(document, target); before = target.read_bytes()
    def fail(*args, **kwargs):
        raise httpx.ConnectError("provider private details")
    monkeypatch.setattr(httpx, "put", fail)
    with pytest.raises(httpx.ConnectError): publish_document(document, "account", "namespace", "secret")
    assert target.read_bytes() == before


def test_large_unrelated_history_does_not_enter_public_query(tmp_path):
    state = _state(tmp_path); raw = json.dumps({"seq": 1, "from": "x", "text": "{}"})
    with state.db:
        state.db.executemany(
            "INSERT INTO formation_events(room,generation,seq,event_kind,game_id,sender_did,"
            "normalized_payload,observed_at,verified) VALUES(?,?,?,?,?,?,?,?,1)",
            [(ROOMS.discovery, 2, i, "ROSTER_WITHDRAWAL", f"old-{i}", "x", raw,
              "2026-09-14T09:00:00Z") for i in range(1, 50001)],
        )
    document = build_public_document(state.path, NOW)
    assert document["recent_activity"] == []
