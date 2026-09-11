from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator


class Phase(StrEnum):
    WAIT_LAUNCH = "WAIT_LAUNCH"
    VERIFY_LAUNCH = "VERIFY_LAUNCH"
    REGISTER = "REGISTER"
    WAIT_REGISTRATION_RECEIPT = "WAIT_REGISTRATION_RECEIPT"
    DISCOVERY = "DISCOVERY"
    SELECT_TEAM = "SELECT_TEAM"
    NEGOTIATE = "NEGOTIATE"
    WAIT_TEAM_SETUP = "WAIT_TEAM_SETUP"
    ROSTER_CONSENT = "ROSTER_CONSENT"
    WAIT_ROSTER_READY = "WAIT_ROSTER_READY"
    WRITING = "WRITING"
    POEM_COMPLETE = "POEM_COMPLETE"
    WAIT_PUBLISHER = "WAIT_PUBLISHER"
    X_AUTH_REQUIRED = "X_AUTH_REQUIRED"
    PUBLISH_IF_FINAL_CONTRIBUTOR = "PUBLISH_IF_FINAL_CONTRIBUTOR"
    SUBMIT = "SUBMIT"
    WAIT_SUBMISSION_RECEIPT = "WAIT_SUBMISSION_RECEIPT"
    DONE = "DONE"


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors (
              room TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0,
              generation INTEGER
            );
            CREATE TABLE IF NOT EXISTS events (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(room, generation, seq)
            );
            CREATE TABLE IF NOT EXISTS requests (
              request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS receipts (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
              payload TEXT NOT NULL, accepted INTEGER NOT NULL,
              PRIMARY KEY(room, generation, seq)
            );
            CREATE TABLE IF NOT EXISTS teams (
              game_id TEXT PRIMARY KEY, payload TEXT NOT NULL, score REAL
            );
            """
        )
        self.db.commit()
        defaults = {
            "phase": Phase.WAIT_LAUNCH.value,
            "trusted_launch": None,
            "referee_did": None,
            "manifest_sha256": None,
            "registered": False,
            "discovery_advertised": False,
            "discovery_advertisement_attempted": False,
            "candidate_teams": [],
            "active_team": None,
            "current_roster": [],
            "poem_room": None,
            "poem_version": 0,
            "poem_state_hash": None,
            "accepted_contributions": [],
            "previous_contributor": None,
            "final_contributor": None,
            "x_post_ids": [],
            "submission_state": None,
            "technocore_write_attempts": 0,
            "x_write_count": 0,
        }
        for key, value in defaults.items():
            if self.get(key) is None:
                self.set(key, value)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.db:
            yield self.db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))),
            )

    @property
    def phase(self) -> Phase:
        return Phase(self.get("phase", Phase.WAIT_LAUNCH.value))

    @phase.setter
    def phase(self, value: Phase) -> None:
        self.set("phase", value.value)

    def cursor(self, room: str) -> tuple[int, int | None]:
        row = self.db.execute(
            "SELECT last_seq,generation FROM cursors WHERE room=?", (room,)
        ).fetchone()
        return (0, None) if row is None else (row["last_seq"], row["generation"])

    def reset_cursor(self, room: str, generation: int | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO cursors(room,last_seq,generation) VALUES(?,0,?) "
                "ON CONFLICT(room) DO UPDATE SET last_seq=0,generation=excluded.generation",
                (room, generation),
            )

    def record_event(self, room: str, seq: int, generation: int | None, raw: dict) -> bool:
        generation_key = generation if generation is not None else -1
        stored = dict(raw)
        stored["_room_generation"] = generation
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO events(room,generation,seq,payload) VALUES(?,?,?,?)",
                (room, generation_key, seq, json.dumps(stored, ensure_ascii=False, separators=(",", ":"))),
            )
            self.db.execute(
                "INSERT INTO cursors(room,last_seq,generation) VALUES(?,?,?) "
                "ON CONFLICT(room) DO UPDATE SET "
                "last_seq=max(last_seq,excluded.last_seq), generation=coalesce(excluded.generation,generation)",
                (room, seq, generation),
            )
        return cur.rowcount == 1

    def events(self, room: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT payload FROM events WHERE room=? ORDER BY generation,seq", (room,)
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def reserve_request(self, request_id: str, kind: str, payload: dict) -> bool:
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO requests(request_id,kind,payload,status) VALUES(?,?,?,'pending')",
                (request_id, kind, json.dumps(payload, separators=(",", ":"))),
            )
        return cur.rowcount == 1

    def pending_request(self, kind: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT request_id,payload,status FROM requests WHERE kind=? AND status='pending' "
            "ORDER BY created_at LIMIT 1", (kind,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload["request_id"] = row["request_id"]
        return payload

    def set_request_status(self, request_id: str, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE requests SET status=? WHERE request_id=?", (status, request_id))

    def increment(self, key: str, amount: int = 1) -> int:
        value = int(self.get(key, 0) or 0) + amount
        self.set(key, value)
        return value

    def active_team(self) -> str | None:
        return self.get("active_team")

    def select_team(self, game_id: str) -> bool:
        active = self.active_team()
        if active and active != game_id:
            return False
        self.set("active_team", game_id)
        return True
