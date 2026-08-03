"""The REAL ablation, as a test: same new event, OPPOSITE mechanical verdict.

This is GOAL victory condition 1 — the beat that is NEVER cut. It is a
DIFFERENT claim from ``test_replay_parity`` (which proves reproducibility: the
same log re-derives the identical graph, an EQUALITY). Here the two graphs are
deliberately UNequal — WARM has the historical corpus, COLD does not — and the
SAME ring Cypher must return opposite verdicts on the SAME event.

Live against FalkorDB only (no laser, no LLM): the ring is pure topology.
"""

from __future__ import annotations

from app.bridge import graphstore
from realtime import ablation

from .conftest import requires_falkordb

# Dedicated lowercase keys (graphstore allowlist: ^palimpsest(_[a-z0-9]+)*$),
# distinct from the module defaults so a test run never collides with a live
# demo invocation of the /ablation endpoint.
WARM = "palimpsest_ablationtestwarm"
COLD = "palimpsest_ablationtestcold"


def _cleanup() -> None:
    for gk in (WARM, COLD):
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)


@requires_falkordb
def test_same_event_opposite_verdict(run):
    """WARM (history + new event) fires -> escalate; COLD (new event only)
    does not fire -> dismiss. Same event, same query, opposite verdict."""
    _cleanup()
    try:
        res = run(ablation.run_ablation(warm_graph=WARM, cold_graph=COLD))

        assert res["ok"] is True
        assert res["opposite_verdict"] is True

        # WARM: the ring is real — three distinct actors, two distinct pages,
        # real Path objects, and FalkorDB's own run_time.
        assert res["warm"]["fired"] is True
        assert res["warm"]["ring_count"] >= 1
        assert res["warm"]["should_open_case"] is True
        assert res["warm"]["paths"], "WARM must return real ring Path objects"
        ring = res["warm"]["rings"][0]
        assert len(set(ring["actors"])) == 3
        assert len(set(ring["pages"])) == 2
        assert isinstance(res["warm"]["run_time_ms"], (int, float))

        # COLD: the SAME event, but no history — the ring cannot close.
        assert res["cold"]["fired"] is False
        assert res["cold"]["ring_count"] == 0

        assert res["verdict_warm"] == "escalate"
        assert res["verdict_cold"] == "dismiss"

        # Both verdicts were computed over the SAME new event.
        assert res["event"]["actor"] == "SableWeather"
        assert res["event"]["page"] == "Regional power grid"
    finally:
        _cleanup()


@requires_falkordb
def test_cold_has_only_the_new_event(run):
    """The discriminator is HISTORY, not the event: COLD holds exactly one
    edit (the new event), WARM holds the full four-edit corpus."""
    _cleanup()
    try:
        res = run(ablation.run_ablation(warm_graph=WARM, cold_graph=COLD))
        assert res["warm"]["seeded"]["ingested"] == 4
        assert res["cold"]["seeded"]["ingested"] == 1

        cold_edits, _ms, _h = graphstore.query(
            "MATCH (:Actor)-[e:EDITED]->(:Page) RETURN count(e)",
            graph_key=COLD, read_only=True,
        )
        warm_edits, _ms2, _h2 = graphstore.query(
            "MATCH (:Actor)-[e:EDITED]->(:Page) RETURN count(e)",
            graph_key=WARM, read_only=True,
        )
        assert cold_edits[0][0] == 1, "COLD is only the new event"
        assert warm_edits[0][0] == 4, "WARM is history + the new event"
    finally:
        _cleanup()
