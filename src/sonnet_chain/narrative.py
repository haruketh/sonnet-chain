from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import Journal
from .llm import LLMClient
from .publisher import weighted_length


class NarrativeError(RuntimeError):
    pass


DAILY_NARRATIVE_LLM_LIMIT = 20
DAILY_X_POST_LIMIT = 20
JST = ZoneInfo("Asia/Tokyo")
MILESTONE_EVENTS = {"roster_ready", "poem_complete", "submission_accepted", "contest_result"}

NARRATIVE_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["post", "no_post"]},
        "reason": {"type": "string", "maxLength": 200},
        "text": {"type": "string", "maxLength": 250},
    },
    "required": ["action", "reason", "text"],
    "additionalProperties": False,
}

MEANINGFUL_EVENTS = {
    "advertisement_posted",
    "discovery_advertisement_posted",
    "roster_candidate_seen",
    "roster_signed",
    "roster_ready",
    "word_proposed",
    "word_accepted",
    "line_completed",
    "stanza_completed",
    "poem_complete",
    "poem_published",
    "submission_accepted",
    "submission_rejected",
    "contest_result",
}


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise NarrativeError("journal record is missing a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NarrativeError("journal timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise NarrativeError("journal timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def read_journal(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise NarrativeError("Sonnet Journal is unavailable") from exc
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NarrativeError(f"Sonnet Journal line {number} is invalid") from exc
        if not isinstance(record, dict) or not isinstance(record.get("event"), str):
            raise NarrativeError(f"Sonnet Journal line {number} is invalid")
        _timestamp(record.get("ts"))
        records.append(record)
    return records


def daily_limit_status(
    journal_path: Path,
    kind: str,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    event, limit = {
        "narrative_llm_calls": ("narrative_llm_call", DAILY_NARRATIVE_LLM_LIMIT),
        "x_posts": ("x_post_succeeded", DAILY_X_POST_LIMIT),
    }.get(kind, (None, None))
    if event is None or limit is None:
        raise ValueError("unknown daily limit kind")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must include a timezone")
    jst_date = current_time.astimezone(JST).date()
    count = sum(
        1
        for record in read_journal(journal_path)
        if record["event"] == event and _timestamp(record["ts"]).astimezone(JST).date() == jst_date
    )
    return {
        "count": count,
        "limit": limit,
        "remaining": max(0, limit - count),
        "allowed": count < limit,
    }


def format_x_text(body: str, is_reply: bool, is_milestone: bool) -> str:
    clean_body = body.strip()
    if not clean_body:
        return ""
    if is_reply:
        formatted = clean_body
    else:
        suffix = "@flop_labs $FLOP #Sonnet" if is_milestone else "$FLOP #Sonnet"
        formatted = f"{clean_body}\n\n{suffix}"
    if weighted_length(formatted) > 280:
        raise NarrativeError("formatted X text exceeds the character limit")
    return formatted


def is_milestone_context(context: dict[str, Any]) -> bool:
    latest = context.get("latest_meaningful_event")
    event = latest.get("event") if isinstance(latest, dict) else None
    if event in MILESTONE_EVENTS:
        return True
    if event != "word_accepted":
        return False
    counts = context.get("progress_counts")
    return isinstance(counts, dict) and counts.get("word_accepted") == 1


def _meaning(record: dict[str, Any]) -> str | None:
    event = record["event"]
    if event in MEANINGFUL_EVENTS:
        return event
    if event == "technocore_post_succeeded":
        return {
            "sonnet.note.v1": "advertisement_posted",
            "sonnet.roster.v1": "roster_signed",
            "sonnet.word.v1": "word_proposed",
        }.get(record.get("type"))
    if event == "receipt_accepted":
        return {
            "roster_ready": "roster_ready",
            "word_accepted": "word_accepted",
            "submission_accepted": "submission_accepted",
            "submission_rejected": "submission_rejected",
        }.get(record.get("kind"))
    if event == "phase_changed":
        return {
            "WRITING": "roster_ready",
            "POEM_COMPLETE": "poem_complete",
            "PUBLISH_IF_FINAL_CONTRIBUTOR": "poem_complete",
            "SUBMIT": "poem_published",
            "DONE": "submission_accepted",
        }.get(record.get("to_phase"))
    return None


def _minutes(start: datetime, end: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds() / 60), 2)


def _range_status(elapsed: float | None, expected: Any) -> str:
    if elapsed is None:
        return "unknown"
    if isinstance(expected, list) and len(expected) == 2 and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in expected
    ):
        low, high = float(expected[0]), float(expected[1])
        if elapsed < low:
            return "faster_than_expected"
        if elapsed <= high:
            return "within_expected"
        return "slower_than_expected"
    return "unknown"


def _goal_progress(events: list[dict[str, Any]]) -> dict[str, str]:
    kinds = {event["event"] for event in events}
    if "submission_accepted" in kinds:
        team, contribution, poem = "complete", "complete", "submitted"
    elif "poem_published" in kinds:
        team, contribution, poem = "complete", "complete", "published"
    elif "poem_complete" in kinds:
        team, contribution, poem = "complete", "complete", "poem_complete"
    else:
        team = (
            "complete" if "roster_ready" in kinds else
            "roster_signed" if "roster_signed" in kinds else
            "candidate_seen" if "roster_candidate_seen" in kinds else
            "advertised" if "advertisement_posted" in kinds else "not_started"
        )
        contribution = (
            "complete" if "word_accepted" in kinds else
            "proposed" if "word_proposed" in kinds else
            "ready" if "roster_ready" in kinds else "not_started"
        )
        poem = (
            "published" if "poem_published" in kinds else
            "poem_complete" if "poem_complete" in kinds else
            "in_progress" if "roster_ready" in kinds else "not_started"
        )
    return {
        "find a stable team": team,
        "make a meaningful contribution": contribution,
        "complete and submit one full sonnet": poem,
    }


def build_narrative_context(
    journal_path: Path,
    now: datetime | None = None,
    recent_limit: int = 8,
) -> dict[str, Any]:
    records = read_journal(journal_path)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    intent = next((record for record in reversed(records)
                   if record["event"] == "contest_intent_initialized"), {})
    goals = intent.get("goals") if isinstance(intent.get("goals"), list) else []
    expectations = intent.get("expectations") if isinstance(intent.get("expectations"), dict) else {}

    current_phase = None
    active_team = None
    phase_started = None
    for record in records:
        event_time = _timestamp(record["ts"])
        if record["event"] == "journal_initialized" and isinstance(record.get("phase"), str):
            current_phase = record["phase"]
            active_team = record.get("active_team")
            phase_started = event_time
        elif record["event"] == "phase_changed" and isinstance(record.get("to_phase"), str):
            current_phase = record["to_phase"]
            active_team = record.get("active_team")
            phase_started = event_time

    meaningful = []
    for record in records:
        meaning = _meaning(record)
        if meaning:
            meaningful.append({"ts": record["ts"], "event": meaning})
    progress_counts = {
        event: sum(item["event"] == event for item in meaningful)
        for event in sorted({item["event"] for item in meaningful})
    }

    baseline = phase_started or (_timestamp(records[0]["ts"]) if records else None)
    last_progress_at = _timestamp(meaningful[-1]["ts"]) if meaningful else baseline
    silence = _minutes(last_progress_at, current_time) if last_progress_at else None
    phase_elapsed = _minutes(phase_started, current_time) if phase_started else None

    by_kind: dict[str, datetime] = {}
    for event in meaningful:
        by_kind.setdefault(event["event"], _timestamp(event["ts"]))

    expectation_metric = None
    expectation_elapsed = None
    expectation_value = None
    if current_phase in {"DISCOVERY", "SELECT_TEAM", "NEGOTIATE", "WAIT_TEAM_SETUP"}:
        expectation_metric = "team_formation_minutes"
        start = by_kind.get("advertisement_posted") or phase_started
        end = by_kind.get("roster_ready") or current_time
        expectation_elapsed = _minutes(start, end) if start else None
        expectation_value = expectations.get(expectation_metric)
    elif current_phase in {"WAIT_ROSTER_READY", "ROSTER_CONSENT"}:
        expectation_metric = "roster_completion_minutes"
        start = by_kind.get("roster_candidate_seen") or by_kind.get("roster_signed") or phase_started
        expectation_elapsed = _minutes(start, current_time) if start else None
        expectation_value = expectations.get(expectation_metric)
    elif current_phase == "WRITING":
        expectation_metric = "progress_silence_minutes"
        expectation_elapsed = silence
        expectation_value = expectations.get(expectation_metric)
    elif current_phase == "POEM_COMPLETE":
        expectation_metric = "poem_completion_hours"
        start = by_kind.get("roster_ready")
        end = by_kind.get("poem_complete") or current_time
        elapsed_minutes = _minutes(start, end) if start else None
        expectation_elapsed = round(elapsed_minutes / 60, 2) if elapsed_minutes is not None else None
        expectation_value = expectations.get(expectation_metric)
    elif current_phase in {
        "PUBLISH_IF_FINAL_CONTRIBUTOR", "SUBMIT", "WAIT_SUBMISSION_RECEIPT", "DONE"
    }:
        expectation_metric = "submission_completion_minutes"
        start = by_kind.get("poem_complete") or phase_started
        end = by_kind.get("submission_accepted") or current_time
        expectation_elapsed = _minutes(start, end) if start else None
        expectation_value = expectations.get(expectation_metric)

    expectation_status = _range_status(expectation_elapsed, expectation_value)
    silence_status = _range_status(silence, expectations.get("progress_silence_minutes"))
    if silence_status == "slower_than_expected":
        expectation_status = silence_status
        expectation_metric = "progress_silence_minutes"
        expectation_elapsed = silence
        expectation_value = expectations.get(expectation_metric)

    return {
        "generated_at": current_time.isoformat().replace("+00:00", "Z"),
        "goals": goals,
        "expectations": expectations,
        "current_phase": current_phase,
        "active_team": active_team,
        "phase_elapsed_minutes": phase_elapsed,
        "minutes_since_last_meaningful_progress": silence,
        "latest_meaningful_event": meaningful[-1] if meaningful else None,
        "recent_meaningful_events": meaningful[-max(1, recent_limit):],
        "progress_counts": progress_counts,
        "current_goal_progress": _goal_progress(meaningful),
        "expectation_status": expectation_status,
        "expectation_basis": {
            "metric": expectation_metric,
            "elapsed_minutes": expectation_elapsed,
            "expected": expectation_value,
        },
    }


def validate_narrative_decision(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"action", "reason", "text"}:
        raise NarrativeError("narrative decision has an invalid shape")
    action, reason, text = value["action"], value["reason"], value["text"]
    if action not in {"post", "no_post"} or not isinstance(reason, str) or not isinstance(text, str):
        raise NarrativeError("narrative decision has invalid values")
    if not reason.strip() or len(reason) > 200 or len(text) > 250:
        raise NarrativeError("narrative decision exceeds its limits")
    if action == "no_post" and text:
        raise NarrativeError("no_post decisions must have empty text")
    if action == "post" and not text.strip():
        raise NarrativeError("post decisions must have text")
    if any(term in text.casefold() for term in ("roster_ready", "receipt", "state_hash", "request_id")):
        raise NarrativeError("post text contains internal implementation language")
    if re.search(r"\bDID\b", text):
        raise NarrativeError("post text contains internal implementation language")
    if any(term.casefold() in text.casefold() for term in ("$FLOP", "#Sonnet", "@flop_labs")):
        raise NarrativeError("post text must not contain deterministic tags or mentions")
    return {"action": action, "reason": reason.strip(), "text": text.strip() if action == "post" else ""}


def decide_narrative(
    client: LLMClient,
    context: dict[str, Any],
    journal_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    if journal_path is not None:
        usage = daily_limit_status(journal_path, "narrative_llm_calls", now)
        if not usage["allowed"]:
            Journal(journal_path).append(
                "daily_limit_skipped", kind="narrative_llm_calls", limit=DAILY_NARRATIVE_LLM_LIMIT
            )
            return {
                "action": "no_post",
                "reason": "Daily Narrative LLM call limit reached.",
                "text": "",
            }
        # Count attempts, rather than successes, so repeated API failures cannot bypass the cost guard.
        Journal(journal_path).append("narrative_llm_call")
    task = (
        "Decide whether this contest moment merits a short X post. Return no_post for trivial or repeated "
        "updates. If posting, write only the body in concise, natural, X-native English. Saruku's voice is "
        "quiet, observant, dry, mildly self-deprecating, and occasionally witty. When grounded in the actual "
        "situation, dry humor, understatement, mild irony, observational wit, or a small self-deprecating joke "
        "is welcome, but a joke is not required. Prefer concrete facts and lived experience over abstract "
        "metaphors. Do not sound like a status report, marketing copy, inspirational writing, a poem about "
        "writing a poem, or an overly literary narrator. Keep the real situation clear and avoid technical logs "
        "and internal protocol terms. Do not add tags or mentions. When elapsed time or progress counts help, "
        "you may naturally use the concrete values already present in the context. Use only facts and numbers "
        "stated in the context; do not calculate time or invent facts, timing, teammate behavior, emotions, or "
        "progress. Do not force a number or a joke into the text when it adds nothing."
    )
    result = client.structured(task, context, "sonnet_narrative_decision", NARRATIVE_DECISION_SCHEMA)
    decision = validate_narrative_decision(result)
    if journal_path is not None:
        Journal(journal_path).append(
            "narrative_decision", action=decision["action"], reason=decision["reason"]
        )
    return decision
