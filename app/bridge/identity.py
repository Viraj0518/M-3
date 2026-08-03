"""Per-agent identity in ONE bridge process — the sleeper feature.

PORTED NEAR-VERBATIM from
``unblock_mcp/src/unblock_mcp/http/session_persona.py`` (142 LOC), which was
chosen precisely because it has ZERO unblock coupling: it is a pure
``session-id -> selector`` map with no auth, no network, no macaroons, no DIDs.
The reference copy is at ``vendor/unblock-reuse/unblock_mcp/src/unblock_mcp/
http/session_persona.py``.

WHAT CARRIES OVER (verbatim, and it is the whole point):
  * The store holds a SELECTOR ONLY — a name. No resolved identity is cached,
    no secret material, no TTL. A binding can therefore never MASK an identity.
  * Keyed on the ``Mcp-Session-Id``, so N concurrent sessions in ONE process
    act as N DIFFERENT agents with ZERO cross-bleed. That is what lets one
    bridge serve every Guild specialist without N hand-built servers.
  * ``STDIO_SESSION`` is the synthetic key for the single-session stdio
    transport (one process = one agent at a time).
  * Dict get/set are atomic under the single asyncio event loop — no lock.

WHAT WAS DELIBERATELY DROPPED (manifest fence):
  * ``bound_with_durable_fallback`` and everything behind it. It reaches into
    ``http/persona_binding.py``, which the manifest names as a pure time sink:
    session_persona.py and persona_binding.py document each other as
    replacements in OPPOSITE directions, and porting that reconciliation buys
    us nothing. We picked session_persona.py and we stay picked.
  * ``grant_did`` / owned-set re-validation / the substrate 403 chokepoint —
    all Kaeva backend wire. PALIMPSEST takes ZERO runtime dependency on it.

WHAT WAS ADDED: the author_agent stamping helper. Every Cypher write carries
``author_agent`` = the acting selector. That is what turns "the analyst
asserted X, the watcher contradicted it" into a GRAPH FACT rendered as node
colour, instead of a caption on a slide.

MCP SESSION PIN (load-bearing, from the manifest's pre-registered bug class):
this whole layer keys on the ``Mcp-Session-Id`` header. MCP spec revision
2026-07-28 (SDK 2.x) REMOVES protocol-level sessions and that header. Hence the
``mcp>=1.28,<2`` pin. If a newer SDK is ever forced, switch to the per-request
header selector path — :func:`selector_from_headers` already supports it and
survives the change.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

# session_id -> agent selector (a NAME — whatever the graph writer stamps).
# NOT a resolved identity, NOT a secret.
_bindings: Dict[str, str] = {}

#: The synthetic session key for the single-session stdio transport (no
#: ``Mcp-Session-Id``). One process = one agent at a time.
STDIO_SESSION = "__stdio__"

#: The property name stamped on every node/edge write.
AUTHOR_PROP = "author_agent"

#: What we stamp when nothing is bound. An HONEST value — never silently
#: attribute an unbound write to a real agent.
UNBOUND_AGENT = "unbound"

#: Header a non-session transport (REST, CLI, a post-2.x MCP SDK) may use to
#: select an agent. Checked AFTER the session binding, so an explicitly bound
#: session always wins over a header a caller can set freely.
AGENT_HEADER = "x-palimpsest-agent"
SESSION_HEADER = "mcp-session-id"


def _key(session_id: Optional[str]) -> str:
    return session_id or STDIO_SESSION


def bind(session_id: Optional[str], agent_selector: str) -> None:
    """Bind ``session_id`` to ``agent_selector``. Overwrites any prior binding
    for the session (a session may re-declare); the selector is the value
    stamped as ``author_agent`` on every write this session makes."""
    if not agent_selector or not isinstance(agent_selector, str):
        raise ValueError("agent_selector must be a non-empty string")
    _bindings[_key(session_id)] = agent_selector


def bound(session_id: Optional[str]) -> Optional[str]:
    """The agent selector bound to ``session_id``, or ``None`` if the session
    has declared no agent."""
    return _bindings.get(_key(session_id))


def unbind(session_id: Optional[str]) -> None:
    """Drop the session's binding (idempotent)."""
    _bindings.pop(_key(session_id), None)


def clear() -> None:
    """Drop every binding (tests + a future admin/rotate hook)."""
    _bindings.clear()


