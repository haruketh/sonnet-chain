"""Offline ~50k fixture benchmark: pytest -q -s tests/test_active_acquisition_performance.py."""
import json
import time
from datetime import timedelta

from test_active_acquisition import scene, add, search
from sonnet_chain.config import ROOMS


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
