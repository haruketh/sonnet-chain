from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sonnet_chain.config import ROOMS, SARUKU_DID
from sonnet_chain.sonnet_public_export import (
    PublicExportError, build_public_document, load_cloudflare_credentials,
    publish_document, stage_document,
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
    assert document["schema_version"] == 2 and document["writing"] is None


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


def test_no_reply_is_internal_but_replied_remains_public(tmp_path):
    state = _state(tmp_path)
    rows = [
        (ROOMS.team("g"), 4, 8, "g", "team_direct_message", "AVAILABLE", "REPLIED"),
        (ROOMS.team("g"), 4, 9, "g", "team_direct_message", "FORMING_OTHER_GAME", "NO_REPLY"),
        (ROOMS.discovery, 2, 10, "other", "targeted_invite", "AVAILABLE", "NO_REPLY"),
    ]
    with state.db:
        state.db.executemany(
            "INSERT INTO formation_reflex_processing(source_room,source_generation,source_seq,game_id,"
            "trigger_kind,protocol_state,status,created_at,processed_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [(*row, "2026-09-14T11:30:00Z", "2026-09-14T11:30:00Z") for row in rows],
        )
    document = build_public_document(state.path, NOW)
    assert document["counts"]["reflex_replies"] == 1
    assert [item["type"] for item in document["recent_activity"]].count("replied") == 1
    assert {item["type"] for item in document["recent_activity"]} == {
        "called_by_name", "replied",
    }
    encoded = json.dumps(document)
    assert "no_reply" not in encoded.casefold()
    assert "reason_code" not in encoded


def test_mixed_sqlite_and_iso_timestamps_are_compared_chronologically(tmp_path):
    state = _state(tmp_path)
    with state.db:
        state.db.execute(
            "INSERT INTO formation_reflex_processing(source_room,source_generation,source_seq,game_id,"
            "trigger_kind,protocol_state,status,created_at,processed_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ROOMS.discovery, 2, 101, "g", "targeted_invite", "AVAILABLE", "REPLIED",
             "2026-09-14 11:00:00", "2026-09-14 11:00:00"),
        )
    state.reserve_request("private", "application:g", {"game_id": "g"})
    state.db.execute("UPDATE requests SET created_at='2026-09-14 11:00:00'")
    _formation(state, 102, "TARGETED_RECRUITMENT_NOTE", {}, observed="2026-09-14T11:05:00+00:00")
    _formation(state, 103, "APPLICATION_READBACK", {}, observed="2026-09-14 11:10:00")
    document = build_public_document(state.path, NOW)
    assert document["counts"]["reflex_replies"] == 1
    assert document["counts"]["applications"] == 1
    assert document["counts"]["targeted_invites"] == 1


def test_application_roster_countersign_and_terminal_states(tmp_path):
    state = _state(tmp_path)
    state.reserve_request("secret-request", "application:g", {"game_id": "g"})
    state.db.execute("UPDATE requests SET created_at='2026-09-14T11:00:00Z'")
    _formation(state, 101, "APPLICATION_READBACK", {})
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


def _writing_state(state, phase=Phase.WRITING):
    state.phase = phase
    state.set("active_team", "g")
    state.set("current_roster", ["did:key:zAlpha", SARUKU_DID, "did:key:zBeta"])
    state.set("team_setup", {"game_id": "g", "poem_room": ROOMS.team("g"),
                             "room_generation": 7, "members": ["private"]})
    state.set("poem_lines", ["Accepted first line", "Accepted second line"])
    state.set("poem_version", 9)
    state.set("previous_contributor", "did:key:zBeta")


def _team_source(state, seq, sender, payload, *, generation=7, room=None, verified=True,
                 observed="2026-09-14T11:30:00Z"):
    room = room or ROOMS.team("g")
    raw = {"seq": seq, "from": sender, "text": json.dumps(payload) if isinstance(payload, dict) else payload,
           "created_at": observed, "sig": "private-signature", "request_id": "private-request"}
    if verified:
        state.persist_team_source(room, generation, seq, sender, raw,
                                  payload.get("type") if isinstance(payload, dict) else None,
                                  "private-request", "semantic")
    else:
        state.record_event(room, seq, generation, raw)


