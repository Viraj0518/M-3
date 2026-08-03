"""PUBLIC-EXPOSURE GUARD — one env var between a demo and an open write surface.

WHY THIS EXISTS
────────────────────────────────────────────────────────────────────────────
The bridge has no authentication on ``main``. ``identity.py`` is explicitly an
ATTRIBUTION layer, not an AUTHENTICATION one: its own precedence ladder ends at
the ``x-palimpsest-agent`` header, which any caller can set to any string. The
verified-identity lane (``app/bridge/auth.py`` + ``auth/``, OAuth 2.1 + PKCE)
lives on the ``auth`` branch and is NOT merged yet.

Meanwhile the bridge is already reachable over public HTTPS. That combination —
public URL, no auth, `POST /v1/remember` and `/mcp` both wide open — means any
stranger can write into the demo graph and choose whose name is on the write.

This module is the stopgap: ONE environment variable that refuses mutating
verbs (or every verb) at the single dispatch chokepoint, so a public endpoint
can be made safe in one deploy rather than waiting on the auth merge.

IT IS OFF BY DEFAULT AND CHANGES NOTHING UNLESS SET.

WHAT `readonly` DOES NOT CLOSE — read this before trusting it
────────────────────────────────────────────────────────────────────────────
It refuses mutating VERBS. It is not a sandbox, and two holes survive it:

* **Reads are still anonymous.** Everything in the graph is world-readable to
  anyone who can reach the URL. That is the whole point of `closed`.
* **Read verbs still bootstrap indexes.** `_h_recall` / `_h_graph` / `_h_ring`
  call `_ensure(graph_key)` → `graphstore.ensure_indexes()`, which issues CREATE
  INDEX. `graphstore.check_graph_key` is a REGEX allowlist
  (`^palimpsest(_[a-z0-9]+)*$`), not an enumeration, so an anonymous caller can
  pass `?graph=palimpsest_whatever` on a read and cause an empty graph plus its
  indexes to be created. It writes no DATA, but it is unbounded graph creation.
  Closing it properly means enumerating the permitted keys or skipping `_ensure`
  under the guard — deliberately NOT done here, because either change alters
  behaviour on the default path, and this module's contract is that it does not.

So this is a blunt instrument and NOT a substitute for the auth lane. It buys
"a stranger cannot write to the graph or pick whose name is on the write",
which is the acute problem. Merge the auth PR.

WHERE IT RUNS
────────────────────────────────────────────────────────────────────────────
``server.dispatch`` — the ONE function every surface enters through. Guarding
there is why one check covers every NETWORK surface at once; guarding in an HTTP
middleware would cover REST only, because an MCP tool call is a JSON-RPC body
inside a single POST to ``/mcp`` and the verb is not visible in the path.

LOCAL SURFACES ARE EXEMPT, AND THAT IS THE POINT
────────────────────────────────────────────────────────────────────────────
``surface="cli"`` is not guarded. This is a PUBLIC-EXPOSURE guard: to reach the
CLI you already have shell access on the box, so refusing it protects nothing
and breaks something real — ``app/bridge/seed_demo.py`` and
``realtime/ablation.py`` both drive the router with ``surface="cli"``, and
``fly/entrypoint.sh`` runs ``python -m app.bridge.seed_demo`` at boot. Guarding
that path would boot a guarded deployment with an EMPTY warm graph, i.e. a dead
demo, which is how a safety feature gets reverted instead of adopted.

``surface`` is never caller-controlled: ``rest.py`` hardcodes ``"rest"`` and
``server.py``'s MCP handler hardcodes ``"mcp"``. An unrecognised surface — and
``None`` — is treated as a network surface, so the exemption cannot be reached
by accident.

WHY UNKNOWN VERBS ARE TREATED AS WRITES
────────────────────────────────────────────────────────────────────────────
:data:`READ_VERBS` is an ALLOWLIST, not a denylist. A verb added to the table
tomorrow is classified as mutating until someone deliberately lists it as a
read. The failure mode of the opposite default is a new write verb silently
staying open on a public URL, which is the exact bug this module exists to
prevent. ``test_guard.py`` asserts every verb in the live table is classified.
"""

from __future__ import annotations

import os
from typing import Optional, Set

#: The env var. Unset or ``off`` == this module is inert.
ENV_VAR = "PALIMPSEST_PUBLIC_MODE"

#: No guard at all. The default, and the only value that preserves today's
#: behaviour byte for byte.
MODE_OFF = "off"

#: Reads answer normally; every mutating verb is refused with ``PUBLIC_READONLY``.
#: This is the mode for a public demo URL before the auth PR merges.
MODE_READONLY = "readonly"

#: Every verb is refused with ``PUBLIC_CLOSED``. For parking a public hostname
#: that must resolve and answer ``/health`` but must not serve the graph.
MODE_CLOSED = "closed"

MODES = (MODE_OFF, MODE_READONLY, MODE_CLOSED)

#: Surfaces that are NOT reachable over the network and are therefore never
#: guarded. See the module docstring: this is what lets the boot-time seeder run
#: on a deployment that is otherwise refusing writes. Anything not in this set,
#: including ``None``, is treated as a network surface.
LOCAL_SURFACES = frozenset({"cli"})

