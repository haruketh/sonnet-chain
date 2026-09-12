from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .journal import Journal
from .narrative import (
    DAILY_X_POST_LIMIT,
    daily_limit_status,
    format_x_text,
    is_milestone_context,
    read_journal,
)
from .publisher import weighted_length
from .x_oauth import XTokenManager

X_POST_URL = "https://api.x.com/2/tweets"
THREAD_KEYS = {"team_formation", "writing_progress", "poem_completion", "submission"}


class NarrativePublisherError(RuntimeError):
    pass


def classify_thread(context: dict[str, Any]) -> str:
    latest = context.get("latest_meaningful_event")
    event = latest.get("event") if isinstance(latest, dict) else None
    phase = context.get("current_phase")
    if phase in {"WAIT_SUBMISSION_RECEIPT", "DONE"}:
        return "submission"
    if event in {"submission_accepted", "submission_rejected", "contest_result"}:
        return "submission"
    if event in {"poem_complete", "poem_published"}:
        return "poem_completion"
    if event in {"word_proposed", "word_accepted", "line_completed", "stanza_completed"}:
        return "writing_progress"
    if event in {
        "advertisement_posted", "roster_candidate_seen", "roster_signed", "roster_ready"
    }:
        return "team_formation"
    if phase == "SUBMIT":
        return "submission"
    if phase in {
        "POEM_COMPLETE", "WAIT_PUBLISHER", "X_AUTH_REQUIRED", "PUBLISH_IF_FINAL_CONTRIBUTOR"
    }:
        return "poem_completion"
    if phase == "WRITING":
        return "writing_progress"
    return "team_formation"


def reconstruct_threads(journal_path: Path) -> dict[str, dict[str, str]]:
    threads: dict[str, dict[str, str]] = {}
    for record in read_journal(journal_path):
        if record["event"] != "x_post_succeeded":
            continue
        key, tweet_id = record.get("thread_key"), record.get("tweet_id")
        if key not in THREAD_KEYS or not isinstance(tweet_id, str) or not tweet_id:
            continue
        state = threads.setdefault(key, {})
        if record.get("is_reply") is False and "root_tweet_id" not in state:
            state["root_tweet_id"] = tweet_id
        state["latest_tweet_id"] = tweet_id
    return threads


def _normalized_body(text: str) -> str:
    without_suffix = re.sub(
        r"\s*(?:@flop_labs\s+)?\$FLOP\s+#Sonnet\s*$", "", text, flags=re.IGNORECASE
    )
    return " ".join(without_suffix.split()).casefold()


def _is_duplicate(journal_path: Path, body: str) -> bool:
    normalized = _normalized_body(body)
    return any(
        record["event"] == "x_post_succeeded"
        and isinstance(record.get("text"), str)
        and _normalized_body(record["text"]) == normalized
        for record in read_journal(journal_path)
    )


def plan_narrative_post(
    decision: dict[str, str],
    context: dict[str, Any],
    journal_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    thread_key = classify_thread(context)
    threads = reconstruct_threads(journal_path)
    thread = threads.get(thread_key, {})
    reply_to = thread.get("latest_tweet_id")
    is_reply = reply_to is not None
    body = decision.get("text", "")
    usage = daily_limit_status(journal_path, "x_posts", now)

    reason = None
    if decision.get("action") != "post":
        reason = "narrative decision is no_post"
    elif not body.strip():
        reason = "narrative text is empty"
    elif not usage["allowed"]:
        reason = "daily X post limit reached"
    elif _is_duplicate(journal_path, body):
        reason = "duplicate narrative text"

    formatted = ""
    if reason is None:
        formatted = format_x_text(body, is_reply, is_milestone_context(context))
        if weighted_length(formatted) > 280:
            reason = "formatted X text exceeds the character limit"
            formatted = ""
    return {
        "status": "ready" if reason is None else "skipped",
        "reason": reason,
        "thread_key": thread_key,
        "root_tweet_id": thread.get("root_tweet_id"),
        "is_reply": is_reply,
        "reply_to_tweet_id": reply_to,
        "text": formatted,
        "daily_x_usage": usage,
    }


def publish_narrative(
    decision: dict[str, str],
    context: dict[str, Any],
    journal_path: Path,
    *,
    live: bool = False,
    token_manager: XTokenManager | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    plan = plan_narrative_post(decision, context, journal_path, now)
    if plan["status"] == "skipped":
        if plan["reason"] == "daily X post limit reached":
            Journal(journal_path).append(
                "daily_limit_skipped", kind="x_posts", limit=DAILY_X_POST_LIMIT
            )
        return plan
    if not live:
        return {**plan, "status": "dry_run"}
    if token_manager is None or client is None:
        raise NarrativePublisherError("Sonnet X credentials are unavailable")

    try:
        access_token = token_manager.get_valid_access_token()
        token_manager.verify_identity(access_token)
        payload: dict[str, Any] = {"text": plan["text"]}
        if plan["is_reply"]:
            payload["reply"] = {"in_reply_to_tweet_id": plan["reply_to_tweet_id"]}
        response = client.post(
            X_POST_URL, headers={"Authorization": f"Bearer {access_token}"}, json=payload
        )
        if response.status_code >= 400:
            raise NarrativePublisherError(f"X rejected the post with HTTP {response.status_code}")
        tweet_id = response.json()["data"]["id"]
        if not isinstance(tweet_id, str) or not tweet_id:
            raise NarrativePublisherError("X response did not contain a tweet ID")
    except NarrativePublisherError:
        raise
    except (OSError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise NarrativePublisherError("X post failed or response was invalid") from exc

    Journal(journal_path).append(
        "x_post_succeeded",
        tweet_id=tweet_id,
        thread_key=plan["thread_key"],
        is_reply=plan["is_reply"],
        text=plan["text"],
    )
    return {
        **plan,
        "status": "posted",
        "tweet_id": tweet_id,
        "daily_x_usage": daily_limit_status(journal_path, "x_posts"),
    }
