"""Small LaserData round-trip smoke test for local Stack or Cloud.

This deliberately exercises only the open Log primitive:

    connect -> explicit stream/topic -> ensure -> publish -> replay

It does not write FalkorDB or use LaserData's managed graph/memory surfaces.
Those boundaries stay explicit in the application architecture.

Examples::

    export LASER_CONNECTION_STRING='iggy:laser@127.0.0.1:8090'
    python -m realtime.laser_smoke

    LASER_CONNECTION_STRING='token@your-host.laserdata.cloud' \
      python -m realtime.laser_smoke --topic smoke.events
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from typing import Any, Dict, Optional

from memory import config

from . import laser_io


async def run(
    *,
    topic: str = "smoke.events",
    stream: Optional[str] = None,
    key: str = "smoke",
) -> Dict[str, Any]:
    """Publish one uniquely identified record and prove it can be replayed."""
    log = await laser_io.connect(stream=stream)
    event = {
        "event_id": "laser-smoke-" + uuid.uuid4().hex,
        "source": "memmotion",
        "kind": "stream_round_trip",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    await log.publish(topic, event, key=key)

    records = await log.drain_from_zero(topic, max_records=10_000)
    matches = [record for record in records if record.get("value", {}).get("event_id") == event["event_id"]]
    if not matches:
        raise RuntimeError("LaserData publish succeeded but replay did not return the smoke record")

    receipt = {
        "ok": True,
        "stream": stream or config.LASER_STREAM,
        "topic": topic,
        "event_id": event["event_id"],
        "replay_matches": len(matches),
        "record_sequence": matches[-1].get("seq"),
        "capabilities_advertised": log.require_log_only(),
        "capabilities_used": ["Log: ensure", "Log: publish", "Log: replay"],
    }
    return receipt


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="realtime.laser_smoke")
    parser.add_argument("--topic", default="smoke.events")
    parser.add_argument("--stream", default=None)
    parser.add_argument("--key", default="smoke")
    args = parser.parse_args(argv)
    receipt = asyncio.run(run(topic=args.topic, stream=args.stream, key=args.key))
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
