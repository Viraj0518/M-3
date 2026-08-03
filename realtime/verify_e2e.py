"""End-to-end verification harness — produces the GATE-LASER-replay receipt.

Runs the whole spine against LIVE iggy + FalkorDB, with the file source (the
wifi-off critical path) and the deterministic test-embedder (no API keys):

  1. file://demo/seed_replay.ndjson  -> LaserData signal.raw   (edge tap)
  2. consumer group (all partitions) -> gate -> FalkorDB       (warm build)
  3. replay from offset 0            -> gate -> FalkorDB        (cold build)
  4. ASSERT warm.digest == cold.digest                          (the rewind proof)
  5. additively enrich the canonical warm `palimpsest`          (demo graph)
  6. replay-from-0 into canonical `palimpsest_cold`             (demo A/B target)

Prints a machine-checkable JSON receipt to stdout.

    python -m realtime.verify_e2e --limit 800
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict

from app.bridge import graphstore
from memory import config
from realtime import laser_io, pipeline, producer, replay

# Fresh, reproducible keys for the clean digest-parity proof (never the seeded
# demo warm graph, which carries a hand-built ring corpus we must not clobber).
GATE_WARM = "palimpsest_gatewarm"
GATE_COLD = "palimpsest_gatecold"


async def run(*, topic: str, limit: int, enrich_canonical: bool) -> Dict[str, Any]:
    started = time.time()
    log = await laser_io.connect()
    caps = log.require_log_only()

    for gk in (GATE_WARM, GATE_COLD):
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)

    # 1. EDGE TAP — file source -> signal.raw (same code path as live SSE)
    produced = await producer.produce("file://" + str(config.SEED_REPLAY_PATH), topic=topic, limit=limit, log=log)

    # 2-4. warm (consumer group) vs cold (replay-from-0), clean fresh keys
    parity = await replay.verify_replay_parity(
        log, topic=topic, warm_graph=GATE_WARM, cold_graph=GATE_COLD,
        group="gate-laser-%d" % int(time.time()),
    )

    # a KNOWN wiki edit is present in the warm graph
    known, _ms, _h = graphstore.query(
        "MATCH (a:Actor)-[e:EDITED]->(p:Page)-[:ON]->(w:Wiki) "
        "RETURN a.name, p.title, w.code ORDER BY e.ts LIMIT 1",
        graph_key=GATE_WARM, read_only=True,
    )
    known_edit = (
        {"actor": known[0][0], "page": known[0][1], "wiki": known[0][2]} if known else None
    )

    canonical: Dict[str, Any] = {"skipped": True}
    if enrich_canonical:
        # 5. additively enrich the canonical warm graph from the same log
        warm_live = await pipeline.run_live_stream(
            log, topic=topic, graph_key=config.GRAPH_WARM,
            group="canonical-warm-%d" % int(time.time()),
        )
        # 6. replay-from-0 into the canonical cold A/B target
        cold_live = await replay.replay_into_cold(
            log, topic=topic, cold_graph=config.GRAPH_COLD, reset=True
        )
        canonical = {
            "warm_graph": config.GRAPH_WARM,
            "warm_node_count": warm_live["digest"]["node_count"],
            "cold_graph": config.GRAPH_COLD,
            "cold_node_count": cold_live["digest"]["node_count"],
        }

    return {
        "ok": bool(parity["match"] and parity["records_match"] and parity["warm"]["digest"]["node_count"] > 0),
        "transport": "laser-sdk (Log primitive only) over iggy 127.0.0.1:8090, stream={0}".format(config.LASER_STREAM),
        "capabilities_advertised": caps,
        "capabilities_used": ["Log: publish", "Log: consumer_group", "Log: replay-from-offset"],
        "topic": topic,
        "produced": produced,
        "warm_build": {
            "read_via": "consumer group (all partitions, commit-after-side-effects)",
            "node_count": parity["warm"]["digest"]["node_count"],
            "edge_count": parity["warm"]["digest"]["edge_count"],
            "gate_stats": parity["warm"]["stats"],
            "digest": parity["warm_digest"],
            "known_edit_present": known_edit,
        },
        "cold_build": {
            "read_via": "replay cursor from offset 0",
            "node_count": parity["cold"]["digest"]["node_count"],
            "edge_count": parity["cold"]["digest"]["edge_count"],
            "digest": parity["cold_digest"],
        },
        "parity": {
            "digests_match": parity["match"],
            "same_event_set": parity["records_match"],
            "group_record_count": parity["group_record_count"],
            "replay_record_count": parity["replay_record_count"],
        },
        "canonical_demo_graphs": canonical,
        "elapsed_s": round(time.time() - started, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="realtime.verify_e2e")
    ap.add_argument("--topic", default=config.TOPIC_SIGNAL_RAW)
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--no-canonical", action="store_true",
                    help="skip additively enriching the canonical palimpsest/palimpsest_cold graphs")
    args = ap.parse_args(argv)
    receipt = asyncio.run(run(topic=args.topic, limit=args.limit, enrich_canonical=not args.no_canonical))
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
