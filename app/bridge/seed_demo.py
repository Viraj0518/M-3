"""Seed the warm graph with a REAL co-edit ring, so the projector has something
true to draw.

This is not fixture data pretending to be a demo — it is the same SHAPE the ring
detector is written against (``app/bridge/tests/test_ring.py::seed_ring``):
three DISTINCT actors, two DISTINCT pages, four ``:EDITED`` edges inside one
window, where no individual edit crosses any threshold. Seeded here so
``GET /ring`` on a freshly-started stack FIRES on real rows rather than on a
hand-written response.

Two properties that make it safe to run repeatedly:

  * IDEMPOTENT. Actors/pages/edits are MERGEd on their natural keys and the
    edit edges are keyed by ``(actor, page, slot)``, so a second run updates
    rather than duplicating. A duplicated edit would inflate ``ring_count`` and
    make ``ring_score`` a lie.
  * RELATIVE TIMESTAMPS. Edits are placed at ``now - 300s .. now``, so the ring
    is inside the 720 s default window whenever it is seeded — a fixed epoch
    would drop out of every ``since``-bounded query the moment the demo box
    stayed up overnight.

The claims/handover are written THROUGH THE ROUTER (``bridge.dispatch``) under
three different bound agents, so ``author_agent`` on screen is a genuine
per-agent attribution and not a colour we assigned.

    .venv/bin/python -m app.bridge.seed_demo            # warm graph
    .venv/bin/python -m app.bridge.seed_demo --reset    # drop it first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List

from memory import config

from . import graphstore, identity
from . import server as bridge

#: (actor, page, seconds-before-now). Four edits, three actors, two pages:
#: Northstar-7 -> Transit <- VectorMint -> Grid <- SableWeather is the ring.
_EDITS: List[tuple] = [
    ("Northstar-7", "Metropolitan Transit Authority", 300, 120, "transit edit"),
    ("VectorMint", "Metropolitan Transit Authority", 220, -40, "transit revert"),
    ("VectorMint", "Regional power grid", 140, 88, "grid capacity"),
    ("SableWeather", "Regional power grid", 60, 15, "grid outage map"),
    # Two off-ring edits: texture, and they prove the detector is selecting a
    # SHAPE rather than reporting every edit it can see.
    ("Northstar-7", "Water treatment network", 240, 9, "water notice"),
    ("SableWeather", "Civic alert protocol", 30, 3, "civic alias"),
]

_CLAIMS: List[tuple] = [
    (
        "watcher",
        "Four edits across two pages closed inside a 12 minute window — no single "
        "edit crosses any byte threshold.",
        "note",
    ),
    (
        "analyst",
        "Prior campaign signature matched: the same three actors co-edited the "
        "transit and grid pages during the March incident.",
        "note",
    ),
    (
        "commander",
        "Escalate: the ring is the evidence, not any individual edit. Requesting "
        "human approval before any external action.",
        "decision",
    ),
]


def _edit_cypher() -> str:
    return (
        "MERGE (a:Actor {name: $actor}) "
        "ON CREATE SET a.created_ts = timestamp() "
        "MERGE (p:Page {title: $page}) "
        "ON CREATE SET p.created_ts = timestamp() "
        "MERGE (a)-[e:EDITED {slot: $slot}]->(p) "
        "SET e.ts = $ts, e.bytes = $bytes, e.summary = $summary "
        "RETURN a.name, p.title, e.ts"
    )


def seed_ring(graph_key: str, *, now: float) -> Dict[str, Any]:
    graphstore.ensure_indexes(graph_key)
    written = 0
    for slot, (actor, page, ago, byte_delta, summary) in enumerate(_EDITS):
        graphstore.mutate(
            _edit_cypher(),
            {
                "actor": actor,
                "page": page,
                "slot": slot,
                "ts": now - ago,
                "bytes": byte_delta,
                "summary": summary,
            },
            graph_key=graph_key,
        )
        written += 1
    return {"edits": written, "actors": 3, "pages": 4}


async def seed(graph_key: str = None, *, reset: bool = False) -> Dict[str, Any]:
    key = graphstore.check_graph_key(graph_key)
    if reset:
        graphstore.drop_graph(key)
        graphstore.forget_ensured(key)

    now = time.time()
    ring = await asyncio.to_thread(seed_ring, key, now=now)

    async def as_agent(agent: str, verb: str, args: dict) -> dict:
        identity.bind(None, agent)
        return await bridge.dispatch(verb, dict(args, graph=key), is_stdio=True, surface="cli")

    claim_ids: List[str] = []
    for agent, text, block_type in _CLAIMS:
        result = await as_agent(
            agent,
            "remember",
            {"content": text, "block_type": block_type, "labels": ["Claim"], "ts": now},
        )
        if result.get("ok"):
            claim_ids.append(result["id"])

    edges = 0
    if len(claim_ids) >= 3:
        for from_id, to_id, relation in (
            (claim_ids[2], claim_ids[0], "derived_from"),
            (claim_ids[2], claim_ids[1], "supports"),
        ):
            rel = await as_agent(
                "commander",
                "relate",
                {"from_id": from_id, "to_id": to_id, "relation": relation},
            )
            edges += 1 if rel.get("ok") else 0

    handover = await as_agent(
        "watcher",
        "handover_write",
        {
            "agent_id": "watcher",
            "to_agent": "commander",
            "role": "edge watcher",
            "summary": "Ring closed on transit + grid; case is ready for a ruling.",
            "next_steps": "Ask for approval, then fire the action with provenance.",
            "checkpoint": {"topic": config.TOPIC_SIGNAL_RAW, "offset": 0},
        },
    )
    identity.clear()

    snapshot = await bridge.dispatch("graph", {"graph": key}, is_stdio=True, surface="cli")
    fired = await bridge.dispatch("ring", {"graph": key}, is_stdio=True, surface="cli")

    return {
        "graph_key": key,
        "ring_seed": ring,
        "claims": claim_ids,
        "claim_edges": edges,
        "handover_id": handover.get("handover_id"),
        "node_count": (snapshot.get("meta") or {}).get("node_count"),
        "edge_count": (snapshot.get("meta") or {}).get("edge_count"),
        "ring_fired": fired.get("fired"),
        "ring_count": fired.get("ring_count"),
        "ring_score": fired.get("ring_score"),
    }


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", default=config.GRAPH_WARM)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="GRAPH.DELETE the key first (never do this to the warm graph mid-demo)",
    )
    args = parser.parse_args(argv)
    out = asyncio.run(seed(args.graph, reset=args.reset))
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ring_fired") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
