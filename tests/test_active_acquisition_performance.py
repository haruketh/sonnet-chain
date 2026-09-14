"""Offline ~50k fixture benchmark: pytest -q -s tests/test_active_acquisition_performance.py."""
import json
import time
from datetime import timedelta

from test_active_acquisition import scene, add, search
from sonnet_chain.config import ROOMS
from sonnet_chain.active_acquisition import ActiveAcquisition
from sonnet_chain.team_formation import FormationOpportunity, TeamFormationStore
from unittest.mock import Mock


def test_fifty_thousand_warm_acquisition(scene, monkeypatch):
    daemon, _, _, now = scene
    old = (now - timedelta(hours=2)).isoformat()
    with daemon.state.db:
        daemon.state.db.executemany(
            "INSERT INTO formation_events VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            [(ROOMS.discovery, 1, seq, "ROSTER_WITHDRAWAL", "history", "writer", None, None,
              json.dumps({"seq": seq, "created_at": old}), old) for seq in range(1, 50001)],
        )
    service = search(scene)
    queries = []
    daemon.state.db.set_trace_callback(queries.append)
    report = []
    def measure(label):
        queries.clear()
        wall, cpu = time.perf_counter(), time.process_time()
        candidates = service.scan(now)
        report.append({"case": label, "wall_ms": round((time.perf_counter()-wall)*1000, 3),
                       "cpu_ms": round((time.process_time()-cpu)*1000, 3), "candidates": candidates,
                       "game_reconstructions": sum("SELECT normalized_payload" in q for q in queries)})
        return candidates
    assert measure("cold bootstrap / 50000 historical withdrawals") == 0
    assert measure("warm no new") == 0
    assert report[-1]["game_reconstructions"] == 0
    add(scene, 4, 50001, withdraw=True)
    assert measure("one new nonqualifying withdrawal") == 0
    add(scene, 0, 50002)
    add(scene, 3, 50003)
    add(scene, 3, 50004, withdraw=True)
    assert measure("one new qualifying withdrawal") == 1
    assert measure("warm after") == 0
    assert report[-1]["game_reconstructions"] == 0
    # No cryptographic verification is performed by the acquisition reducer.
    import sonnet_chain.rosters as rosters
    monkeypatch.setattr(rosters, "verify_room_signature", lambda *a: (_ for _ in ()).throw(AssertionError("reverify")))
    assert service.reconstruct("vacancy", 50004, now)
    print(json.dumps(report, indent=2))


def test_daemon_warm_stale_vacancies_do_no_expensive_work(scene, monkeypatch):
    daemon, _, roster, now = scene
    daemon.state.reset_cursor(ROOMS.discovery, 1)
    store = TeamFormationStore(daemon.state)
    # Include missing/future/naive timestamps, not just expired valid timestamps.
    stamps = [now - timedelta(hours=2)] * 100 + [None, now + timedelta(hours=1), now.replace(tzinfo=None)]
    for seq, stamp in enumerate(stamps, 1):
        game = f"stale-{seq}"
        store.save_opportunity(FormationOpportunity(
            game, roster.members[0], seq, stamp, ROOMS.team(game), 3,
            opportunity_kind="ACTIVE_VACANCY", source_generation=1,
        ))
    spies = {name: Mock(return_value=False) for name in ("open", "read", "reconstruct", "revalidate", "post")}
    daemon._team_room_open = spies["open"]
    daemon._read_incremental = spies["read"]
    daemon._post = spies["post"]
    monkeypatch.setattr(ActiveAcquisition, "reconstruct", spies["reconstruct"])
    monkeypatch.setattr(ActiveAcquisition, "revalidate", spies["revalidate"])
    daemon._discovery()  # initialize the real acquisition frontier
    wall, cpu = time.perf_counter(), time.process_time()
    for _ in range(10):
        daemon._discovery()
    result = {"case": "daemon candidate pass / 103 stale or invalid vacancies",
              "polls": 10, "wall_ms_per_poll": (time.perf_counter()-wall)*100,
              "cpu_ms_per_poll": (time.process_time()-cpu)*100}
    for name, spy in spies.items():
        spy.assert_not_called()
        result[name + "_calls"] = spy.call_count
    assert store.active_epoch() is None
    assert daemon.state.db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    print(json.dumps(result, indent=2))
