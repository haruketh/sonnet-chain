from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sonnet_chain.narrative import read_journal
from sonnet_chain.narrative_publisher import (
    NarrativePublisherError,
    classify_thread,
    plan_narrative_post,
    publish_narrative,
    reconstruct_threads,
)


def _journal(tmp_path: Path, *records: dict[str, Any]) -> Path:
    path = tmp_path / "sonnet.jsonl"
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _context(phase: str, event: str, count: int = 1) -> dict[str, Any]:
    return {
        "current_phase": phase,
        "latest_meaningful_event": {"ts": "2026-01-01T00:00:00Z", "event": event},
        "recent_meaningful_events": [
            {"ts": "2026-01-01T00:00:00Z", "event": event}
        ],
        "progress_counts": {event: count},
    }


def _decision(text: str = "Still looking for a team.") -> dict[str, str]:
    return {"action": "post", "reason": "Worth sharing.", "text": text}


def _success(
    tweet_id: str, thread_key: str, text: str, is_reply: bool = False
) -> dict[str, Any]:
    return {
        "ts": "2026-01-01T00:00:00Z",
        "event": "x_post_succeeded",
        "tweet_id": tweet_id,
        "thread_key": thread_key,
        "is_reply": is_reply,
        "text": text,
    }


def test_team_formation_creates_root(tmp_path: Path) -> None:
    plan = plan_narrative_post(
        _decision(), _context("DISCOVERY", "advertisement_posted"), _journal(tmp_path)
    )

    assert plan["thread_key"] == "team_formation"
    assert plan["is_reply"] is False
    assert plan["reply_to_tweet_id"] is None
    assert plan["text"].endswith("$FLOP #Sonnet")


def test_second_post_in_thread_replies_to_latest(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        _success("root-1", "team_formation", "First update.\n\n$FLOP #Sonnet"),
        _success("reply-2", "team_formation", "Second update.", True),
    )

    plan = plan_narrative_post(
        _decision("Found a team."), _context("WRITING", "roster_ready"), path
    )

    assert plan["root_tweet_id"] == "root-1"
    assert plan["is_reply"] is True
    assert plan["reply_to_tweet_id"] == "reply-2"
    assert plan["text"] == "Found a team."


@pytest.mark.parametrize(
    ("phase", "event", "expected"),
    [
        ("WRITING", "word_accepted", "writing_progress"),
        ("POEM_COMPLETE", "poem_complete", "poem_completion"),
        ("DONE", "submission_accepted", "submission"),
    ],
)
def test_later_stages_use_separate_roots(
    tmp_path: Path, phase: str, event: str, expected: str
) -> None:
    path = _journal(
        tmp_path, _success("team-root", "team_formation", "Earlier.\n\n$FLOP #Sonnet")
    )

    plan = plan_narrative_post(_decision(f"Update for {expected}."), _context(phase, event), path)

    assert classify_thread(_context(phase, event)) == expected
    assert plan["thread_key"] == expected
    assert plan["is_reply"] is False


def test_milestone_root_has_mention_and_tags(tmp_path: Path) -> None:
    plan = plan_narrative_post(
        _decision("The poem is complete."),
        _context("POEM_COMPLETE", "poem_complete"),
        _journal(tmp_path),
    )

    assert plan["text"].endswith("@flop_labs $FLOP #Sonnet")


def test_duplicate_normalized_text_is_skipped(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        _success("root-1", "team_formation", "Still   looking.\n\n$FLOP #Sonnet"),
    )

    plan = plan_narrative_post(
        _decision(" still looking. "), _context("DISCOVERY", "advertisement_posted"), path
    )

    assert plan["status"] == "skipped"
    assert plan["reason"] == "duplicate narrative text"


def test_x_daily_limit_skips(tmp_path: Path) -> None:
    records = [
        {
            **_success(str(index), "team_formation", f"Update {index}."),
            "ts": f"2026-01-01T15:{index:02d}:00Z",
        }
        for index in range(20)
    ]
    path = _journal(tmp_path, *records)

    plan = plan_narrative_post(
        _decision("A new update."),
        _context("DISCOVERY", "advertisement_posted"),
        path,
        datetime(2026, 1, 1, 16, tzinfo=timezone.utc),
    )

    assert plan["status"] == "skipped"
    assert plan["reason"] == "daily X post limit reached"


class _TokenManager:
    def __init__(self) -> None:
        self.calls = 0

    def get_valid_access_token(self) -> str:
        self.calls += 1
        return "secret-access-token"

    def verify_identity(self, access_token: str) -> object:
        self.calls += 1
        assert access_token == "secret-access-token"
        return object()


class _Response:
    def __init__(self, status_code: int, tweet_id: str = "tweet-1") -> None:
        self.status_code = status_code
        self.tweet_id = tweet_id

    def json(self) -> dict[str, Any]:
        return {"data": {"id": self.tweet_id}}


class _Client:
    def __init__(self, status_code: int = 201) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.status_code)


def test_dry_run_does_not_call_x_client(tmp_path: Path) -> None:
    manager = _TokenManager()
    client = _Client()

    result = publish_narrative(
        _decision(), _context("DISCOVERY", "advertisement_posted"), _journal(tmp_path),
        live=False, token_manager=manager, client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "dry_run"
    assert manager.calls == 0
    assert client.calls == []


def test_live_success_is_the_only_time_success_event_is_written(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    manager = _TokenManager()
    client = _Client()

    result = publish_narrative(
        _decision(), _context("DISCOVERY", "advertisement_posted"), path,
        live=True, token_manager=manager, client=client,  # type: ignore[arg-type]
    )

    successes = [record for record in read_journal(path) if record["event"] == "x_post_succeeded"]
    assert result["status"] == "posted"
    assert result["tweet_id"] == "tweet-1"
    assert len(successes) == 1
    assert successes[0]["is_reply"] is False


def test_live_failure_does_not_write_success_event(tmp_path: Path) -> None:
    path = _journal(tmp_path)

    with pytest.raises(NarrativePublisherError, match="HTTP 500"):
        publish_narrative(
            _decision(), _context("DISCOVERY", "advertisement_posted"), path,
            live=True, token_manager=_TokenManager(), client=_Client(500),  # type: ignore[arg-type]
        )

    assert not any(record["event"] == "x_post_succeeded" for record in read_journal(path))


def test_reconstruct_threads_keeps_root_and_latest(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        _success("root", "writing_progress", "Started.\n\n$FLOP #Sonnet"),
        _success("reply", "writing_progress", "A new line.", True),
    )

    assert reconstruct_threads(path)["writing_progress"] == {
        "root_tweet_id": "root",
        "latest_tweet_id": "reply",
    }
