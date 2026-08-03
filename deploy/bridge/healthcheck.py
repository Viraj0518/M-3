#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Container healthcheck for the PALIMPSEST bridge.

Exits 0 only when ``GET /health`` returns **200**, which per
``app/bridge/rest.py:403`` requires a real ``RETURN 1`` round-trip against
FalkorDB. Deliberately strict: a bridge that cannot see the graph is not
healthy, it is a liar.

The failure path matters as much as the success path. When the memory plane is
down the bridge answers **503** with ``code: "FALKORDB_UNAVAILABLE"``.
``urllib`` raises ``HTTPError`` on a 503, so that branch is caught explicitly
and the BODY is echoed to the healthcheck log — ``docker inspect`` then shows
the reason, not just a non-zero exit.

stdlib only, on purpose: python:3.12-slim ships no curl and no wget, and
adding one would put an apt-get (i.e. a network round-trip) in the build.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ.get("BRIDGE_PORT") or "8931"
URL = "http://127.0.0.1:{0}/health".format(PORT)
TIMEOUT_S = 4.0
#: Enough of the body to read the verdict, short enough not to spam
#: `docker inspect`'s health log (which keeps the last 5 probes).
BODY_CHARS = 400


def _summarise(raw: bytes) -> str:
    """One line: the fields that decide the verdict, or the raw head."""
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return raw.decode("utf-8", "replace")[:BODY_CHARS]
    if not isinstance(body, dict):
        return str(body)[:BODY_CHARS]
    falkor = body.get("falkordb") or {}
    return json.dumps(
        {
            "ok": body.get("ok"),
            "code": body.get("code"),
            "falkordb": {
                "reachable": falkor.get("reachable"),
                "endpoint": falkor.get("endpoint"),
                "latency_ms": falkor.get("latency_ms"),
                "error": falkor.get("error"),
            },
        },
        default=str,
    )[:BODY_CHARS]


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT_S) as response:
            raw = response.read()
            status = int(response.status)
            sys.stdout.write("{0} {1}\n".format(status, _summarise(raw)))
            return 0 if status == 200 else 1
    except urllib.error.HTTPError as exc:
        # THE 503 PATH. Not an error in the probe — an honest unhealthy verdict
        # from the bridge, carrying code=FALKORDB_UNAVAILABLE. Surface the body.
        raw = b""
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - a body is a nicety, not a requirement
            pass
        sys.stdout.write("{0} {1}\n".format(exc.code, _summarise(raw)))
        return 1
    except Exception as exc:  # noqa: BLE001 - connection refused, DNS, timeout
        sys.stderr.write("healthcheck: {0}: {1} ({2})\n".format(type(exc).__name__, exc, URL))
        return 1


if __name__ == "__main__":
    sys.exit(main())