#: Verbs that only READ. Everything else — including anything added to
#: ``_VERB_DISPATCH`` later — is treated as mutating. Note that HTTP method is
#: NOT a usable proxy here: ``recall`` and ``ring`` are both POST and both
#: read-only, because their payloads are too large for a query string.
READ_VERBS: Set[str] = {
    "recall",
    "ring",
    "graph",
    "stream_tail",
    "handover_read",
}

#: Verbs known to mutate state, either in the graph or in the world. Declared
#: explicitly so ``test_guard.py`` can assert the two sets partition the table
#: and neither drifts silently.
#:
#: ``ask`` is here despite being a stub today: its whole purpose is to become
#: the human-approval gate in front of ``act``, and a verb that will fire a
#: decision card at a human is not something a stranger gets to trigger.
#:
#: ``ablation`` LOOKS like a read — it answers a question and touches neither
#: demo graph — but ``realtime.ablation.run_ablation`` runs with ``reset=True``,
#: which ``graphstore.drop_graph()``s and re-seeds ``palimpsest_ablation_warm``
#: and ``palimpsest_ablation_cold`` on EVERY call. That is a destructive write
#: plus a full corpus ingest per request: as a "read" on a public URL it would
#: be an anonymous DoS lever. Consequence to be aware of: under ``readonly`` the
#: ablation demo is unavailable, so a box that needs to drive that beat live must
#: run with the guard ``off`` behind a trusted network.
WRITE_VERBS: Set[str] = {
    "remember",
    "relate",
    "ablation",
    "stream_publish",
    "stream_replay",
    "act",
    "handover_write",
    "ask",
}


def mode() -> str:
    """The effective mode, read from the environment on EVERY call.

    Deliberately not cached at import time: a container that flips the variable
    and restarts gets the new value either way, but a test (and a future admin
    hook) can change it in-process without reimporting the module. The cost is
    one ``os.environ`` lookup per dispatch, which is noise next to a Cypher
    round trip.

    An UNRECOGNISED value is treated as :data:`MODE_CLOSED`, not as ``off``. A
    typo in the deployment config must fail safe — ``PALIMPSEST_PUBLIC_MODE=
    read-only`` (with a hyphen) silently leaving the write surface open is
    precisely the class of bug this module is for.
    """
    raw = (os.environ.get(ENV_VAR) or "").strip().lower()
    if not raw:
        return MODE_OFF
    if raw in MODES:
        return raw
    return MODE_CLOSED


def enabled() -> bool:
    """True when the guard will refuse anything at all."""
    return mode() != MODE_OFF


def is_read(verb: str) -> bool:
    """Whether ``verb`` is a known read. Unknown verbs are NOT reads."""
    return verb in READ_VERBS


def is_local(surface: Optional[str]) -> bool:
    """Whether ``surface`` is off-network and therefore exempt.

    ``None`` is NOT local. A caller that forgets to pass the surface gets the
    guarded behaviour, which is the safe direction to be wrong in.
    """
    return surface in LOCAL_SURFACES


def refusal(verb: str, *, surface: Optional[str] = None) -> Optional[dict]:
    """The ``err()``-shaped envelope refusing ``verb``, or ``None`` to allow it.

    Returns a plain dict rather than importing ``server.err`` so this module has
    no import cycle with the dispatcher that calls it. The shape is identical:
    ``ok`` / ``isError`` / ``code`` / ``error``, which is what every surface's
    status mapping and every client's envelope check already understand.
    """
    current = mode()
    if current == MODE_OFF:
        return None

    if is_local(surface):
        return None

    if current == MODE_CLOSED:
        return {
            "ok": False,
            "isError": True,
            "code": "PUBLIC_CLOSED",
            "error": (
                "this bridge is running with {0}=closed — every verb is "
                "refused. The endpoint is parked; it is not broken.".format(ENV_VAR)
            ),
            "verb": verb,
            "public_mode": current,
        }

    if is_read(verb):
        return None

    return {
        "ok": False,
        "isError": True,
        "code": "PUBLIC_READONLY",
        "error": (
            "{0!r} mutates state and this bridge is running with {1}=readonly. "
            "The unauthenticated write surface is deliberately closed until "
            "verified identity (OAuth 2.1 + PKCE) is merged and configured — "
            "until then any caller could both write to the graph and choose "
            "whose name is on the write.".format(verb, ENV_VAR)
        ),
        "verb": verb,
        "public_mode": current,
        "reads_still_allowed": sorted(READ_VERBS),
    }


def describe() -> dict:
    """A loggable, secret-free snapshot for ``/health`` and ``/``.

    Reporting the posture on the health endpoint is the point: a third party
    pointing an MCP client at this URL should be able to see, without guessing,
    whether writes will be accepted.
    """
    current = mode()
    return {
        "mode": current,
        "enforcing": current != MODE_OFF,
        "env": ENV_VAR,
        "writes_refused": current != MODE_OFF,
        "reads_refused": current == MODE_CLOSED,
        "exempt_surfaces": sorted(LOCAL_SURFACES),
    }


__all__ = [
    "ENV_VAR",
    "LOCAL_SURFACES",
    "MODES",
    "MODE_CLOSED",
    "MODE_OFF",
    "MODE_READONLY",
    "READ_VERBS",
    "WRITE_VERBS",
    "describe",
    "enabled",
    "is_local",
    "is_read",
    "mode",
    "refusal",
]
