"""[6] THE REAL ABLATION — same new event, two differently-seeded graphs,
OPPOSITE mechanical verdict (GOAL victory condition 1, NEVER cut).

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS, AND WHAT IT IS NOT
════════════════════════════════════════════════════════════════════════════
``realtime/replay.py`` proves REPRODUCIBILITY: replay-from-0 re-derives the
byte-identical graph (``digest_warm == digest_cold``). That is a real and
valuable property — but it is NOT the ablation, and asserting graph EQUALITY is
the opposite of asserting an opposite verdict. Two identical graphs give the
SAME answer, by construction.

THE ABLATION is the headline beat: take ONE target "new event" — the Nth
co-edit that completes a ring — and feed it to two graphs that differ ONLY in
their history:

  * WARM  = the full historical co-edit corpus (the prior ring edits) + the new
            event.  The 3-hop ring Cypher FIRES  -> verdict "escalate".
  * COLD  = ONLY the new event, no history.       The SAME ring Cypher does NOT
            fire (there is no chain to close)      -> verdict "dismiss".

Same event. Same query. Opposite verdict — and the reason is MECHANICAL
(graph topology), not an LLM mood. It is topology-only: NO LLM, NO API key, no
network. The ring query is pure Cypher and runs in milliseconds.

════════════════════════════════════════════════════════════════════════════
THE REUSE THAT MAKES IT HONEST
════════════════════════════════════════════════════════════════════════════
The verdict on BOTH graphs is computed by the SAME code the projector and the
demo call: ``app.bridge.server.dispatch("ring", ...)`` — the exact ``_RING_CYPHER``
in ``_h_ring``. There is no second ring implementation here to drift from it.
The corpus is ingested through the SAME ``realtime.graph_writer.GraphWriter``
that the live firehose uses, so the ablation runs the production write path, not
a bespoke one.

Dedicated graph keys (``palimpsest_ablation_warm`` / ``palimpsest_ablation_cold``)
are used so the beat is repeatable and NON-DESTRUCTIVE to the live demo graphs
``palimpsest`` / ``palimpsest_cold``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from app.bridge import graphstore
from app.bridge import server as bridge

from . import graph_writer as gw

# ─── the ring corpus (same SHAPE the ring detector is written against) ───────
# Three DISTINCT actors, two DISTINCT pages, four :EDITED edges inside one
# window. No individual edit crosses any threshold; the SHAPE is the signal.
#
#   Northstar-7 -> Transit <- VectorMint -> Grid <- SableWeather
#
# The first three edits are HISTORY. The fourth — SableWeather editing the grid
# page — is THE NEW EVENT: the move that closes the ring.
_ACTOR_A = "Northstar-7"
_ACTOR_B = "VectorMint"
_ACTOR_C = "SableWeather"
_PAGE_A = "Metropolitan Transit Authority"
_PAGE_B = "Regional power grid"

#: 12 minutes — the synthesis ring beat (matches gw.CO_EDIT_WINDOW_S / the /ring
#: default). The four edits below span ~240 s, comfortably inside it.
RING_WINDOW_S = gw.CO_EDIT_WINDOW_S

#: Dedicated, repeatable, NON-DESTRUCTIVE ablation keys (never the live demo
#: graphs). Both match graphstore's ``palimpsest(_[a-z0-9]+)*`` allowlist.
WARM_GRAPH = "palimpsest_ablation_warm"
COLD_GRAPH = "palimpsest_ablation_cold"


def _event(actor: str, page: str, ts: float, *, event_id: str, comment: str,
           bytes_new: int = 100, bytes_old: int = 0, wiki: str = "demowiki") -> Dict[str, Any]:
    """A recentchange-shaped edit, so the SAME ``graph_writer.map_event`` path
    the live firehose uses ingests it — no bespoke seeding shortcut."""
    return {
        "type": "edit",
        "user": actor,
        "title": page,
        "wiki": wiki,
        "server_name": wiki,
        "meta": {"id": event_id},
        "timestamp": ts,
        "length": {"new": bytes_new, "old": bytes_old},
        "comment": comment,
    }


def build_corpus(now: float) -> Dict[str, Any]:
    """The historical co-edit corpus + the one new event that completes it.

    Relative timestamps (``now - ...``) keep the whole ring inside the window
    whenever it is run — a fixed epoch would fall out of the window overnight.
    """
    history = [
        _event(_ACTOR_A, _PAGE_A, now - 300, event_id="ablation-h1", comment="transit edit"),
        _event(_ACTOR_B, _PAGE_A, now - 220, event_id="ablation-h2", comment="transit revert", bytes_new=60, bytes_old=100),
        _event(_ACTOR_B, _PAGE_B, now - 140, event_id="ablation-h3", comment="grid capacity"),
    ]
    new_event = _event(_ACTOR_C, _PAGE_B, now - 60, event_id="ablation-new", comment="grid outage map")
    return {"history": history, "new_event": new_event}


async def _seed(graph_key: str, events: List[Dict[str, Any]], *, reset: bool) -> Dict[str, Any]:
    """Ingest ``events`` into ``graph_key`` through the real GraphWriter.

    Gate OFF (``use_gate=False``, ``gate=None``): the ablation is about topology,
    not novelty, and every seeded edit must land so the ring is deterministic.
    """
    gk = graphstore.check_graph_key(graph_key)
    if reset:
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)
    writer = gw.GraphWriter(graph_key=gk, gate=None, use_gate=False, author_agent=gw.DEFAULT_AUTHOR)
    for ev in events:
        await writer.write(ev)
    return writer.stats.as_dict()


async def _ring(graph_key: str, window_s: int) -> Dict[str, Any]:
    """Run the SAME ring verdict the projector/demo run, through the router."""
    return await bridge.dispatch(
        "ring",
        {"graph": graph_key, "window_s": window_s},
        is_stdio=True,
        surface="cli",
    )


async def run_ablation(
    *,
    warm_graph: Optional[str] = None,
    cold_graph: Optional[str] = None,
    window_s: int = RING_WINDOW_S,
    reset: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """THE ablation. Seed WARM (history + new event) and COLD (new event only),
    run the SAME ring Cypher against both, and return the opposite-verdict proof.

    The falsifiable claim: ``warm.fired is True`` AND ``cold.fired is False``.
    """
    now = float(now) if now is not None else time.time()
    warm = graphstore.check_graph_key(warm_graph or WARM_GRAPH)
    cold = graphstore.check_graph_key(cold_graph or COLD_GRAPH)

    corpus = build_corpus(now)
    new_event = corpus["new_event"]

    warm_stats = await _seed(warm, corpus["history"] + [new_event], reset=reset)
    cold_stats = await _seed(cold, [new_event], reset=reset)

    warm_ring = await _ring(warm, window_s)
    cold_ring = await _ring(cold, window_s)

    warm_fired = bool(warm_ring.get("fired"))
    cold_fired = bool(cold_ring.get("fired"))
    opposite = warm_fired and not cold_fired

    return {
        "ok": opposite,
        "opposite_verdict": opposite,
        "event": {
            "actor": new_event["user"],
            "page": new_event["title"],
            "event_id": new_event["meta"]["id"],
            "ts": new_event["timestamp"],
            "summary": new_event["comment"],
            "role": "the 4th co-edit — the move that completes the ring",
        },
        "warm": {
            "graph_key": warm,
            "fired": warm_fired,
            "ring_count": warm_ring.get("ring_count"),
            "ring_score": warm_ring.get("ring_score"),
            "should_open_case": warm_ring.get("should_open_case"),
            "run_time_ms": warm_ring.get("run_time_ms"),
            "rings": warm_ring.get("rings"),
            "paths": warm_ring.get("paths"),
            "seeded": warm_stats,
        },
        "cold": {
            "graph_key": cold,
            "fired": cold_fired,
            "ring_count": cold_ring.get("ring_count"),
            "ring_score": cold_ring.get("ring_score"),
            "run_time_ms": cold_ring.get("run_time_ms"),
            "seeded": cold_stats,
        },
        "verdict_warm": "escalate" if warm_fired else "dismiss",
        "verdict_cold": "escalate" if cold_fired else "dismiss",
        "window_s": window_s,
        "note": (
            "Same new event, two differently-seeded graphs. WARM holds the "
            "historical co-edit corpus so the 3-hop ring Cypher FIRES (escalate); "
            "COLD holds only the new event so the SAME query does NOT fire "
            "(dismiss). Topology-only: no LLM, no API key, pure Cypher."
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warm-graph", default=WARM_GRAPH)
    parser.add_argument("--cold-graph", default=COLD_GRAPH)
    parser.add_argument("--window-s", type=int, default=RING_WINDOW_S)
    parser.add_argument("--no-reset", action="store_true", help="do not drop the ablation keys first")
    args = parser.parse_args(argv)

    out = asyncio.run(
        run_ablation(
            warm_graph=args.warm_graph,
            cold_graph=args.cold_graph,
            window_s=args.window_s,
            reset=not args.no_reset,
        )
    )
    print(json.dumps(out, indent=2, default=str))
    # Exit non-zero unless the opposite-verdict claim actually held.
    return 0 if out.get("opposite_verdict") else 1


__all__ = [
    "RING_WINDOW_S",
    "WARM_GRAPH",
    "COLD_GRAPH",
    "build_corpus",
    "run_ablation",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