def _receipt(state, seq, kind, payload, *, generation=7, room=None,
             observed="2026-09-14T11:40:00Z"):
    room = room or ROOMS.team("g")
    raw = {"seq": seq, "from": "did:key:zReferee", "text": "{}",
           "created_at": observed, "sig": "private-signature"}
    state.record_event(room, seq, generation, raw)
    with state.db:
        state.db.execute(
            "INSERT INTO receipts(room,generation,seq,kind,payload,accepted) VALUES(?,?,?,?,?,1)",
            (room, generation, seq, kind, json.dumps(payload)),
        )


@pytest.mark.parametrize("phase", [Phase.WRITING, Phase.POEM_COMPLETE,
                                   Phase.WAIT_SUBMISSION_RECEIPT, Phase.DONE])
def test_writing_document_and_aliases_persist_through_terminal_phases(tmp_path, phase):
    state = _state(tmp_path); _writing_state(state, phase)
    if phase == Phase.DONE: state.set("submission_state", "accepted")
    first = build_public_document(state.path, NOW)
    second = build_public_document(state.path, NOW)
    assert first["writing"] == second["writing"]
    assert first["writing"] == {
        "version": 9, "line_number": 3,
        "lines": ["Accepted first line", "Accepted second line"],
        "previous_contributor": "Teammate B",
        "team": ["Teammate A", "Saruku", "Teammate B"], "activity": [],
    }
    assert "did:key" not in json.dumps(first)


