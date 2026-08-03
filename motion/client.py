"""Validate and run the PALIMPSEST RocketRide Wave pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from rocketride import RocketRideClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # documented invocation is `python3.12 motion/client.py`
from memory import config  # noqa: E402 -- single source of constants; do not re-derive here

EVENTS = ["task", "summary", "flow", "output", "sse"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default="Should this live case be escalated or dismissed?")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args()

    pipeline = json.loads((HERE / "palimpsest.pipe").read_text())
    client = RocketRideClient(uri=config.ROCKETRIDE_URL, auth=config.ROCKETRIDE_API_KEY)
    await client.connect()
    token = None
    try:
        validation = await client.validate(pipeline=pipeline)
        print(json.dumps({"validation": validation}, default=str))
        if args.validate_only:
            return 0
        run = await client.use(filepath=str(HERE / "palimpsest.pipe"), ttl=0, pipelineTraceLevel="full", env={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]})
        token = run["token"]
        await client.set_events(token, EVENTS)
        frames: list[object] = []

        async def on_sse(event_type: str, data: object) -> None:
            frame = {"type": event_type, "data": data}
            frames.append(frame)
            print(json.dumps({"sse": frame}, default=str))

        result = await client.send(token, args.prompt, on_sse=on_sse)
        receipt = {"events": EVENTS, "result": result, "sse": frames}
        if args.trace:
            args.trace.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
        print(json.dumps(receipt, default=str))
        return 0
    finally:
        try:
            if token:
                await client.terminate(token)
        finally:
            await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
