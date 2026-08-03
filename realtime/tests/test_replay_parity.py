"""The money beat, as a test: replay-from-0 re-derives the identical graph.

LIVE — produces a small slice of the captured firehose to a fresh topic through
the SAME producer code path the demo uses, then asserts that a consumer-group
build and a replay-from-0 build hash IDENTICALLY. This is the falsifiable claim
behind the ablation (GOAL victory condition 1).
"""

from __future__ import annotations

import time

from app.bridge import graphstore
from realtime import laser_io, producer, replay

from .conftest import requires_falkordb, requires_laser

WARM = "palimpsest_rtwarm"
COLD = "palimpsest_rtcold"


def _cleanup():
    for gk in (WARM, COLD):
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)


@requires_laser
@requires_falkordb
def test_replay_from_zero_matches_consumer_build(run):
    topic = "rttest.replay.%d" % int(time.time() * 1000)
    _cleanup()
    try:
        async def go():
            log = await laser_io.connect()
            # 1. produce 120 events from the file source (the demo's wifi-off path)
            rec = await producer.produce(
                "file://demo/seed_replay.ndjson", topic=topic, limit=120, log=log
            )
            assert rec["published"] == 120
            # 2. warm (consumer group from 0) vs cold (replay from 0)
            return await replay.verify_replay_parity(
                log, topic=topic, warm_graph=WARM, cold_graph=COLD,
                group="rttest-group-%d" % int(time.time()),
            )

        parity = run(go())
        assert parity["group_record_count"] == 120
        assert parity["replay_record_count"] == 120
        assert parity["records_match"] is True, "both read paths must see the same event set"
        assert parity["match"] is True, "replay-from-0 must re-derive the IDENTICAL graph"
        assert parity["warm_digest"] == parity["cold_digest"]
        assert parity["warm"]["digest"]["node_count"] > 0, "the warm graph must actually be populated"
        # gate did real work
        assert parity["warm"]["stats"]["ingested"] == 120
    finally:
        _cleanup()


@requires_laser
@requires_falkordb
def test_replay_into_cold_populates_from_empty(run):
    topic = "rttest.cold.%d" % int(time.time() * 1000)
    graphstore.drop_graph(COLD)
    graphstore.forget_ensured(COLD)
    try:
        async def go():
            log = await laser_io.connect()
            await producer.produce("file://demo/seed_replay.ndjson", topic=topic, limit=80, log=log)
            return await replay.replay_into_cold(log, topic=topic, cold_graph=COLD, reset=True)

        built = run(go())
        assert built["source"] == "replay-from-0"
        assert built["digest"]["node_count"] > 0
        rows, _ms, _h = graphstore.query(
            "MATCH (a:Actor)-[:EDITED]->(:Page) RETURN count(a)",
            graph_key=COLD, read_only=True,
        )
        assert rows[0][0] > 0, "a known EDITED relationship must be present after replay"
    finally:
        graphstore.drop_graph(COLD)
        graphstore.forget_ensured(COLD)
