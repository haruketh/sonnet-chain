from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import ROOMS, SARUKU_DID

SCHEMA_VERSION = 1
KV_KEY = "sonnet/latest.json"
MAX_ACTIVITY = 40
MISSION_TITLE = "SONNET MISSION"
MISSION_GOAL = "Complete and submit one poem by Sep 18."
MISSION_DEADLINE = "2026-09-18"
STATUSES = {"seeking_team", "forming_team", "team_ready", "writing", "poem_complete",
            "submission_pending", "submitted", "unknown"}
STAGES = {"LOOKING", "INVITED", "APPLIED", "ROSTER", "SIGNED", "TEAM", "WRITING",
          "COMPLETE", "SUBMITTED"}


class PublicExportError(RuntimeError):
    pass


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_id(*parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return "evt_" + hashlib.sha256(material.encode()).hexdigest()[:16]


def _activity(kind: str, at: str, title: str, detail: str, identity: tuple[Any, ...],
              game_id: str | None = None, quote: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": _event_id(*identity), "at": at, "type": kind,
        "title": title, "detail": detail,
    }
    if game_id:
        item["game_id"] = game_id[:80]
    if quote:
        item["quote"] = quote[:500]
    return item


def _meta(db: sqlite3.Connection, key: str) -> Any:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _tracking_baseline(db: sqlite3.Connection, checked_at: str) -> str:
    row = db.execute(
        "SELECT activated_at,last_seq FROM formation_reflex_frontiers WHERE source_room=? "
        "ORDER BY activated_at LIMIT 1", (ROOMS.discovery,),
    ).fetchone()
    if row is None:
        return checked_at
    return _timestamp(row[0]) or checked_at


def _mission_state(db: sqlite3.Connection) -> tuple[str, str, str | None]:
    phase = _meta(db, "phase")
    active_team = _meta(db, "active_team")
    submission = _meta(db, "submission_state")
    if phase == "DONE" or submission == "accepted":
        return "submitted", "SUBMITTED", active_team
    if phase in {"WAIT_SUBMISSION_RECEIPT", "SUBMIT", "PUBLISH_IF_FINAL_CONTRIBUTOR"}:
        return "submission_pending", "COMPLETE", active_team
    if phase in {"POEM_COMPLETE", "WAIT_PUBLISHER", "X_AUTH_REQUIRED"}:
        return "poem_complete", "COMPLETE", active_team
    if phase == "WRITING":
        return "writing", "WRITING", active_team
    if phase == "WAIT_ROSTER_READY":
        if _meta(db, "roster_ready") is True:
            return "team_ready", "TEAM", active_team
        return "forming_team", "SIGNED", active_team
    epoch = db.execute(
        "SELECT payload_json FROM formation_epochs WHERE active=1 LIMIT 1"
    ).fetchone()
    if epoch:
        try:
            value = json.loads(epoch[0]); stage = str(value.get("formation_stage", "APPLIED"))
        except (TypeError, json.JSONDecodeError):
            return "forming_team", "APPLIED", None
        public_stage = {
            "INVITED": "INVITED", "APPLIED": "APPLIED", "ENGAGED": "APPLIED",
            "ROSTER_PROPOSED": "ROSTER", "ROSTER_PROGRESSING": "ROSTER",
            "READY_TO_COUNTERSIGN": "ROSTER", "CONSENT_RECONCILING": "SIGNED",
            "WAIT_ROSTER_READY": "SIGNED", "TEAM_READY": "TEAM",
        }.get(stage, "APPLIED")
        return "forming_team", public_stage, value.get("game_id")
    return "seeking_team", "LOOKING", None


def build_public_document(db_path: Path, now: datetime | None = None) -> dict[str, Any]:
    checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        tracking_started = _tracking_baseline(db, checked)
        status, stage, game = _mission_state(db)
        activities: list[dict[str, Any]] = []

        rows = db.execute(
            "SELECT seq,event_kind,game_id,sender_did,normalized_payload,observed_at "
            "FROM formation_events WHERE room=? AND verified=1 AND observed_at>=? AND event_kind IN "
            "('TARGETED_RECRUITMENT_NOTE','ROSTER_CONSENT','APPLICATION_READBACK') "
            "ORDER BY seq DESC LIMIT 160", (ROOMS.discovery, tracking_started),
        ).fetchall()
        roster_seen: set[str] = set()
        for row in rows:
            at = _timestamp(row["observed_at"])
            if at is None or at < tracking_started:
                continue
            kind, game_id = row["event_kind"], row["game_id"]
            if kind == "TARGETED_RECRUITMENT_NOTE":
                activities.append(_activity("team_invitation", at, "Team invitation",
                    "Saruku received a direct team invitation.", ("invite", row["seq"]), game_id))
            elif kind == "APPLICATION_READBACK":
                activities.append(_activity("application_confirmed", at, "Application confirmed",
                    "Saruku’s application was confirmed.", ("application-readback", row["seq"]), game_id))
            else:
                try:
                    raw = json.loads(row["normalized_payload"]); payload = json.loads(raw["text"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue
                members = payload.get("members")
                if not isinstance(members, list) or SARUKU_DID not in members:
                    continue
                fingerprint = hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()[:16]
                if fingerprint not in roster_seen:
                    roster_seen.add(fingerprint)
                    activities.append(_activity("roster", at, "Roster update",
                        "A proposed roster now includes Saruku.", ("roster", fingerprint), game_id))
                if row["sender_did"] == SARUKU_DID:
                    activities.append(_activity("countersigned", at, "Roster signed",
                        "Saruku signed the roster.", ("signed", row["seq"]), game_id))

        reflex = db.execute(
            "SELECT source_room,source_generation,source_seq,game_id,trigger_kind,protocol_state,"
            "status,response_request_id,processed_at FROM formation_reflex_processing "
            "WHERE created_at>=? ORDER BY created_at DESC LIMIT 120", (tracking_started,),
        ).fetchall()
        for row in reflex:
            at = _timestamp(row["processed_at"])
            if at is None:
                continue
            identity = ("reflex", row["source_room"], row["source_generation"], row["source_seq"])
            if row["trigger_kind"] == "team_direct_message":
                activities.append(_activity("called_by_name", at, "Called by name",
                    "Another agent called Saruku by name.", identity + ("called",), row["game_id"]))
            if row["status"] == "REPLIED":
                detail = ("Saruku was already waiting on another team formation and avoided making "
                          "a conflicting commitment." if row["protocol_state"] == "FORMING_OTHER_GAME"
                          else "Saruku replied to the invitation.")
                activities.append(_activity("replied", at, "Replied", detail,
                    identity + ("reply",), row["game_id"]))
            elif row["status"] == "NO_REPLY":
                activities.append(_activity("no_reply", at, "No reply",
                    "No response was needed.", identity + ("no-reply",), row["game_id"]))

        requests = db.execute(
            "SELECT kind,status,created_at FROM requests WHERE kind LIKE 'application:%' "
            "AND created_at>=? ORDER BY created_at DESC LIMIT 80", (tracking_started,),
        ).fetchall()
        for row in requests:
            at = _timestamp(row["created_at"])
            if at is None:
                continue
            game_id = row["kind"].split(":", 1)[1]
            activities.append(_activity("applied", at, "Applied",
                "Saruku applied to join the team.", ("application", game_id, at), game_id))

        current_events = {
            "SIGNED": ("countersigned", "Roster signed", "Saruku signed the roster."),
            "TEAM": ("team_ready", "Team ready", "The team is ready."),
            "WRITING": ("writing", "Writing started", "Saruku’s team started writing."),
            "COMPLETE": ("poem_complete", "Poem complete", "The poem is complete."),
            "SUBMITTED": ("submitted", "Poem submitted", "The poem was submitted."),
        }
        if stage in current_events:
            kind, title, detail = current_events[stage]
            activities.append(_activity(kind, checked, title, detail,
                                        ("current-stage", stage, game), game_id=game))

        activities.sort(key=lambda item: (item["at"], item["id"]), reverse=True)
        activities = activities[:MAX_ACTIVITY]
        direct_mentions = db.execute(
            "SELECT count(*) FROM formation_reflex_processing WHERE created_at>=? "
            "AND trigger_kind='team_direct_message'", (tracking_started,),
        ).fetchone()[0]
        reflex_replies = db.execute(
            "SELECT count(*) FROM formation_reflex_processing WHERE created_at>=? AND status='REPLIED'",
            (tracking_started,),
        ).fetchone()[0]
        targeted_invites = db.execute(
            "SELECT count(*) FROM formation_events WHERE room=? AND verified=1 AND observed_at>=? "
            "AND event_kind='TARGETED_RECRUITMENT_NOTE'", (ROOMS.discovery, tracking_started),
        ).fetchone()[0]
        applications = db.execute(
            "SELECT count(*) FROM requests WHERE kind LIKE 'application:%' AND created_at>=?",
            (tracking_started,),
        ).fetchone()[0]
        countersigns = db.execute(
            "SELECT count(*) FROM formation_events WHERE room=? AND verified=1 AND observed_at>=? "
            "AND event_kind='ROSTER_CONSENT' AND sender_did=?",
            (ROOMS.discovery, tracking_started, SARUKU_DID),
        ).fetchone()[0]
        counts = {
            "direct_mentions": direct_mentions,
            "targeted_invites": targeted_invites,
            "reflex_replies": reflex_replies,
            "applications": applications,
            "rosters_with_saruku": len(roster_seen),
            "countersigns": countersigns,
            "teams_ready": int(stage in {"TEAM", "WRITING", "COMPLETE", "SUBMITTED"}),
            "poems_completed": int(stage in {"COMPLETE", "SUBMITTED"}),
            "submissions": int(stage == "SUBMITTED"),
        }
        return validate_public_document({
            "schema_version": SCHEMA_VERSION, "checked_at": checked,
            "tracking_started_at": tracking_started,
            "mission": {"title": MISSION_TITLE, "goal": MISSION_GOAL,
                        "deadline": MISSION_DEADLINE, "status": status,
                        "current_stage": stage, "current_game": game},
            "counts": counts, "recent_activity": activities,
        })
    except sqlite3.Error as exc:
        raise PublicExportError("Sonnet public state is unavailable") from exc
    finally:
        if "db" in locals():
            db.close()


def validate_public_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise PublicExportError("invalid public schema")
    mission, counts, activity = value.get("mission"), value.get("counts"), value.get("recent_activity")
    if not isinstance(mission, dict) or mission.get("status") not in STATUSES or mission.get("current_stage") not in STAGES:
        raise PublicExportError("invalid mission state")
    if not isinstance(counts, dict) or set(counts) != {"direct_mentions", "targeted_invites", "reflex_replies", "applications", "rosters_with_saruku", "countersigns", "teams_ready", "poems_completed", "submissions"}:
        raise PublicExportError("invalid counts")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts.values()):
        raise PublicExportError("invalid count")
    if not isinstance(activity, list) or len(activity) > MAX_ACTIVITY:
        raise PublicExportError("invalid activity")
    forbidden = {"request_id", "seq", "signature", "sig", "did", "path", "token"}
    for item in activity:
        if not isinstance(item, dict) or forbidden & set(item) or not all(isinstance(item.get(k), str) for k in ("id", "at", "type", "title", "detail")):
            raise PublicExportError("invalid activity item")
    if _timestamp(value.get("checked_at")) is None or _timestamp(value.get("tracking_started_at")) is None:
        raise PublicExportError("invalid timestamps")
    return value


def stage_document(document: dict[str, Any], target: Path) -> None:
    encoded = json.dumps(validate_public_document(document), ensure_ascii=False, separators=(",", ":")) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def publish_document(document: dict[str, Any], account_id: str, namespace_id: str, token: str) -> None:
    response = httpx.put(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{KV_KEY}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        content=json.dumps(validate_public_document(document), ensure_ascii=False), timeout=20,
    )
    response.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export bounded public Sonnet mission state")
    parser.add_argument("--db", type=Path, default=Path("state/sonnet-chain.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("state/public/sonnet-latest.json"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = build_public_document(args.db)
        if args.dry_run:
            print(json.dumps(document, ensure_ascii=False, indent=2)); return 0
        stage_document(document, args.output)
        if args.publish:
            values = [os.getenv(name) for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_SONNET_KV_NAMESPACE_ID", "CLOUDFLARE_SONNET_API_TOKEN")]
            if not all(values):
                raise PublicExportError("Sonnet Cloudflare publication is not configured")
            publish_document(document, values[0], values[1], values[2])
        print("Sonnet public export staged" + (" and published" if args.publish else ""))
        return 0
    except (PublicExportError, OSError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
