"""Fixtures for the realtime suite. LIVE by design — against the running
laser-stack iggy (127.0.0.1:8090) and FalkorDB (127.0.0.1:6401).

The consumer INVARIANT tests use an in-process fake source (no broker), so they
run even when iggy is down; the gate, graph-writer and replay-parity tests are
skipped-with-a-reason when their live dependency is absent — never a green run
that tested nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The connection string is read from the environment ONLY — never hardcoded in
# repo code (hygiene rule: the LASER password lives in .env/.env.example, not in
# source). Export the exact `iggy:laser@...` string ./scripts/up prints before
# running the live tests:  export LASER_CONNECTION_STRING="iggy:laser@127.0.0.1:8090".
# When it is unset, the laser-dependent tests SKIP with a reason (never a false
# green), because laser_io.connect() raises without a connection string.

from app.bridge import graphstore  # noqa: E402
from memory import config  # noqa: E402
from realtime import laser_io  # noqa: E402


def _falkordb_live() -> bool:
    if not graphstore.FALKORDB_AVAILABLE:
        return False
    try:
        graphstore.query("RETURN 1", graph_key="palimpsest_rttest")
        return True
    except Exception:  # noqa: BLE001
        return False


def _laser_live() -> bool:
    if not laser_io.LASER_AVAILABLE:
        return False
    try:
        asyncio.run(laser_io.connect())
        return True
    except Exception:  # noqa: BLE001
        return False


requires_falkordb = pytest.mark.skipif(
    not _falkordb_live(),
    reason="FalkorDB not reachable at {0}:{1}".format(config.FALKORDB_HOST, config.FALKORDB_PORT),
)

requires_laser = pytest.mark.skipif(
    not _laser_live(),
    reason="laser-stack iggy not reachable at 127.0.0.1:8090 (or laser-sdk not installed)",
)


@pytest.fixture()
def rt_graph():
    """A pristine realtime test graph key, dropped before AND after."""
    key = "palimpsest_rttest"
    graphstore.drop_graph(key)
    graphstore.forget_ensured(key)
    yield key
    graphstore.drop_graph(key)
    graphstore.forget_ensured(key)


@pytest.fixture()
def run():
    def _run(coro):
        return asyncio.run(coro)
    return _run
