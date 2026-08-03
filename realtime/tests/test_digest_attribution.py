"""The graph digest is ATTRIBUTION-SENSITIVE — the honest-receipt fix.

The reproducibility claim is "byte-identical ATTRIBUTED graph". Before the fix
``digest_graph`` hashed only topology (label + domain key), so a replay that
re-derived the same shape under a DIFFERENT ``author_agent`` would false-pass the
digest match. These tests prove the digest is now load-bearing:

  * SAME attribution  -> digest MATCHES  (a true same-attribution replay still
    reproduces exactly, so ``test_replay_parity`` stays green).
  * DIFFERENT / PERTURBED attribution -> digest DIFFERS (the mutation test).

Live against FalkorDB only (no laser needed — ``build_canonical`` takes an
in-memory record set).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.bridge import graphstore
from realtime import pipeline

from .conftest import requires_falkordb

A = "palimpsest_digattra"
B = "palimpsest_digattrb"
C = "palimpsest_digattrc"


def _records(now: float) -> List[Dict[str, Any]]:
    def ev(actor: str, page: str, ts: float, eid: str, off: int) -> Dict[str, Any]:
        return {
            "partition": 0,
            "offset": off,
            "value": {
                "type": "edit", "user": actor, "title": page, "wiki": "demowiki",
                "meta": {"id": eid}, "timestamp": ts,
                "length": {"new": 100, "old": 0}, "comment": "edit " + eid,
            },
        }
    return [
        ev("Alpha", "Page One", now - 100, "d1", 0),
        ev("Bravo", "Page One", now - 80, "d2", 1),
        ev("Bravo", "Page Two", now - 60, "d3", 2),
    ]


def _cleanup() -> None:
    for gk in (A, B, C):
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)


@requires_falkordb
def test_same_attribution_matches_different_differs(run):
    """Two builds under the SAME author hash identically; a build under a
    DIFFERENT author does not. Attribution is IN the digest."""
    _cleanup()
    try:
        recs = _records(time.time())
        a = run(pipeline.build_canonical(recs, graph_key=A, use_gate=False, author_agent="watcher"))
        b = run(pipeline.build_canonical(recs, graph_key=B, use_gate=False, author_agent="watcher"))
        c = run(pipeline.build_canonical(recs, graph_key=C, use_gate=False, author_agent="analyst"))

        da = a["digest"]["digest"]
        db = b["digest"]["digest"]
        dc = c["digest"]["digest"]

        assert da == db, "same topology AND same author must reproduce the digest"
        assert da != dc, "same topology but a DIFFERENT author must change the digest"
    finally:
        _cleanup()


@requires_falkordb
def test_perturbing_author_agent_changes_digest(run):
    """The mutation test: build a graph, digest it, flip ONE node's
    author_agent, re-digest. The digest MUST change — proving it is
    load-bearing for the 'byte-identical attributed graph' receipt."""
    _cleanup()
    try:
        recs = _records(time.time())
        built = run(pipeline.build_canonical(recs, graph_key=A, use_gate=False, author_agent="watcher"))
        before = built["digest"]["digest"]

        graphstore.mutate(
            "MATCH (a:Actor) WITH a ORDER BY a.name LIMIT 1 "
            "SET a.author_agent = 'tampered'",
            graph_key=A,
        )
        after = pipeline.digest_graph(A)["digest"]

        assert before != after, "perturbing author_agent must change the digest"
    finally:
        _cleanup()
