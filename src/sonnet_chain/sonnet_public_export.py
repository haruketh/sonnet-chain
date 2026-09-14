from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import CONTEST_ID, ROOMS, SARUKU_DID

SCHEMA_VERSION = 2
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


def _tracked_generations(db: sqlite3.Connection) -> list[int]:
    return [int(row[0]) for row in db.execute(
        "SELECT generation FROM formation_history_state WHERE room=? UNION "
        "SELECT source_generation FROM formation_reflex_frontiers WHERE source_room=? "
        "ORDER BY 1", (ROOMS.discovery, ROOMS.discovery),
    ).fetchall()]


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


def _aliases(roster: Any) -> tuple[dict[str, str], list[str]]:
    if not isinstance(roster, list) or not 1 <= len(roster) <= 8:
        return {}, []
    if len(set(roster)) != len(roster) or not all(isinstance(item, str) for item in roster):
        return {}, []
    aliases: dict[str, str] = {}
    next_alias = 0
    for did in roster:
        if did == SARUKU_DID:
            aliases[did] = "Saruku"
        else:
            aliases[did] = f"Teammate {chr(ord('A') + next_alias)}"
            next_alias += 1
    return aliases, [aliases[did] for did in roster]


def _public_note(raw: dict[str, Any]) -> str | None:
    text = raw.get("text")
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        public = text[:500] if text.strip() else None
        return None if public and "did:key:" in public else public
    if (
        not isinstance(payload, dict) or payload.get("type") != "sonnet.note.v1"
        or payload.get("contest_id") != CONTEST_ID
    ):
        return None
    public = payload.get("text")
    public = public[:500] if isinstance(public, str) and public.strip() else None
    return None if public and "did:key:" in public else public


