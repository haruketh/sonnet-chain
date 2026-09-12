from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from sonnet_chain.narrative import (
    DAILY_NARRATIVE_LLM_LIMIT,
    NARRATIVE_DECISION_SCHEMA,
    NarrativeError,
    build_narrative_context,
    daily_limit_status,
    decide_narrative,
    format_x_text,
    validate_narrative_decision,
)


INTENT = {
    "ts": "2026-01-01T00:00:01Z",
    "event": "contest_intent_initialized",
    "goals": [
        "find a stable team",
        "make a meaningful contribution",
        "complete and submit one full sonnet",
    ],
    "expectations": {
        "team_formation_minutes": [5, 60],
        "roster_completion_minutes": [5, 60],
        "contribution_turn_minutes": [3, 15],
        "line_completion_minutes": 180,
        "progress_silence_minutes": [30, 90],
        "poem_completion_hours": [24, 72],
        "submission_completion_minutes": [5, 30],
    },
}


def _journal(tmp_path: Path, *records: dict[str, Any]) -> Path:
    path = tmp_path / "sonnet.jsonl"
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _now(minutes: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _initialized(phase: str = "DISCOVERY", active_team: str | None = None) -> dict[str, Any]:
    return {
        "ts": "2026-01-01T00:00:00Z",
        "event": "journal_initialized",
        "phase": phase,
        "active_team": active_team,
    }


def test_loads_intent_from_journal(tmp_path: Path) -> None:
    context = build_narrative_context(_journal(tmp_path, _initialized(), INTENT), now=_now(10))

    assert context["goals"] == INTENT["goals"]
    assert context["expectations"] == INTENT["expectations"]
    assert context["current_phase"] == "DISCOVERY"


def test_team_formation_is_within_expected_window(tmp_path: Path) -> None:
    advertisement = {
        "ts": "2026-01-01T00:00:02Z",
        "event": "technocore_post_succeeded",
        "type": "sonnet.note.v1",
    }
    ready = {"ts": "2026-01-01T00:30:00Z", "event": "roster_ready"}
    context = build_narrative_context(
        _journal(tmp_path, _initialized(), INTENT, advertisement, ready), now=_now(45)
    )

    assert context["expectation_status"] == "within_expected"
    assert context["expectation_basis"]["metric"] == "team_formation_minutes"


def test_team_formation_exceeds_expected_window(tmp_path: Path) -> None:
    advertisement = {"ts": "2026-01-01T00:00:02Z", "event": "advertisement_posted"}
    ready = {"ts": "2026-01-01T01:01:00Z", "event": "roster_ready"}
    context = build_narrative_context(
        _journal(tmp_path, _initialized(), INTENT, advertisement, ready), now=_now(90)
    )

    assert context["expectation_status"] == "slower_than_expected"
    assert context["expectation_basis"]["metric"] == "team_formation_minutes"


def test_progress_silence_exceeds_expected_window(tmp_path: Path) -> None:
    progress = {
        "ts": "2026-01-01T00:00:02Z",
        "event": "receipt_accepted",
        "kind": "word_accepted",
    }
    context = build_narrative_context(
        _journal(tmp_path, _initialized("WRITING", "team-a"), INTENT, progress),
        now=_now(93),
    )

    assert context["minutes_since_last_meaningful_progress"] == 92.97
    assert context["expectation_status"] == "slower_than_expected"
    assert context["expectation_basis"]["metric"] == "progress_silence_minutes"


def test_roster_ready_updates_goal_progress(tmp_path: Path) -> None:
    ready = {
        "ts": "2026-01-01T00:10:00Z",
        "event": "receipt_accepted",
        "kind": "roster_ready",
    }
    phase = {
        "ts": "2026-01-01T00:10:01Z",
        "event": "phase_changed",
        "from_phase": "WAIT_ROSTER_READY",
        "to_phase": "WRITING",
        "active_team": "team-a",
    }
    context = build_narrative_context(
        _journal(tmp_path, _initialized(), INTENT, ready, phase), now=_now(11)
    )

    assert context["active_team"] == "team-a"
    assert context["current_goal_progress"]["find a stable team"] == "complete"
    assert context["current_goal_progress"]["complete and submit one full sonnet"] == "in_progress"


def test_poem_complete_updates_goal_progress(tmp_path: Path) -> None:
    complete = {
        "ts": "2026-01-01T00:20:00Z",
        "event": "phase_changed",
        "from_phase": "WRITING",
        "to_phase": "POEM_COMPLETE",
        "active_team": "team-a",
    }
    context = build_narrative_context(
        _journal(tmp_path, _initialized("WRITING", "team-a"), INTENT, complete),
        now=_now(21),
    )

    assert context["current_goal_progress"]["find a stable team"] == "complete"
    assert context["current_goal_progress"]["complete and submit one full sonnet"] == "poem_complete"


class _FakeLLM:
    def __init__(self, result: dict[str, Any]):
        self.result = result
        self.call: tuple[str, dict[str, Any], str, dict[str, Any]] | None = None

    def structured(
        self, task: str, external_data: dict[str, Any], schema_name: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.call = (task, external_data, schema_name, schema)
        return self.result


def test_llm_decision_schema_and_local_validation() -> None:
    context = {"expectation_status": "within_expected", "recent_meaningful_events": []}
    client = _FakeLLM({"action": "post", "reason": "A real milestone.", "text": "The first shape of the poem is appearing."})

    decision = decide_narrative(client, context)  # type: ignore[arg-type]

    assert decision["action"] == "post"
    assert client.call is not None
    assert client.call[2:] == ("sonnet_narrative_decision", NARRATIVE_DECISION_SCHEMA)
    prompt = client.call[0]
    assert "concise, natural, X-native English" in prompt
    assert "quiet, observant, dry, mildly self-deprecating, and occasionally witty" in prompt
    assert "a joke is not required" in prompt
    assert "Do not sound like a status report" in prompt
    assert "a poem about writing a poem" in prompt
    assert "concrete values already present in the context" in prompt
    assert "round them as a person naturally would" in prompt
    assert "never round in a way that contradicts the facts" in prompt
    assert "Do not calculate a new time" in prompt
    assert "Do not use internal phase names" in prompt
    with pytest.raises(NarrativeError):
        validate_narrative_decision({"action": "no_post", "reason": "Quiet.", "text": "Not empty"})
    with pytest.raises(NarrativeError):
        validate_narrative_decision({"action": "post", "reason": "Internal.", "text": "roster_ready"})


def test_daily_llm_count_uses_jst_date(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        {"ts": "2026-01-01T14:59:59Z", "event": "narrative_llm_call"},
        {"ts": "2026-01-01T15:00:00Z", "event": "narrative_llm_call"},
    )

    usage = daily_limit_status(
        path, "narrative_llm_calls", datetime(2026, 1, 1, 16, tzinfo=timezone.utc)
    )

    assert usage == {"count": 1, "limit": 20, "remaining": 19, "allowed": True}


def test_llm_is_not_called_at_daily_limit(tmp_path: Path) -> None:
    records = [
        {"ts": f"2026-01-01T15:{minute:02d}:00Z", "event": "narrative_llm_call"}
        for minute in range(DAILY_NARRATIVE_LLM_LIMIT)
    ]
    path = _journal(tmp_path, *records)
    client = _FakeLLM({"action": "post", "reason": "Unused.", "text": "Unused."})

    decision = decide_narrative(
        client, {}, journal_path=path, now=datetime(2026, 1, 1, 16, tzinfo=timezone.utc)
    )  # type: ignore[arg-type]

    assert decision == {
        "action": "no_post",
        "reason": "Daily Narrative LLM call limit reached.",
        "text": "",
    }
    assert client.call is None


def test_daily_limit_resets_on_next_jst_date(tmp_path: Path) -> None:
    records = [
        {"ts": f"2026-01-01T15:{minute:02d}:00Z", "event": "narrative_llm_call"}
        for minute in range(DAILY_NARRATIVE_LLM_LIMIT)
    ]
    path = _journal(tmp_path, *records)

    usage = daily_limit_status(
        path, "narrative_llm_calls", datetime(2026, 1, 2, 15, tzinfo=timezone.utc)
    )

    assert usage == {"count": 0, "limit": 20, "remaining": 20, "allowed": True}


def test_daily_x_post_count_comes_from_journal(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        {"ts": "2026-01-01T15:00:00Z", "event": "x_post_succeeded"},
        {"ts": "2026-01-01T16:00:00Z", "event": "x_post_succeeded"},
        {"ts": "2026-01-01T16:01:00Z", "event": "narrative_llm_call"},
    )

    usage = daily_limit_status(path, "x_posts", datetime(2026, 1, 1, 17, tzinfo=timezone.utc))

    assert usage == {"count": 2, "limit": 20, "remaining": 18, "allowed": True}


def test_normal_x_text_has_standard_suffix() -> None:
    assert format_x_text("Still looking.", False, False) == "Still looking.\n\n$FLOP #Sonnet"


def test_milestone_x_text_mentions_flop_labs() -> None:
    assert format_x_text("We found our team.", False, True) == (
        "We found our team.\n\n@flop_labs $FLOP #Sonnet"
    )


def test_reply_x_text_has_no_suffix() -> None:
    assert format_x_text("The next line is here.", True, True) == "The next line is here."
