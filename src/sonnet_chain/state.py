from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from .config import ROOMS
from .formation_records import VerifiedFormationRecord


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
            CREATE TABLE IF NOT EXISTS receipt_processing (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              status TEXT NOT NULL, processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(room,generation,seq)
            );
            CREATE TABLE IF NOT EXISTS teams (
              game_id TEXT PRIMARY KEY, payload TEXT NOT NULL, score REAL
            );
            CREATE TABLE IF NOT EXISTS team_events (
              event_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, room TEXT NOT NULL,
              room_generation INTEGER NOT NULL, source_seq INTEGER NOT NULL,
              source_ordinal INTEGER NOT NULL,
              source_poem_version INTEGER, evidence_class TEXT NOT NULL,
              event_type TEXT NOT NULL, actor_did TEXT, subject_did TEXT,
              scope TEXT, predicate TEXT, value_json TEXT, target_text TEXT,
              resolved_target_did TEXT, extraction_method TEXT NOT NULL,
              extraction_confidence REAL, extractor_version INTEGER NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS team_message_analysis (
              game_id TEXT NOT NULL, room TEXT NOT NULL, generation INTEGER NOT NULL,
              seq INTEGER NOT NULL, extractor_version INTEGER NOT NULL,
              status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              last_error_code TEXT, processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(game_id,room,generation,seq,extractor_version)
            );
            CREATE TABLE IF NOT EXISTS team_source_events (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              sender_did TEXT NOT NULL, event_type TEXT, request_id TEXT,
              payload TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified=1),
              PRIMARY KEY(room,generation,seq)
            );
            CREATE INDEX IF NOT EXISTS team_source_request
              ON team_source_events(room,generation,request_id);
            CREATE TABLE IF NOT EXISTS team_source_processing (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              referee_checked INTEGER NOT NULL DEFAULT 0,
              semantic_checked INTEGER NOT NULL DEFAULT 0,
              processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(room,generation,seq)
            );
            CREATE TABLE IF NOT EXISTS team_room_status (
              room TEXT NOT NULL, generation INTEGER NOT NULL,
              closed INTEGER NOT NULL DEFAULT 0, terminal_seq INTEGER,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(room,generation)
            );
            CREATE TABLE IF NOT EXISTS team_context_snapshots (
              game_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
              reducer_version INTEGER NOT NULL, ledger_high_watermark TEXT NOT NULL,
              payload_json TEXT NOT NULL, rebuilt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS formation_history_state (
              room TEXT NOT NULL, generation INTEGER NOT NULL,
              complete_from_seq INTEGER, complete_through_seq INTEGER NOT NULL DEFAULT 0,
              highest_observed_seq INTEGER NOT NULL DEFAULT 0,
              gap_ranges_json TEXT NOT NULL DEFAULT '[]',
              reconciliation_required INTEGER NOT NULL DEFAULT 0,
              available_from_seq INTEGER, available_through_seq INTEGER,
              retention_truncated INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(room,generation)
            );
            CREATE TABLE IF NOT EXISTS formation_epochs (
              epoch_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, inviter_did TEXT NOT NULL,
              payload_json TEXT NOT NULL, status TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_formation_epoch
              ON formation_epochs(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS formation_opportunities (
              opportunity_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
              inviter_did TEXT NOT NULL, source_seq INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              consumed_at TEXT, consumed_request_id TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS formation_events (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              event_kind TEXT NOT NULL, game_id TEXT, sender_did TEXT NOT NULL,
              request_id TEXT, roster_fingerprint TEXT,
              normalized_payload TEXT NOT NULL, observed_at TEXT,
              verified INTEGER NOT NULL CHECK(verified=1),
              PRIMARY KEY(room,generation,seq)
            );
            CREATE INDEX IF NOT EXISTS formation_events_game
              ON formation_events(room,generation,game_id,seq);
            CREATE INDEX IF NOT EXISTS formation_events_kind_game_seq
              ON formation_events(room,generation,event_kind,game_id,seq);
            CREATE INDEX IF NOT EXISTS formation_events_kind_seq
              ON formation_events(room,generation,event_kind,seq);
            CREATE INDEX IF NOT EXISTS formation_events_request
              ON formation_events(room,generation,request_id);
            CREATE TABLE IF NOT EXISTS formation_event_processing (
              room TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL,
              processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(room,generation,seq)
            );
            CREATE TABLE IF NOT EXISTS formation_reflex_processing (
              source_room TEXT NOT NULL, source_generation INTEGER NOT NULL,
              source_seq INTEGER NOT NULL, game_id TEXT NOT NULL,
              trigger_kind TEXT NOT NULL, protocol_state TEXT NOT NULL,
              status TEXT NOT NULL, llm_action TEXT, generator TEXT,
              response_request_id TEXT, response_text_hash TEXT,
              delivery_state TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              processed_at TEXT,
              PRIMARY KEY(source_room,source_generation,source_seq)
            );
            CREATE TABLE IF NOT EXISTS formation_reflex_frontiers (
              source_room TEXT NOT NULL, source_generation INTEGER NOT NULL,
              last_seq INTEGER NOT NULL,
              activated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(source_room,source_generation)
            );
            CREATE TABLE IF NOT EXISTS protocol_outbox (
              request_id TEXT PRIMARY KEY, action_kind TEXT NOT NULL,
              room TEXT NOT NULL, game_id TEXT,
              roster_fingerprint TEXT, payload_json TEXT NOT NULL,
              delivery_state TEXT NOT NULL, created_at TEXT NOT NULL,
              reconcile_started_at TEXT, reconcile_deadline TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS entry_closures (
              game_id TEXT PRIMARY KEY, contest_id TEXT NOT NULL,
              poem_room TEXT NOT NULL, room_generation INTEGER NOT NULL,
              final_version INTEGER NOT NULL, final_state_hash TEXT NOT NULL,
              final_contributor_did TEXT NOT NULL, canonical_poem TEXT NOT NULL,
              poem_sha256 TEXT NOT NULL, roster_fingerprint TEXT,
              state TEXT NOT NULL, failure_code TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_parts (
              publication_id TEXT NOT NULL, game_id TEXT NOT NULL,
              final_version INTEGER NOT NULL, poem_sha256 TEXT NOT NULL,
              part_index INTEGER NOT NULL, part_count INTEGER NOT NULL,
              exact_content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
              parent_part_index INTEGER, parent_post_id TEXT,
              state TEXT NOT NULL, x_post_id TEXT,
              confirmed_account_id TEXT, confirmed_text TEXT,
              confirmed_parent_post_id TEXT,
              strong_confirmation INTEGER NOT NULL DEFAULT 0,
              attempt_started_at TEXT, confirmed_at TEXT,
              PRIMARY KEY(publication_id,part_index)
            );
            CREATE INDEX IF NOT EXISTS publication_parts_game
              ON publication_parts(game_id,final_version,part_index);
            CREATE TABLE IF NOT EXISTS submission_intents (
              request_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
              poem_room TEXT NOT NULL, room_generation INTEGER NOT NULL,
              final_version INTEGER NOT NULL, final_state_hash TEXT NOT NULL,
              poem_sha256 TEXT NOT NULL, x_post_ids_json TEXT NOT NULL,
              packet_json TEXT NOT NULL, packet_sha256 TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL,
              last_attempt_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT, receipt_seq INTEGER,
              entry_id TEXT, failure_code TEXT
            );
            CREATE INDEX IF NOT EXISTS submission_intents_game
              ON submission_intents(game_id,state,created_at);
            CREATE TABLE IF NOT EXISTS participant_commitments (
              contest_id TEXT NOT NULL, game_id TEXT NOT NULL,
              roster_fingerprint TEXT, state TEXT NOT NULL,
              release_entry_id TEXT, release_submission_request_id TEXT,
              release_receipt_seq INTEGER, release_referee_did TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(contest_id,game_id)
            );
            CREATE TABLE IF NOT EXISTS peer_endgame_state (
              game_id TEXT PRIMARY KEY, final_contributor_did TEXT NOT NULL,
              final_version INTEGER NOT NULL, frozen_at TEXT NOT NULL,
              last_progress_at TEXT NOT NULL, last_reminder_at TEXT,
              reminder_stage INTEGER NOT NULL DEFAULT 0,
              dedupe_key TEXT, state TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        self.db.commit()
        publication_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(publication_parts)").fetchall()
        }
        if "confirmed_parent_post_id" not in publication_columns:
            self.db.execute(
                "ALTER TABLE publication_parts ADD COLUMN confirmed_parent_post_id TEXT"
            )
        submission_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(submission_intents)").fetchall()
        }
        for name, definition in (
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("next_retry_at", "TEXT"),
        ):
            if name not in submission_columns:
                self.db.execute(f"ALTER TABLE submission_intents ADD COLUMN {name} {definition}")
        self.db.commit()
        team_event_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(team_events)").fetchall()
        }
        if "source_ordinal" not in team_event_columns:
            self.db.execute(
                "ALTER TABLE team_events ADD COLUMN source_ordinal INTEGER NOT NULL DEFAULT 0"
            )
            self.db.commit()
        history_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(formation_history_state)").fetchall()
        }
        for name, definition in (
            ("available_from_seq", "INTEGER"),
            ("available_through_seq", "INTEGER"),
            ("retention_truncated", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in history_columns:
                self.db.execute(f"ALTER TABLE formation_history_state ADD COLUMN {name} {definition}")
        self.db.commit()
        source_processing_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(team_source_processing)").fetchall()
        }
        for name in ("referee_checked", "semantic_checked"):
            if name not in source_processing_columns:
                self.db.execute(
                    f"ALTER TABLE team_source_processing ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
                )
        self.db.commit()
        opportunity_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(formation_opportunities)").fetchall()
        }
        for name, definition in (("consumed_at", "TEXT"), ("consumed_request_id", "TEXT")):
            if name not in opportunity_columns:
                self.db.execute(f"ALTER TABLE formation_opportunities ADD COLUMN {name} {definition}")
        self.db.commit()
        # This index references additive columns and therefore must be created
        # only after an existing v0.2 database has been ALTERed above.
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS formation_opportunities_game_seq "
            "ON formation_opportunities(game_id,source_seq,consumed_at)"
        )
        self.db.commit()
        # A normalized registration_accepted row could only have been created
        # after pinned-referee signature and schema verification. Historically
        # `accepted` meant "matched Saruku's pending request"; for registration
        # facts it now records the trusted referee's accepted status instead.
        with self.db:
            self.db.execute(
                "UPDATE receipts SET accepted=1 WHERE room=? "
                "AND kind='registration_accepted'",
                (ROOMS.registration,),
            )
        # Conservative v0.2 bootstrap: a legacy pending official mutation may
        # already have crossed the network boundary. Absence of a new table row
        # is not evidence that consent/withdrawal was never attempted.
        legacy = self.db.execute(
            "SELECT request_id,kind,payload,created_at FROM requests "
            "WHERE status IN ('pending','posted') AND kind IN ('roster','withdraw')"
        ).fetchall()
        with self.db:
            for row in legacy:
                payload = json.loads(row["payload"])
                action = row["kind"]
                delivery = (
                    "CONSENT_DELIVERY_UNKNOWN" if action == "roster"
                    else "DELIVERY_UNKNOWN"
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO protocol_outbox("
                    "request_id,action_kind,room,game_id,payload_json,delivery_state,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (row["request_id"], action, ROOMS.discovery,
                     payload.get("game_id"), row["payload"], delivery, row["created_at"]),
                )
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
            "poem_last_progress_at": None,
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

    def unprocessed_team_sources(
        self, room: str, generation: int, scope: str,
    ) -> list[dict[str, Any]]:
        column = "referee_checked" if scope == "referee" else "semantic_checked"
        rows = self.db.execute(
            "SELECT e.seq,e.payload FROM events e LEFT JOIN team_source_processing p "
            "ON p.room=e.room AND p.generation=e.generation AND p.seq=e.seq "
            f"WHERE e.room=? AND e.generation=? AND (p.seq IS NULL OR p.{column}=0) ORDER BY e.seq",
            (room, generation),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def persist_team_source(
        self, room: str, generation: int, seq: int, sender: str, raw: dict[str, Any],
        event_type: str | None, request_id: str | None, scope: str,
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO team_source_events(room,generation,seq,sender_did,"
                "event_type,request_id,payload,verified) VALUES(?,?,?,?,?,?,?,1)",
                (room, generation, seq, sender, event_type, request_id,
                 json.dumps(raw, ensure_ascii=False, separators=(",", ":"))),
            )
            self._mark_team_source_processed_sql(room, generation, seq, scope)

    def _mark_team_source_processed_sql(
        self, room: str, generation: int, seq: int, scope: str,
    ) -> None:
        referee = int(scope == "referee"); semantic = int(scope != "referee")
        self.db.execute(
            "INSERT INTO team_source_processing(room,generation,seq,referee_checked,semantic_checked) "
            "VALUES(?,?,?,?,?) ON CONFLICT(room,generation,seq) DO UPDATE SET "
            "referee_checked=max(referee_checked,excluded.referee_checked),"
            "semantic_checked=max(semantic_checked,excluded.semantic_checked),"
            "processed_at=CURRENT_TIMESTAMP",
            (room, generation, seq, referee, semantic),
        )

    def mark_team_source_processed(
        self, room: str, generation: int, seq: int, scope: str,
    ) -> None:
        with self.db:
            self._mark_team_source_processed_sql(room, generation, seq, scope)

    def verified_team_source(self, room: str, generation: int, seq: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload FROM team_source_events WHERE room=? AND generation=? AND seq=?",
            (room, generation, seq),
        ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def has_verified_receipt(self, room: str, generation: int, seq: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM receipts WHERE room=? AND generation=? AND seq=?",
            (room, generation, seq),
        ).fetchone() is not None

    def verified_team_sources(self, room: str, generation: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT payload FROM team_source_events WHERE room=? AND generation=? ORDER BY seq",
            (room, generation),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close_team_room(self, room: str, generation: int, seq: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO team_room_status(room,generation,closed,terminal_seq) VALUES(?,?,1,?) "
                "ON CONFLICT(room,generation) DO UPDATE SET closed=1,terminal_seq=max("
                "coalesce(terminal_seq,0),excluded.terminal_seq),updated_at=CURRENT_TIMESTAMP",
                (room, generation, seq),
            )

    def team_room_closed(self, room: str, generation: int) -> bool:
        row = self.db.execute(
            "SELECT closed FROM team_room_status WHERE room=? AND generation=?", (room, generation)
        ).fetchone()
        return bool(row and row["closed"])

    def unprocessed_formation_events(self, room: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT e.generation,e.seq,e.payload FROM events e LEFT JOIN "
            "formation_event_processing p ON p.room=e.room AND p.generation=e.generation "
            "AND p.seq=e.seq WHERE e.room=? AND p.seq IS NULL ORDER BY e.generation,e.seq",
            (room,),
        ).fetchall()
        out = []
        for row in rows:
            raw = json.loads(row["payload"])
            raw["_formation_generation"] = row["generation"]
            raw["_formation_seq"] = row["seq"]
            out.append(raw)
        return out

    def mark_formation_event_processed(self, room: str, generation: int, seq: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO formation_event_processing(room,generation,seq) VALUES(?,?,?)",
                (room, generation, seq),
            )

    def persist_formation_event(
        self, room: str, generation: int, seq: int, event_kind: str,
        game_id: str | None, sender_did: str, request_id: str | None,
        roster_fingerprint: str | None, raw: dict[str, Any], observed_at: str | None,
    ) -> None:
        stored = dict(raw)
        stored.pop("_formation_verified", None)
        stored["_room_generation"] = None if generation == -1 else generation
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO formation_events(room,generation,seq,event_kind,game_id,"
                "sender_did,request_id,roster_fingerprint,normalized_payload,observed_at,verified) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                (room, generation, seq, event_kind, game_id, sender_did, request_id,
                 roster_fingerprint, json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                 observed_at),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO formation_event_processing(room,generation,seq) VALUES(?,?,?)",
                (room, generation, seq),
            )

    def formation_events(
        self, room: str, generation: int | None = None, *, game_id: str | None = None,
        request_id: str | None = None, event_kind: str | None = None,
        min_seq: int | None = None, sender_did: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["room=?", "verified=1"]
        args: list[Any] = [room]
        if generation is not None:
            clauses.append("generation=?")
            args.append(generation)
        if game_id is not None:
            clauses.append("game_id=?")
            args.append(game_id)
        if request_id is not None:
            clauses.append("request_id=?")
            args.append(request_id)
        if event_kind is not None:
            clauses.append("event_kind=?")
            args.append(event_kind)
        if min_seq is not None:
            clauses.append("seq>=?")
            args.append(min_seq)
        if sender_did is not None:
            clauses.append("sender_did=?")
            args.append(sender_did)
        rows = self.db.execute(
            "SELECT normalized_payload FROM formation_events WHERE " + " AND ".join(clauses)
            + " ORDER BY generation,seq", args,
        ).fetchall()
        return [VerifiedFormationRecord(json.loads(row["normalized_payload"])) for row in rows]

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

    def persist_protocol_intent(
        self, request_id: str, action_kind: str, room: str, payload: dict,
        delivery_state: str, *, game_id: str | None = None,
        roster_fingerprint: str | None = None, created_at: str,
        reconcile_started_at: str | None = None,
        reconcile_deadline: str | None = None,
    ) -> bool:
        """Durably freeze an outbound mutation before any network side effect."""
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO protocol_outbox("
                "request_id,action_kind,room,game_id,roster_fingerprint,payload_json,"
                "delivery_state,created_at,reconcile_started_at,reconcile_deadline) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (request_id, action_kind, room, game_id, roster_fingerprint, encoded,
                 delivery_state, created_at, reconcile_started_at, reconcile_deadline),
            )
        if cur.rowcount == 1:
            return True
        existing = self.protocol_intent(request_id)
        if (
            existing is None or existing["action_kind"] != action_kind
            or existing["room"] != room or existing["payload"] != payload
            or existing["game_id"] != game_id
            or existing["roster_fingerprint"] != roster_fingerprint
        ):
            raise ValueError("request_id is already bound to a different protocol intent")
        return False

    def protocol_intent(self, request_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM protocol_outbox WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def unresolved_protocol_intent(self, action_kind: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT request_id FROM protocol_outbox WHERE action_kind=? AND "
            "delivery_state IN ('POSTED_UNCONFIRMED','DELIVERY_UNKNOWN',"
            "'CONSENT_POSTED_UNCONFIRMED','CONSENT_DELIVERY_UNKNOWN') "
            "ORDER BY created_at LIMIT 1", (action_kind,),
        ).fetchone()
        return None if row is None else self.protocol_intent(row["request_id"])

    def set_protocol_delivery(self, request_id: str, delivery_state: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE protocol_outbox SET delivery_state=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE request_id=?", (delivery_state, request_id),
            )

    def trusted_writer_dids(self) -> set[str]:
        """Writer roles derived only from accepted, verified referee receipts."""
        rows = self.db.execute(
            "SELECT payload FROM receipts WHERE kind='registration_accepted' AND accepted=1"
        ).fetchall()
        writers: set[str] = set()
        for row in rows:
            payload = json.loads(row["payload"])
            did = payload.get("participant_did", payload.get("sender_did"))
            if payload.get("role") == "writer" and isinstance(did, str):
                writers.add(did)
        return writers

    def unprocessed_receipt_events(self, room: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT e.generation,e.seq,e.payload FROM events e "
            "LEFT JOIN receipt_processing p ON p.room=e.room "
            "AND p.generation=e.generation AND p.seq=e.seq "
            "WHERE e.room=? AND (p.seq IS NULL OR p.status<>'done') "
            "ORDER BY e.generation,e.seq",
            (room,),
        ).fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["_receipt_generation"] = row["generation"]
            payload["_receipt_seq"] = row["seq"]
            out.append(payload)
        return out

    def mark_receipt_event_processed(
        self, room: str, generation: int, seq: int, status: str,
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO receipt_processing(room,generation,seq,status) "
                "VALUES(?,?,?,?) ON CONFLICT(room,generation,seq) DO UPDATE SET "
                "status=excluded.status,processed_at=CURRENT_TIMESTAMP",
                (room, generation, seq, status),
            )
