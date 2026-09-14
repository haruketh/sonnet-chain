from __future__ import annotations

import sqlite3

from sonnet_chain.state import StateStore


def test_old_formation_opportunity_schema_migrates_before_index(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE formation_opportunities (opportunity_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, "
        "inviter_did TEXT NOT NULL, source_seq INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    db.execute(
        "INSERT INTO formation_opportunities(opportunity_id,game_id,inviter_did,source_seq,payload_json) "
        "VALUES('g:lead:7','g','lead',7,'{}')"
    )
    db.commit(); db.close()

    state = StateStore(path)
    columns = {row[1] for row in state.db.execute("PRAGMA table_info(formation_opportunities)")}
    indexes = {row[1] for row in state.db.execute("PRAGMA index_list(formation_opportunities)")}
    assert {"consumed_at", "consumed_request_id"} <= columns
    assert "formation_opportunities_game_seq" in indexes
    assert tuple(state.db.execute(
        "SELECT game_id,source_seq FROM formation_opportunities WHERE opportunity_id='g:lead:7'"
    ).fetchone()) == ("g", 7)


def test_fresh_database_has_opportunity_consumption_index(tmp_path):
    state = StateStore(tmp_path / "fresh.db")
    indexes = {row[1] for row in state.db.execute("PRAGMA index_list(formation_opportunities)")}
    assert "formation_opportunities_game_seq" in indexes
