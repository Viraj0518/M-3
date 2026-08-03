"""[1] EDGE TAP — the only process that touches the public internet for ingest.

Reads Wikimedia's zero-auth recentchange firehose (live SSE) OR a captured
NDJSON file, THROUGH THE SAME CODE PATH, and publishes every edit to the
LaserData topic ``signal.raw`` keyed by ``wiki`` so per-wiki ordering survives
parallel consumers.

THE FILE SOURCE IS FIRST-CLASS, NOT BOLTED ON (plan/synthesis.json [1] +
pre_event_checklist: "conference wifi must never be on the demo's critical
path"). ``--source file://demo/seed_replay.ndjson`` and the live SSE URL both
feed the identical ``publish`` loop; the ONLY difference is the async iterator
that yields raw events.

    python -m realtime.producer --source file://demo/seed_replay.ndjson --limit 500
    python -m realtime.producer --source sse --limit 500 --timeout 30

``producer.init()`` is called for you inside ``laser_io`` BEFORE the first send
(SCHEMA.md trap 9). No secrets: the connection string comes from
``memory/config.laser_connection()``; the SSE URL is public and zero-auth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, AsyncIterator, Dict, Optional

from memory import config

from . import laser_io

DEFAULT_FILE_SOURCE = "file://" + str(config.SEED_REPLAY_PATH)


# ═══════════════════════════════════════════════════════════════════════════
# sources — each an async iterator of RAW recentchange dicts
# ═══════════════════════════════════════════════════════════════════════════

async def _iter_file(path: str, *, limit: Optional[int]) -> AsyncIterator[Dict[str, Any]]:
    """Yield events from a captured NDJSON firehose. One JSON object per line.

    Reads on a worker thread in bounded chunks so a 42 MB file never blocks the
    event loop, and yields cooperatively.
    """
    count = 0
    with open(path, "r", encoding="utf-8") as fh:
        while True:
            line = await asyncio.to_thread(fh.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except (TypeError, ValueError):
                continue
            yield evt
            count += 1
            if limit is not None and count >= limit:
                break
            if count % 500 == 0:
                await asyncio.sleep(0)  # cooperative yield


async def _iter_sse(url: str, *, limit: Optional[int], timeout: float) -> AsyncIterator[Dict[str, Any]]:
    """Yield events from the live Wikimedia EventStreams SSE endpoint.

    httpx streaming with a hard read timeout so a stalled connection can never
    wedge the demo — the watchdog note: never a silent blocking network read.
    """
    import httpx  # local import: the file path must not require httpx

    count = 0
    deadline = time.monotonic() + timeout if timeout else None
    to = httpx.Timeout(timeout or 30.0, read=timeout or 30.0)
    async with httpx.AsyncClient(timeout=to, headers={"User-Agent": "PALIMPSEST-edge-tap/0.1 (hackathon)"}) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for raw in resp.aiter_lines():
                if deadline and time.monotonic() > deadline:
                    break
                if not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                if not data:
                    continue
                try:
                    evt = json.loads(data)
                except (TypeError, ValueError):
                    continue
                yield evt
                count += 1
                if limit is not None and count >= limit:
                    break


def _resolve_source(source: str) -> str:
    """``file://...`` / ``sse`` / a bare path / the SSE URL -> a normalized tag."""
    if source in ("sse", "live", "wikimedia"):
        return "sse://" + config.WIKIMEDIA_SSE_URL
    if source.startswith("http://") or source.startswith("https://"):
        return "sse://" + source
    if source.startswith("file://"):
        return source
    return "file://" + source


async def _events(source: str, *, limit: Optional[int], timeout: float) -> AsyncIterator[Dict[str, Any]]:
    tag = _resolve_source(source)
    if tag.startswith("sse://"):
        async for e in _iter_sse(tag[len("sse://"):], limit=limit, timeout=timeout):
            yield e
    else:
        async for e in _iter_file(tag[len("file://"):], limit=limit):
            yield e


# ═══════════════════════════════════════════════════════════════════════════
# publish loop — SAME for both sources
# ═══════════════════════════════════════════════════════════════════════════

def _partition_key(evt: Dict[str, Any]) -> str:
    """key = wiki, so per-wiki ordering survives parallel consumers (design [1])."""
    return str(evt.get("wiki") or evt.get("server_name") or "unknown")


async def produce(
    source: str = DEFAULT_FILE_SOURCE,
    *,
    topic: str = config.TOPIC_SIGNAL_RAW,
    limit: Optional[int] = None,
    timeout: float = 30.0,
    log: Optional[laser_io.LaserLog] = None,
    progress_every: int = 200,
) -> Dict[str, Any]:
    """Publish every event from ``source`` to ``topic`` keyed by wiki.

    Returns a receipt: {published, wikis, source, topic, elapsed_s}. Idempotent
    at the graph layer (every downstream write is a MERGE), so re-running only
    grows the log — never corrupts the graph.
    """
    owns = log is None
    if log is None:
        log = await laser_io.connect()
        caps = log.require_log_only()
        print("[edge-tap] connected; laser capabilities (Log-only enforced): {0}".format(
            json.dumps(caps)), file=sys.stderr)

    published = 0
    wikis: Dict[str, int] = {}
    started = time.monotonic()
    tag = _resolve_source(source)
    print("[edge-tap] source={0} topic={1} limit={2}".format(tag, topic, limit), file=sys.stderr)

    async for evt in _events(source, limit=limit, timeout=timeout):
        key = _partition_key(evt)
        await log.publish(topic, evt, key=key)
        published += 1
        wikis[key] = wikis.get(key, 0) + 1
        if published % progress_every == 0:
            print("[edge-tap] published {0} events ({1} wikis)".format(published, len(wikis)), file=sys.stderr)

    elapsed = round(time.monotonic() - started, 3)
    print("[edge-tap] DONE: {0} events -> {1} in {2}s across {3} wikis".format(
        published, topic, elapsed, len(wikis)), file=sys.stderr)
    return {
        "ok": True,
        "published": published,
        "wikis": len(wikis),
        "top_wikis": dict(sorted(wikis.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "source": tag,
        "topic": topic,
        "elapsed_s": elapsed,
    }


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="realtime.producer", description="PALIMPSEST edge tap")
    ap.add_argument("--source", default=DEFAULT_FILE_SOURCE,
                    help="file://path | sse | an SSE URL. Default: the captured seed file.")
    ap.add_argument("--topic", default=config.TOPIC_SIGNAL_RAW)
    ap.add_argument("--limit", type=int, default=None, help="stop after N events")
    ap.add_argument("--timeout", type=float, default=30.0, help="SSE read timeout seconds")
    return ap.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    receipt = asyncio.run(produce(args.source, topic=args.topic, limit=args.limit, timeout=args.timeout))
    print(json.dumps(receipt, indent=2))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