def _writing_state(
    db: sqlite3.Connection, status: str, game: str | None,
) -> dict[str, Any] | None:
    if status not in {"writing", "poem_complete", "submission_pending", "submitted"}:
        return None
    setup = _meta(db, "team_setup")
    roster = _meta(db, "current_roster")
    if not isinstance(game, str) or not isinstance(setup, dict) or setup.get("game_id") != game:
        return None
    room, generation = setup.get("poem_room"), setup.get("room_generation")
    if not isinstance(room, str) or room != ROOMS.team(game):
        return None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        return None
    aliases, team = _aliases(roster)
    if not team or SARUKU_DID not in aliases:
        return None
    lines = _meta(db, "poem_lines")
    version = _meta(db, "poem_version")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        lines = []
    lines = [line[:1000] for line in lines[:14]]
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        version = 0
    previous = _meta(db, "previous_contributor")
    previous_alias = aliases.get(previous) if isinstance(previous, str) else None
    activity: list[dict[str, Any]] = []

    # The primary key (room,generation,seq) makes this a bounded tail read.
    source_rows = db.execute(
        "SELECT seq,sender_did,event_type,payload FROM team_source_events "
        "WHERE room=? AND generation=? AND verified=1 ORDER BY seq DESC LIMIT 200",
        (room, generation),
    ).fetchall()
    for row in source_rows:
        actor = aliases.get(row["sender_did"])
        if actor is None:
            continue
        try:
            raw = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        at = _timestamp(raw.get("created_at", raw.get("timestamp", raw.get("ts"))))
        if at is None:
            continue
        try:
            payload = json.loads(raw.get("text", ""))
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("type") == "sonnet.word.v1":
            if (
                payload.get("contest_id") != CONTEST_ID or payload.get("game_id") != game
                or payload.get("room_generation") != generation
            ):
                continue
            word = payload.get("word")
            if isinstance(word, str) and word.strip():
                activity.append(_activity(
                    "word_proposed", at, "Word proposed", f"{actor} proposed a word.",
                    ("writing-proposal", room, generation, row["seq"]), game, word[:100],
                ))
            continue
        note = _public_note(raw)
        if note is not None:
            activity.append(_activity(
                "message", at, actor, "Team message.",
                ("writing-message", room, generation, row["seq"]), game, note,
            ))

    receipt_rows = db.execute(
        "SELECT r.seq,r.kind,r.payload,e.payload AS raw_payload FROM ("
        "SELECT room,generation,seq,kind,payload FROM receipts WHERE room=? AND generation=? "
        "AND accepted=1 ORDER BY seq DESC LIMIT 200) r "
        "JOIN events e ON e.room=r.room AND e.generation=r.generation AND e.seq=r.seq "
        "WHERE r.kind='word_accepted' ORDER BY r.seq DESC LIMIT 81",
        (room, generation),
    ).fetchall()
    accepted: list[tuple[int, str, dict[str, Any], str]] = []
    for row in receipt_rows:
        try:
            payload = json.loads(row["payload"]); raw = json.loads(row["raw_payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("game_id") != game:
            continue
        at = _timestamp(raw.get("created_at", raw.get("timestamp", raw.get("ts"))))
        if at is None:
            continue
        accepted.append((row["seq"], at, payload, row["kind"]))

    ready_rows = db.execute(
        "SELECT r.seq,r.payload,e.payload AS raw_payload FROM (SELECT room,generation,seq,kind,payload "
        "FROM receipts WHERE room=? AND accepted=1 ORDER BY generation DESC,seq DESC LIMIT 200) r "
        "JOIN events e "
        "ON e.room=r.room AND e.generation=r.generation AND e.seq=r.seq "
        "WHERE r.kind='roster_ready' ORDER BY r.generation DESC,r.seq DESC LIMIT 20",
        (ROOMS.discovery,),
    ).fetchall()
    for row in ready_rows:
        try:
            payload = json.loads(row["payload"]); raw = json.loads(row["raw_payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("game_id") != game:
            continue
        at = _timestamp(raw.get("created_at", raw.get("timestamp", raw.get("ts"))))
        if at is not None:
            activity.append(_activity(
                "writing_started", at, "Writing started", "The team began writing.",
                ("writing-started", ROOMS.discovery, row["seq"], game), game,
            ))
            break

    accepted.sort(key=lambda item: item[0])
    prior_lines = 0
    if len(accepted) > 80:
        _, _, baseline, _ = accepted.pop(0)
        baseline_lines = baseline.get("lines")
        if isinstance(baseline_lines, list) and all(
            isinstance(line, str) for line in baseline_lines
        ):
            prior_lines = len(baseline_lines)
    for seq, at, payload, _ in accepted:
        contributor = aliases.get(payload.get("contributor_did"))
        word = payload.get("word")
        detail = f"{contributor} contributed an accepted word." if contributor else "A word was accepted."
        activity.append(_activity(
            "word_accepted", at, "Word accepted", detail,
            ("writing-accepted", room, generation, seq), game,
            word[:100] if isinstance(word, str) and word.strip() else None,
        ))
        accepted_lines = payload.get("lines")
        line_count = len(accepted_lines) if isinstance(accepted_lines, list) else prior_lines
        if line_count > prior_lines:
            for completed in range(prior_lines + 1, line_count + 1):
                activity.append(_activity(
                    "line_completed", at, "Line completed", f"Line {completed} was completed.",
                    ("writing-line", room, generation, seq, completed), game,
                ))
        prior_lines = max(prior_lines, line_count)
        if payload.get("complete") is True:
            activity.append(_activity(
                "poem_completed", at, "Poem completed", "The sonnet was completed.",
                ("writing-complete", room, generation, seq), game,
            ))
    activity.sort(key=lambda item: (item["at"], item["id"]), reverse=True)
    current_line = _meta(db, "line_number")
    line_number = (
        current_line if isinstance(current_line, int) and not isinstance(current_line, bool)
        and 0 <= current_line <= 14
        else min(14, len(lines) + (0 if len(lines) >= 14 else 1))
    )
    return {
        "version": version, "line_number": line_number, "lines": lines,
        "previous_contributor": previous_alias, "team": team,
        "activity": activity[:MAX_ACTIVITY],
    }


def build_public_document(db_path: Path, now: datetime | None = None) -> dict[str, Any]:
    checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        tracking_started = _tracking_baseline(db, checked)
        status, stage, game = _mission_state(db)
        activities: list[dict[str, Any]] = []

        invitation_rows: list[sqlite3.Row] = []
        application_rows: list[sqlite3.Row] = []
        roster_rows: list[sqlite3.Row] = []
        generations = _tracked_generations(db)
        for generation in generations:
            common = (ROOMS.discovery, generation, tracking_started)
            invitation_rows.extend(db.execute(
                "SELECT seq,event_kind,game_id,sender_did,normalized_payload,observed_at "
                "FROM formation_events WHERE room=? AND generation=? AND verified=1 "
                "AND julianday(observed_at)>=julianday(?) AND event_kind='TARGETED_RECRUITMENT_NOTE' "
                "ORDER BY seq DESC LIMIT 40", common,
            ).fetchall())
            application_rows.extend(db.execute(
                "SELECT seq,event_kind,game_id,sender_did,normalized_payload,observed_at "
                "FROM formation_events WHERE room=? AND generation=? AND verified=1 "
                "AND julianday(observed_at)>=julianday(?) AND event_kind='APPLICATION_READBACK' "
                "ORDER BY seq DESC LIMIT 40", common,
            ).fetchall())
            roster_rows.extend(db.execute(
                "SELECT seq,event_kind,game_id,sender_did,normalized_payload,observed_at "
                "FROM formation_events WHERE room=? AND generation=? AND verified=1 "
                "AND julianday(observed_at)>=julianday(?) AND event_kind='ROSTER_CONSENT' "
                "AND EXISTS (SELECT 1 FROM json_each(json_extract(json_extract("
                "formation_events.normalized_payload,'$.text'),'$.members')) member "
                "WHERE member.value=?) ORDER BY seq DESC LIMIT 160",
                (*common, SARUKU_DID),
            ).fetchall())
        roster_seen: set[str] = set()
        for row in [*invitation_rows, *application_rows, *roster_rows]:
            at = _timestamp(row["observed_at"])
            if at is None or at < tracking_started:
                continue
            kind, game_id = row["event_kind"], row["game_id"]
            if kind == "TARGETED_RECRUITMENT_NOTE":
                activities.append(_activity("team_invitation", at, "Team invitation",
                    "Saruku received a direct team invitation.", ("invite", row["seq"]), game_id))
            elif kind == "APPLICATION_READBACK":
                activities.append(_activity("applied", at, "Applied",
                    "Saruku's application was confirmed in Discovery.",
                    ("application-readback", row["seq"]), game_id))
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
            "WHERE julianday(created_at)>=julianday(?) ORDER BY created_at DESC LIMIT 120",
            (tracking_started,),
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

        # Only timestamped durable facts belong in the timeline. Current phase
        # remains visible in the hero even when no authoritative transition
        # timestamp exists.
        receipt_rows = db.execute(
            "SELECT r.room,r.generation,r.seq,r.kind,r.payload,e.payload AS raw_payload "
            "FROM receipts r JOIN events e ON e.room=r.room AND e.generation=r.generation "
            "AND e.seq=r.seq WHERE r.accepted=1 AND r.kind IN "
            "('roster_ready','word_accepted','submission_accepted') "
            "ORDER BY r.generation DESC,r.seq DESC LIMIT 120"
        ).fetchall()
        receipt_mapping = {
            "roster_ready": ("team_ready", "Team ready", "The team is ready."),
            "word_accepted": ("writing", "Writing progress", "A contribution was accepted."),
            "submission_accepted": ("submitted", "Poem submitted", "The poem was submitted."),
        }
        for row in receipt_rows:
            try:
                raw = json.loads(row["raw_payload"]); payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            at = _timestamp(raw.get("created_at", raw.get("timestamp", raw.get("ts"))))
            if at is None or at < tracking_started:
                continue
            kind, title, detail = receipt_mapping[row["kind"]]
            game_id = payload.get("game_id") if isinstance(payload, dict) else None
            activities.append(_activity(kind, at, title, detail,
                                        ("receipt", row["room"], row["generation"], row["seq"]),
                                        game_id if isinstance(game_id, str) else None))

        activities.sort(key=lambda item: (item["at"], item["id"]), reverse=True)
        activities = activities[:MAX_ACTIVITY]
        direct_mentions = db.execute(
            "SELECT count(*) FROM formation_reflex_processing "
            "WHERE julianday(created_at)>=julianday(?) "
            "AND trigger_kind='team_direct_message'", (tracking_started,),
        ).fetchone()[0]
        reflex_replies = db.execute(
            "SELECT count(*) FROM formation_reflex_processing "
            "WHERE julianday(created_at)>=julianday(?) AND status='REPLIED'",
            (tracking_started,),
        ).fetchone()[0]
        def formation_count(kind: str, sender: str | None = None) -> int:
            total = 0
            for generation in generations:
                sql = ("SELECT count(*) FROM formation_events WHERE room=? AND generation=? "
                       "AND verified=1 AND julianday(observed_at)>=julianday(?) AND event_kind=?")
                args: tuple[Any, ...] = (ROOMS.discovery, generation, tracking_started, kind)
                if sender is not None:
                    sql += " AND sender_did=?"; args += (sender,)
                total += int(db.execute(sql, args).fetchone()[0])
            return total
        targeted_invites = formation_count("TARGETED_RECRUITMENT_NOTE")
        applications = formation_count("APPLICATION_READBACK")
        countersigns = formation_count("ROSTER_CONSENT", SARUKU_DID)
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
        writing = _writing_state(db, status, game)
        return validate_public_document({
            "schema_version": SCHEMA_VERSION, "checked_at": checked,
            "tracking_started_at": tracking_started,
            "mission": {"title": MISSION_TITLE, "goal": MISSION_GOAL,
                        "deadline": MISSION_DEADLINE, "status": status,
                        "current_stage": stage, "current_game": game},
            "counts": counts, "recent_activity": activities, "writing": writing,
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
    writing = value.get("writing")
    if writing is not None:
        if not isinstance(writing, dict) or set(writing) != {
            "version", "line_number", "lines", "previous_contributor", "team", "activity"
        }:
            raise PublicExportError("invalid writing")
        if (
            not isinstance(writing["version"], int) or isinstance(writing["version"], bool)
            or not isinstance(writing["line_number"], int) or isinstance(writing["line_number"], bool)
            or not 0 <= writing["version"] or not 0 <= writing["line_number"] <= 14
            or not isinstance(writing["lines"], list) or len(writing["lines"]) > 14
            or not all(isinstance(line, str) and len(line) <= 1000 for line in writing["lines"])
            or not isinstance(writing["team"], list) or not 1 <= len(writing["team"]) <= 8
            or not all(isinstance(alias, str) and alias in {"Saruku", *{
                f"Teammate {chr(ord('A') + index)}" for index in range(7)
            }} for alias in writing["team"])
            or writing["previous_contributor"] is not None
            and writing["previous_contributor"] not in writing["team"]
            or not isinstance(writing["activity"], list) or len(writing["activity"]) > MAX_ACTIVITY
        ):
            raise PublicExportError("invalid writing")
        allowed_writing = {
            "writing_started", "message", "word_proposed", "word_accepted",
            "line_completed", "poem_completed",
        }
        for item in writing["activity"]:
            if (
                not isinstance(item, dict) or forbidden & set(item)
                or item.get("type") not in allowed_writing
                or not all(isinstance(item.get(key), str) for key in ("id", "at", "title", "detail"))
                or "quote" in item and (not isinstance(item["quote"], str) or len(item["quote"]) > 500)
            ):
                raise PublicExportError("invalid writing activity")
    if _timestamp(value.get("checked_at")) is None or _timestamp(value.get("tracking_started_at")) is None:
        raise PublicExportError("invalid timestamps")
    if "did:key:" in json.dumps(value, ensure_ascii=False):
        raise PublicExportError("public DID leakage")
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


def load_cloudflare_credentials(path: Path) -> tuple[str, str, str]:
    try:
        directory = path.parent.stat()
        file_info = path.lstat()
        if stat.S_IMODE(directory.st_mode) != 0o700 or directory.st_uid != os.getuid():
            raise PublicExportError("Cloudflare credential directory permissions are unsafe")
        if not stat.S_ISREG(file_info.st_mode) or stat.S_IMODE(file_info.st_mode) != 0o600 or file_info.st_uid != os.getuid():
            raise PublicExportError("Cloudflare credential file permissions are unsafe")
        if path.is_symlink() or file_info.st_size > 8192:
            raise PublicExportError("Cloudflare credential file is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if opened.st_ino != file_info.st_ino or opened.st_dev != file_info.st_dev:
                raise PublicExportError("Cloudflare credential file changed during validation")
            value = json.loads(os.read(fd, 8193).decode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicExportError("Cloudflare credentials are unavailable") from exc
    keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_SONNET_KV_NAMESPACE_ID", "CLOUDFLARE_SONNET_API_TOKEN")
    if not isinstance(value, dict) or set(value) != set(keys) or any(
        not isinstance(value.get(key), str) or not value[key].strip() for key in keys
    ):
        raise PublicExportError("Cloudflare credential file format is invalid")
    return tuple(value[key].strip() for key in keys)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export bounded public Sonnet mission state")
    parser.add_argument("--db", type=Path, default=Path("state/sonnet-chain.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("state/public/sonnet-latest.json"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--credentials-file", type=Path)
    args = parser.parse_args(argv)
    try:
        document = build_public_document(args.db)
        if args.dry_run:
            print(json.dumps(document, ensure_ascii=False, indent=2)); return 0
        stage_document(document, args.output)
        if args.publish:
            values = list(load_cloudflare_credentials(args.credentials_file)) if args.credentials_file else [
                os.getenv(name) for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_SONNET_KV_NAMESPACE_ID", "CLOUDFLARE_SONNET_API_TOKEN")
            ]
            if not all(values):
                raise PublicExportError("Sonnet Cloudflare publication is not configured")
            publish_document(document, values[0], values[1], values[2])
        print("Sonnet public export staged" + (" and published" if args.publish else ""))
        return 0
    except (PublicExportError, OSError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