def test_verified_current_team_sources_only_and_proposal_never_changes_poem(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    _team_source(state, 1, SARUKU_DID, {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "I can take it."})
    _team_source(state, 2, "did:key:zAlpha", {"type": "sonnet.word.v1", "contest_id": "sonnet-2", "game_id": "g",
                                             "room_generation": 7, "word": "drifts",
                                             "request_id": "secret"})
    _team_source(state, 3, "did:key:zOutside", {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "outsider"})
    _team_source(state, 4, "did:key:zAlpha", {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "wrong generation"}, generation=8)
    _team_source(state, 5, "did:key:zAlpha", {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "wrong room"}, room=ROOMS.team("other"))
    _team_source(state, 6, "did:key:zAlpha", {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "unverified"}, verified=False)
    _team_source(state, 7, "did:key:zAlpha", {"type": "sonnet.roster.v1", "members": []})
    writing = build_public_document(state.path, NOW)["writing"]
    assert writing["lines"] == ["Accepted first line", "Accepted second line"]
    assert {(item["type"], item.get("quote")) for item in writing["activity"]} == {
        ("word_proposed", "drifts"), ("message", "I can take it."),
    }


def test_authoritative_acceptance_line_and_completion_activity(tmp_path):
    state = _state(tmp_path); _writing_state(state, Phase.POEM_COMPLETE)
    _receipt(state, 1, "roster_ready", {"game_id": "g"}, room=ROOMS.discovery,
             generation=2, observed="2026-09-14T11:00:00Z")
    _receipt(state, 2, "word_accepted", {"game_id": "g", "version": 1,
                                        "contributor_did": "did:key:zAlpha", "word": "drifts",
                                        "lines": ["First line"]})
    _receipt(state, 3, "word_accepted", {"game_id": "g", "version": 2,
                                        "contributor_did": "did:key:zUnknown", "word": "night",
                                        "lines": ["First line", "Second line"], "complete": True})
    activity = build_public_document(state.path, NOW)["writing"]["activity"]
    assert {item["type"] for item in activity} == {
        "writing_started", "word_accepted", "line_completed", "poem_completed",
    }
    accepted = [item for item in activity if item["type"] == "word_accepted"]
    assert any(item["detail"].startswith("Teammate A") for item in accepted)
    assert any(item["detail"] == "A word was accepted." for item in accepted)


def test_writing_activity_is_bounded_to_40_and_message_to_500(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    for seq in range(1, 61):
        _team_source(state, seq, "did:key:zAlpha", {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "x" * 700},
                     observed=f"2026-09-14T11:{seq % 60:02d}:00Z")
    activity = build_public_document(state.path, NOW)["writing"]["activity"]
    assert len(activity) == 40
    assert all(len(item["quote"]) == 500 for item in activity)


def test_160_unrelated_rosters_cannot_hide_two_applications(tmp_path):
    state = _state(tmp_path)
    _formation(state, 101, "APPLICATION_READBACK", {}, sender=SARUKU_DID,
               observed="2026-09-14T10:10:00Z")
    members = ["did:key:zOne", "did:key:zTwo"]
    for seq in range(102, 273):
        _formation(state, seq, "ROSTER_CONSENT", {"members": members}, game=f"noise-{seq}")
    _formation(state, 273, "APPLICATION_READBACK", {}, sender=SARUKU_DID,
               observed="2026-09-14T11:50:00Z")
    document = build_public_document(state.path, NOW)
    assert document["counts"]["applications"] == 2
    assert len([item for item in document["recent_activity"] if item["type"] == "applied"]) == 2


def test_160_unrelated_rosters_cannot_hide_saruku_roster_or_countersign(tmp_path):
    state = _state(tmp_path)
    members = [SARUKU_DID, "did:key:zTeammate"]
    _formation(state, 101, "ROSTER_CONSENT", {"members": members},
               sender="did:key:zTeammate", game="ours")
    unrelated = ["did:key:zOne", "did:key:zTwo"]
    for seq in range(102, 273):
        _formation(state, seq, "ROSTER_CONSENT", {"members": unrelated}, game=f"noise-{seq}")
    _formation(state, 273, "ROSTER_CONSENT", {"members": members},
               sender=SARUKU_DID, game="ours")
    document = build_public_document(state.path, NOW)
    assert document["counts"]["rosters_with_saruku"] == 1
    assert document["counts"]["countersigns"] == 1
    assert {item["type"] for item in document["recent_activity"]} == {
        "roster", "countersigned",
    }


def test_accepted_word_tail_uses_81st_receipt_only_as_line_baseline(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    base = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    for seq in range(1, 86):
        lines = ["Line one", "Line two", "Line three"]
        if seq == 85:
            lines.append("Line four")
        _receipt(
            state, seq, "word_accepted",
            {"game_id": "g", "version": seq, "contributor_did": "did:key:zAlpha",
             "word": f"word{seq}", "lines": lines},
            observed=(base + timedelta(seconds=seq)).isoformat(),
        )
    activity = build_public_document(state.path, NOW)["writing"]["activity"]
    completed = [item["detail"] for item in activity if item["type"] == "line_completed"]
    assert completed == ["Line 4 was completed."]
    assert all("Line 1 " not in item and "Line 2 " not in item and "Line 3 " not in item
               for item in completed)


def test_public_schema_has_no_private_structural_fields(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    _team_source(state, 1, SARUKU_DID, {"type": "sonnet.note.v1", "contest_id": "sonnet-2", "text": "Public speech"})
    document = build_public_document(state.path, NOW)
    encoded = json.dumps(document)
    assert "did:key" not in encoded
    forbidden = {"request_id", "signature", "sig", "prompt", "confidence",
                 "candidate_validation", "branch_count", "planner_state", "path", "token"}
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()
    assert not forbidden & keys(document)


def test_full_did_in_public_speech_is_dropped(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    _team_source(state, 1, "did:key:zAlpha", {"type": "sonnet.note.v1",
                 "contest_id": "sonnet-2", "text": "Ask did:key:zSecret about this."})
    document = build_public_document(state.path, NOW)
    assert document["writing"]["activity"] == []
    assert "did:key" not in json.dumps(document)


def test_synthetic_v2_export_is_bounded_and_indexed(tmp_path):
    state = _state(tmp_path); _writing_state(state)
    members = ["did:key:zOne", "did:key:zTwo"]
    for seq in range(101, 272):
        _formation(state, seq, "ROSTER_CONSENT", {"members": members}, game=f"noise-{seq}")
    _formation(state, 272, "APPLICATION_READBACK", {}, sender=SARUKU_DID)
    for seq in range(1, 41):
        _team_source(state, seq, "did:key:zAlpha", {"type": "sonnet.note.v1",
                     "contest_id": "sonnet-2", "text": f"Public message {seq}"},
                     observed=f"2026-09-14T11:{seq:02d}:00Z")
    # Raw/unverified and other-generation rows remain outside the public source.
    _team_source(state, 41, "did:key:zAlpha", "raw unverified", verified=False)
    _team_source(state, 42, "did:key:zAlpha", {"type": "sonnet.note.v1",
                 "contest_id": "sonnet-2", "text": "wrong generation"}, generation=8)
    elapsed = []
    for _ in range(10):
        started = time.perf_counter(); document = build_public_document(state.path, NOW)
        elapsed.append(time.perf_counter() - started)
    assert len(document["writing"]["activity"]) == 40
    assert max(elapsed) < 0.25
    readonly = sqlite3.connect(f"file:{state.path.resolve()}?mode=ro", uri=True)
    plan = readonly.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM formation_events WHERE room=? AND generation=? "
        "AND verified=1 AND event_kind=? ORDER BY seq DESC LIMIT 40",
        (ROOMS.discovery, 2, "APPLICATION_READBACK"),
    ).fetchall()
    assert any("formation_events_kind_seq" in str(row) for row in plan)
    roster_plan = readonly.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM formation_events WHERE room=? AND generation=? "
        "AND verified=1 AND event_kind='ROSTER_CONSENT' AND EXISTS (SELECT 1 FROM "
        "json_each(json_extract(json_extract(formation_events.normalized_payload,'$.text'),"
        "'$.members')) member WHERE member.value=?) ORDER BY seq DESC LIMIT 160",
        (ROOMS.discovery, 2, SARUKU_DID),
    ).fetchall()
    assert any("formation_events_kind_seq" in str(row) for row in roster_plan)
    print({"runs": len(elapsed), "max_ms": round(max(elapsed) * 1000, 3),
           "writing_activity": len(document["writing"]["activity"]),
           "unrelated_rosters": 171})


@pytest.mark.parametrize("request_status", ["pending", "delivery_unknown", "expired", "rejected"])
def test_local_application_request_without_verified_readback_is_not_public(tmp_path, request_status):
    state = _state(tmp_path)
    state.reserve_request("private-request", "application:g", {"game_id": "g"})
    state.db.execute(
        "UPDATE requests SET status=?,created_at='2026-09-14 11:00:00' WHERE request_id='private-request'",
        (request_status,),
    )
    document = build_public_document(state.path, NOW)
    assert document["counts"]["applications"] == 0
    assert not any(item["type"] == "applied" for item in document["recent_activity"])


def test_verified_application_readback_is_exactly_one_public_applied_event(tmp_path):
    state = _state(tmp_path)
    state.reserve_request("private-request", "application:g", {"game_id": "g"})
    _formation(state, 101, "APPLICATION_READBACK", {}, observed="2026-09-14T11:00:00+00:00")
    document = build_public_document(state.path, NOW)
    applied = [item for item in document["recent_activity"] if item["type"] == "applied"]
    assert document["counts"]["applications"] == 1
    assert len(applied) == 1
    assert applied[0]["title"] == "Applied"
    assert applied[0]["detail"] == "Saruku's application was confirmed in Discovery."
    assert applied[0]["at"] == "2026-09-14T11:00:00Z"


def test_export_is_stable_except_checked_at_and_invalid_does_not_replace(tmp_path):
    state = _state(tmp_path)
    one = build_public_document(state.path, NOW)
    two = build_public_document(state.path, NOW.replace(minute=1))
    one.pop("checked_at"); two.pop("checked_at")
    assert one == two
    target = tmp_path / "latest.json"; target.write_text("last-good", encoding="utf-8")
    with pytest.raises(PublicExportError): stage_document({"schema_version": 99}, target)
    assert target.read_text(encoding="utf-8") == "last-good"


def test_nonlooking_state_does_not_fabricate_moving_activity_time(tmp_path):
    state = _state(tmp_path); state.phase = Phase.WRITING
    one = build_public_document(state.path, NOW)
    two = build_public_document(state.path, NOW.replace(minute=5))
    one.pop("checked_at"); two.pop("checked_at")
    assert one == two
    assert one["mission"]["current_stage"] == "WRITING"
    assert one["recent_activity"] == []


def test_failed_kv_publish_does_not_change_staged_document(tmp_path, monkeypatch):
    state = _state(tmp_path); document = build_public_document(state.path, NOW)
    target = tmp_path / "latest.json"; stage_document(document, target); before = target.read_bytes()
    def fail(*args, **kwargs):
        raise httpx.ConnectError("provider private details")
    monkeypatch.setattr(httpx, "put", fail)
    with pytest.raises(httpx.ConnectError): publish_document(document, "account", "namespace", "secret")
    assert target.read_bytes() == before


def test_protected_cloudflare_credentials_require_exact_modes(tmp_path):
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    path = private / "sonnet_cloudflare.json"
    path.write_text(json.dumps({
        "CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_SONNET_KV_NAMESPACE_ID": "namespace",
        "CLOUDFLARE_SONNET_API_TOKEN": "token",
    }), encoding="utf-8"); path.chmod(0o600)
    assert load_cloudflare_credentials(path) == ("account", "namespace", "token")
    path.chmod(0o644)
    with pytest.raises(PublicExportError, match="permissions"):
        load_cloudflare_credentials(path)


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
