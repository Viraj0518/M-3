"""ring — the 3-hop co-edit traversal, and the window that makes it honest.

The ring is the load-bearing FalkorDB proof (plan/synthesis.json
sponsor_integration.falkordb §1): three distinct actors, two distinct pages,
four :EDITED edges inside one time window, where NO INDIVIDUAL EDIT crosses any
threshold. The signal is the SHAPE.

The discriminating pair is what makes it evidence rather than a demo: the same
actors and the same pages must FIRE inside the window and NOT FIRE outside it.
A test that only proves it fires would pass on a query that returns everything.
"""

from __future__ import annotations

from app.bridge.tests.conftest import requires_live_db

pytestmark = requires_live_db

BASE = 1_700_000_000


def seed_ring(cypher, spread_s: int) -> None:
    """3 actors, 4 pages, 4 co-edits. `spread_s` scales how far apart the edits
    are — the ONLY thing that differs between the warm and the cold case."""
    cypher(
        "CREATE (a:Actor {name:'Northstar-7'}), (b:Actor {name:'VectorMint'}), "
        "       (c:Actor {name:'SableWeather'}), "
        "       (p1:Page {title:'Metropolitan Transit Authority'}), "
        "       (p2:Page {title:'Regional power grid'}), "
        "       (p3:Page {title:'Water treatment network'}), "
        "       (p4:Page {title:'Civic alert protocol'}), "
        "       (a)-[:EDITED {ts:$t0, bytes:120, summary:'transit edit'}]->(p1), "
        "       (b)-[:EDITED {ts:$t1, bytes:-40, summary:'transit revert'}]->(p1), "
        "       (b)-[:EDITED {ts:$t2, bytes:88,  summary:'grid capacity'}]->(p2), "
        "       (c)-[:EDITED {ts:$t3, bytes:15,  summary:'grid outage map'}]->(p2), "
        "       (a)-[:EDITED {ts:$t0, bytes:9,   summary:'water notice'}]->(p3), "
        "       (c)-[:EDITED {ts:$t3, bytes:3,   summary:'civic alias'}]->(p4)",
        {
            "t0": BASE,
            "t1": BASE + spread_s,
            "t2": BASE + spread_s * 2,
            "t3": BASE + spread_s * 3,
        },
    )


def test_ring_fires_inside_the_window(call, run, cypher):
    seed_ring(cypher, spread_s=120)          # whole ring closes in 6 minutes
    r = run(call("ring", {"window_s": 720}))

    assert r["ok"] is True
    assert r["fired"] is True
    assert r["ring_count"] >= 1

    ring = r["rings"][0]
    assert len(set(ring["actors"])) == 3, "three DISTINCT actors"
    assert len(set(ring["pages"])) == 2, "two DISTINCT pages"
    assert ring["span_s"] <= 720
    assert 0.0 <= r["ring_score"] <= 1.0


def test_ring_does_not_fire_when_cold(call, run, cypher):
    """SAME actors, SAME pages, edits spread over days instead of minutes.
    If this ever goes green the window is not being applied and the ring
    detects nothing — it just reports every co-edit that ever happened."""
    seed_ring(cypher, spread_s=86_400)       # a day between each edit
    r = run(call("ring", {"window_s": 720}))

    assert r["ok"] is True
    assert r["fired"] is False
    assert r["ring_count"] == 0
    assert r["rings"] == []
    assert r["paths"] == []
    assert r["should_open_case"] is False


def test_ring_returns_falkordb_paths_and_its_own_run_time(call, run, cypher):
    """`run_time_ms` goes ON SCREEN next to the verdict, so it must be
    FalkorDB's OWN measured execution time, not a wall clock we computed."""
    seed_ring(cypher, spread_s=120)
    r = run(call("ring", {"window_s": 720}))

    assert isinstance(r["run_time_ms"], float)
    assert r["run_time_ms"] > 0.0

    path = r["paths"][0]
    # A path is the 5-node alternating chain actor->page<-actor->page<-actor.
    assert len(path["nodes"]) == 5
    assert len(path["edges"]) == 4
    assert [n["labels"][0] for n in path["nodes"]] == [
        "Actor", "Page", "Actor", "Page", "Actor",
    ]
    assert all(e["type"] == "EDITED" for e in path["edges"])
    assert path["ids"] == [n["id"] for n in path["nodes"]]


def test_mirror_rings_are_deduped(call, run, cypher):
    """(A,B,C) and (C,B,A) are the same ring walked backwards. Counting both
    would double ring_score and open the case twice."""
    seed_ring(cypher, spread_s=120)
    r = run(call("ring", {"window_s": 720}))
    fingerprints = [
        (tuple(sorted(x["actors"])), tuple(sorted(x["pages"]))) for x in r["rings"]
    ]
    assert len(fingerprints) == len(set(fingerprints))


def test_ring_anchor_filters_to_one_actor(call, run, cypher):
    seed_ring(cypher, spread_s=120)
    hit = run(call("ring", {"window_s": 720, "anchor": "Northstar-7"}))
    assert hit["fired"] is True
    assert all("Northstar-7" in x["actors"] for x in hit["rings"])

    miss = run(call("ring", {"window_s": 720, "anchor": "NobodyAtAll"}))
    assert miss["fired"] is False


def test_ring_on_an_empty_graph_is_an_honest_zero(call, run):
    """The ablation's cold graph. Not an error, not a fabricated result."""
    r = run(call("ring", {}))
    assert r["ok"] is True
    assert r["fired"] is False
    assert r["ring_count"] == 0
    assert r["ids"] == []
