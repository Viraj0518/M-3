"""Shared fixtures. THE SUITE RUNS AGAINST THE LIVE FALKORDB CONTAINER.

There is no mock and that is deliberate: every bug this suite is written to
catch — the DISTANCE-not-similarity sort, the vector-index DDL that changed
between FalkorDB builds, the map-property type that does not exist, the
``OPTIONAL MATCH ... SET`` supersede — is a property of the REAL database.
A fake would have passed on all five while the demo failed on stage.

Isolation is by GRAPH KEY, never by mocking: everything writes to
``palimpsest_test`` (matched by graphstore's allowlist pattern) and every test
GRAPH.DELETEs it on the way in and the way out, so a failed run cannot poison
the next one and the warm graph is never touched.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.bridge import graphstore, identity  # noqa: E402
from app.bridge import server as bridge  # noqa: E402
from memory import config  # noqa: E402

#: The dedicated test graph key. NOT `palimpsest` and NOT `palimpsest_cold` —
#: a suite that truncates the warm graph would delete the demo.
TEST_GRAPH = "palimpsest_test"

assert TEST_GRAPH not in (config.GRAPH_WARM, config.GRAPH_COLD), (
    "the test graph key must never be the warm or the ablation graph"
)


def _live() -> bool:
    """Is the container actually up? A skipped suite must be visibly skipped,
    never a green run that tested nothing."""
    if not graphstore.FALKORDB_AVAILABLE:
        return False
    try:
        graphstore.query("RETURN 1", graph_key=TEST_GRAPH)
        return True
    except Exception:  # noqa: BLE001
        return False


requires_live_db = pytest.mark.skipif(
    not _live(),
    reason=(
        "FalkorDB is not reachable at {0}:{1} — start it: "
        "docker start palimpsest-falkordb".format(
            config.FALKORDB_HOST, config.FALKORDB_PORT
        )
    ),
)


@pytest.fixture()
def graph_key():
    """A pristine `palimpsest_test` graph, dropped before AND after."""
    graphstore.drop_graph(TEST_GRAPH)
    graphstore.forget_ensured(TEST_GRAPH)
    yield TEST_GRAPH
    graphstore.drop_graph(TEST_GRAPH)
    graphstore.forget_ensured(TEST_GRAPH)


@pytest.fixture()
def agent():
    """Bind the stdio session to a known agent, so `author_agent` assertions
    are about a real resolved identity rather than the unbound fallback."""
    identity.clear()
    identity.bind(None, "analyst")
    yield "analyst"
    identity.clear()


@pytest.fixture()
def call(graph_key, agent):
    """Invoke one verb THROUGH THE REAL ROUTER.

    Deliberately not calling handlers directly: the router is where the payload
    builder, the path substitution and the identity resolution happen, and the
    pre-registered bug class in server.py's header is a builder that silently
    reshapes the payload. Testing under the router is the only way that class
    of bug is visible.
    """

    async def _call(verb: str, args: dict = None):
        args = dict(args or {})
        args.setdefault("graph", graph_key)
        return await bridge.dispatch(verb, args, is_stdio=True, surface="cli")

    return _call


@pytest.fixture()
def run():
    """Drive one coroutine to completion (no pytest-asyncio mode dependency)."""

    def _run(coro):
        return asyncio.run(coro)

    return _run


@pytest.fixture()
def cypher(graph_key):
    """Raw Cypher against the test graph — used to SEED states the public verbs
    deliberately refuse to write (e.g. a closed handover), and to act as the
    UNGUARDED control that proves a guard is load-bearing."""

    def _q(statement: str, params: dict = None):
        rows, _ms, _h = graphstore.query(statement, params or {}, graph_key=graph_key)
        return rows

    return _q


def vec(seed: float) -> list:
    """A deterministic EMBED_DIM-wide unit-ish vector. The bridge never calls an
    embedding API, so tests supply their own — which is exactly the contract the
    handler exposes (`embedding` is a precomputed parameter)."""
    return [seed] + [0.0] * (config.EMBED_DIM - 1)


def vec_near(seed: float, nudge: float = 0.05) -> list:
    out = [seed, nudge] + [0.0] * (config.EMBED_DIM - 2)
    return out