def bindings() -> Dict[str, str]:
    """A COPY of the live binding table — for the /graph agent-lane render and
    for asserting isolation in tests. Never hand out the live dict."""
    return dict(_bindings)


def resolve(
    session_id: Optional[str],
    *,
    is_stdio: bool = False,
    header_selector: Optional[str] = None,
    default: Optional[str] = None,
) -> str:
    """Resolve the ACTING agent for one request. Never raises; never guesses.

    Precedence, and the isolation rule inherited verbatim from the source:

      1. ``is_stdio=True`` -> the single stdio bucket (``bound(None)``).
         Genuine one-process-one-session transport.
      2. HTTP -> the lookup is keyed STRICTLY on a NON-EMPTY ``session_id``.
         A missing/empty id consults NO bucket — and in particular NEVER the
         shared stdio bucket. That is the isolation hole the upstream review
         caught, and re-opening it would let two sessionless requests read each
         other's identity.
      3. An explicit per-request ``header_selector`` (the post-MCP-2.x path,
         and the ``x-palimpsest-agent`` an MCP host may forward).
      4. A surface-supplied ``default`` — a stable, configured author for a
         surface that has no session and no header (the MCP stdio mount), so a
         real write is never stamped :data:`UNBOUND_AGENT`. It is the LOWEST
         precedence on purpose: an explicit binding, a forwarded header, and
         any future verified-token selector all still outrank it, so this can
         never mask a real identity — it only replaces the honest-but-useless
         'unbound' fallback with an honest-and-useful configured name.
      5. :data:`UNBOUND_AGENT` — honest, not a fabricated identity.
    """
    if is_stdio:
        found = bound(None)
    elif isinstance(session_id, str) and session_id:
        found = bound(session_id)
    else:
        found = None
    if found:
        return found
    if header_selector and isinstance(header_selector, str):
        return header_selector
    if default and isinstance(default, str):
        return default
    return UNBOUND_AGENT


def selector_from_headers(headers: Optional[Mapping[str, str]]) -> tuple:
    """Extract ``(session_id, header_selector)`` from request headers,
    case-insensitively. Returns ``(None, None)`` when there are no headers
    (stdio). This is the ONE place header names are spelled."""
    if not headers:
        return (None, None)
    lowered = {str(k).lower(): v for k, v in headers.items()}
    return (lowered.get(SESSION_HEADER), lowered.get(AGENT_HEADER))


# ── author_agent stamping ───────────────────────────────────────────────────

def stamp(payload: dict, agent: str) -> dict:
    """Return a COPY of ``payload`` with ``author_agent`` stamped on it.

    Returns a copy on purpose: an in-place mutation of a caller's dict is how
    a retry ends up double-stamped or cross-stamped. The caller-supplied value
    NEVER wins — the acting agent is resolved server-side, exactly like the
    upstream ``findOwnedPersonaBySelector`` chokepoint refuses to let a request
    body name its own author.
    """
    stamped = dict(payload)
    stamped[AUTHOR_PROP] = agent
    return stamped


def stamp_params(params: dict, agent: str) -> dict:
    """Same as :func:`stamp`, for a Cypher ``$params`` map. Use it on EVERY
    write query so the MERGE can do ``SET n.author_agent = $author_agent``."""
    return stamp(params, agent)


def author_clause(alias: str = "n") -> str:
    """The Cypher fragment that stamps the acting agent. Kept here so the
    property name is spelled in exactly one place across the whole repo.

    >>> author_clause("e")
    'e.author_agent = $author_agent'
    """
    return f"{alias}.{AUTHOR_PROP} = ${AUTHOR_PROP}"


def contributors(rows: Iterable[Mapping]) -> Dict[str, int]:
    """Count writes per acting agent from a result set — feeds the per-agent
    node colouring in the projector UI (the graph payload already carries
    agent contribution stats upstream; this is the local equivalent)."""
    counts: Dict[str, int] = {}
    for row in rows:
        who = row.get(AUTHOR_PROP) or UNBOUND_AGENT
        counts[who] = counts.get(who, 0) + 1
    return counts


__all__ = [
    "STDIO_SESSION",
    "AUTHOR_PROP",
    "UNBOUND_AGENT",
    "AGENT_HEADER",
    "SESSION_HEADER",
    "bind",
    "bound",
    "unbind",
    "clear",
    "bindings",
    "resolve",
    "selector_from_headers",
    "stamp",
    "stamp_params",
    "author_clause",
    "contributors",
]
