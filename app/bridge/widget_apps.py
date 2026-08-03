"""SURFACE (b+): interactive MCP-Apps widgets — the `_meta.ui` declaration layer.

This is the ENTIRE widget delta and it is deliberately OFF the hot path: pure
data + a file read, no `mcp` import, no network, no FalkorDB. `server.py` imports
this module on a BARE interpreter (the guarded-import contract in
`test_mcp_module_imports_without_the_sdk` / the 3.9-importability contract), so
nothing here may reach for the SDK or a `X | Y` runtime annotation.

WHAT THIS IS (ground truth: plan/research/mcp-widgets-guide.md)
──────────────────────────────────────────────────────────────
MCP Apps (SEP-1865, Stable 2026-01-26) rides on top of the base protocol the
hosts already speak (2025-11-25). A tool opts into a widget by carrying
``_meta.ui.resourceUri`` pointing at a predeclared ``ui://`` resource whose
bundle is ``text/html;profile=mcp-app``. There is NO inline UI content type — the
HTML is a *resource*, the tool merely *references* it. Non-widget hosts ignore
``_meta`` entirely and read the tool's plain-text ``content`` block, so the
widget is ADDITIVE and never required. Claude Desktop / claude.ai RENDER these;
Claude Code does NOT (it speaks elicitation instead) — both paths coexist because
no host today declares both.

TIER-0 SCOPE (the achievable + recommended slice from the guide §4):
attach ``_meta.ui`` to the three highest-value verbs, ship three dependency-free
HTML bundles, and keep the text fallback byte-identical. The three widget→host
interactions all go THROUGH THE HOST (``tools/call`` against an existing read
verb, or ``ui/message``) — zero network permission, so the CSP stays locked down.

THE FIVE claude.ai GOTCHAS this module encodes (guide §2.3)
──────────────────────────────────────────────────────────
1. ``_meta.ui.domain`` is an undocumented render gate: it MUST equal
   ``sha256("<connector URL incl. /mcp, no trailing slash>")[:32] +
   ".claudemcpcontent.com"``. Deterministic and self-computable — not an
   Anthropic credential. Omit it and claude.ai never places the iframe.
2. ``ui/notifications/initialized`` is fired UNCONDITIONALLY by the bundle (see
   the HTML), plus a timeout fallback — the host keeps the iframe hidden until it
   arrives.
3. ``ui/notifications/size-changed`` carries REAL numbers (never null) — a null
   throws an uncaught host error that breaks the whole tool call.
4. The tool declares its resource URI TWICE: nested ``_meta.ui.resourceUri``
   (spec form) AND flat ``_meta["ui/resourceUri"]`` (what claude.ai reads today).
   :func:`tool_ui_meta` emits both.
5. ``mimeType`` is ``text/html;profile=mcp-app`` on BOTH resources/list and
   resources/read (Python 1.29 accepts the parameterized mime cleanly).

Also: version the ``ui://`` suffix (``-v1``) but keep old URIs servable — hosts
cache the bundle by URI and a vanished URI shows "Failed to fetch template."
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory import config

# ── the HTML bundles live next to this module (app/bridge/widgets/) ──────────
WIDGETS_DIR: Path = Path(__file__).resolve().parent / "widgets"

#: The ratified MCP-Apps bundle mime. The ``;profile=mcp-app`` parameter is what
#: distinguishes a ratified MCP-Apps resource from a legacy mcp-ui ``text/html``
#: one; a host that supports MCP Apps keys off exactly this.
MIME: str = "text/html;profile=mcp-app"


def _endpoint() -> str:
    """The connector URL the domain hash is computed from (gotcha #1).

    It MUST be the exact string the HOST connects to, including ``/mcp`` and with
    NO trailing slash. Locally that is ``http://127.0.0.1:8931/mcp`` (the bridge
    binds 6401/8931 by default). ``PALIMPSEST_MCP_ENDPOINT`` overrides it for a
    deploy whose public connector URL differs from the bind address (e.g. behind
    a Fly proxy) — because the widget sandbox domain is derived from the URL the
    client dials, not the port the process binds. Non-secret, env-overridable.
    """
    override = os.environ.get("PALIMPSEST_MCP_ENDPOINT")
    if override:
        return override.rstrip("/")
    return "http://{0}:{1}/mcp".format(config.BRIDGE_HOST, config.BRIDGE_PORT)


def widget_domain(endpoint: str) -> str:
    """The claude.ai widget-sandbox domain for one connector URL (gotcha #1).

    Deterministic: ``sha256(endpoint)[:32] + ".claudemcpcontent.com"``. Send the
    wrong one and claude.ai prints the expected value in the error; omit it and
    the resource fetches but the iframe is never placed.
    """
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:32] + ".claudemcpcontent.com"


ENDPOINT: str = _endpoint()
DOMAIN: str = widget_domain(ENDPOINT)


@dataclass(frozen=True)
class UiSpec:
    """The 5th, OPTIONAL field of a verb — the entire widget delta (guide §3.1).

    REST, OpenAPI and CLI never read ``.ui``; only the MCP emitter does. Adding a
    ``UiSpec`` to a verb therefore changes exactly one surface and cannot touch
    the pinned 13-verb table, the schemas, or the text fallback.
    """

    verb: str
    uri: str                       # ui://palimpsest/<name>-v1  (version the suffix)
    html: str                      # bundle filename inside WIDGETS_DIR
    name: str                      # resource name (resources/list)
    title: str                     # human title
    description: str
    #: ``connectDomains``/``resourceDomains``/``frameDomains`` for EXTERNAL
    #: resources. Omitted here for all three: every widget→server interaction
    #: goes through the host (``tools/call`` / ``ui/message``), so no origin needs
    #: to be granted and the sandbox stays maximally locked down (guide §2.5).
    csp: Optional[Dict[str, Any]] = None
    #: ``["model","app"]``: the model may call it AND the widget may call it
    #: (a widget polling its own read verb needs "app"). Hosts MUST NOT hide a
    #: model-visible tool; they MUST allow app-initiated calls to any listed tool.
    visibility: Tuple[str, ...] = ("model", "app")


#: The three Tier-0 widgets, keyed by the verb they decorate. ORDER = declaration
#: order in resources/list. Each maps to an EXISTING verb in `_VERB_DISPATCH`; no
#: new verb is introduced (the dispatch table is pinned at 13).
UI_SPECS: Dict[str, UiSpec] = {
    # LIVE graph view — polls the existing read verb `graph` every 1.5 s through
    # the host and paints an inline-SVG force layout. Fallback: node/edge counts.
    "graph": UiSpec(
        verb="graph",
        uri="ui://palimpsest/graph-v1",
        html="graph.html",
        name="palimpsest_graph_view",
        title="PALIMPSEST — live memory graph",
        description=(
            "Live nodes+edges view of the attributed memory graph. Polls the "
            "`graph` read verb through the host every 1.5 s and paints an inline "
            "SVG; no network permission required."
        ),
    ),
    # Approve / dismiss decision card with a SAFE-DEFAULT countdown. Buttons
    # report the human's tap to the model via `ui/message` (the host-native
    # decision channel) — never fabricating an approval, honoring `_h_ask`'s
    # "this verb never fabricates an approval" invariant.
    "ask": UiSpec(
        verb="ask",
        uri="ui://palimpsest/approval-v1",
        html="approval.html",
        name="palimpsest_approval_card",
        title="PALIMPSEST — approve this action?",
        description=(
            "Approve/dismiss decision card. Renders the `ask` card, counts down "
            "to the safe default, and reports the human's choice to the model via "
            "ui/message (never fabricates an approval)."
        ),
    ),
    # The COLD | WARM opposite-verdict beat rendered INSIDE the host: the same
    # new event into two differently-seeded graphs -> WARM escalates, COLD
    # dismisses, side by side. GOAL victory condition 1, the never-cut beat.
    "ablation": UiSpec(
        verb="ablation",
        uri="ui://palimpsest/ablation-v1",
        html="ablation.html",
        name="palimpsest_ablation_view",
        title="PALIMPSEST — cold vs warm (opposite verdict)",
        description=(
            "The ablation, rendered: identical event into a WARM (history) and a "
            "COLD (event-only) graph yields OPPOSITE verdicts — escalate vs "
            "dismiss — side by side. Refreshes via the `ablation` read verb."
        ),
    ),
}


def tool_ui_meta(spec: UiSpec) -> Dict[str, Any]:
    """The tool's ``_meta`` (goes on tools/list). Emits the resource URI in BOTH
    the nested spec form and the flat form claude.ai reads today (gotcha #4)."""
    return {
        "ui": {
            "resourceUri": spec.uri,
            "visibility": list(spec.visibility),
        },
        # gotcha #4 — claude.ai's current implementation reads the FLAT key.
        "ui/resourceUri": spec.uri,
    }


def resource_ui_meta(spec: UiSpec) -> Dict[str, Any]:
    """The resource's ``_meta.ui`` (goes on resources/list AND resources/read).

    Carries the render-gate domain (gotcha #1), the border hint, and — only if
    the spec declares one — the CSP. Attached to BOTH list and read so an
    ``--app-info`` probe reads the same declaration wherever it looks.
    """
    ui: Dict[str, Any] = {"domain": DOMAIN, "prefersBorder": True}
    if spec.csp:
        ui["csp"] = spec.csp
    return {"ui": ui}


def spec_for_uri(uri: str) -> Optional[UiSpec]:
    for spec in UI_SPECS.values():
        if spec.uri == uri:
            return spec
    return None


def read_widget_html(spec: UiSpec) -> str:
    """The bundle text for one widget. Read fresh (no caching) — the bundles are
    tiny and reading per request keeps an edited bundle live without a restart."""
    return (WIDGETS_DIR / spec.html).read_text(encoding="utf-8")


def resource_rows() -> List[Tuple[UiSpec, Dict[str, Any]]]:
    """``(spec, resource_meta)`` for every widget, for the MCP list_resources
    handler. The handler (which has the SDK) turns each into a ``Resource``."""
    return [(spec, resource_ui_meta(spec)) for spec in UI_SPECS.values()]


def app_info() -> List[Dict[str, Any]]:
    """A self-contained mirror of what the inspector's ``--app-info`` reports, so
    the compliance receipt is reproducible without a network probe. hasApp is
    True for exactly the decorated verbs."""
    rows: List[Dict[str, Any]] = []
    for verb, spec in UI_SPECS.items():
        row: Dict[str, Any] = {
            "hasApp": True,
            "toolName": verb,
            "resourceUri": spec.uri,
            "domain": DOMAIN,
            "prefersBorder": True,
            "resourceMimeType": MIME,
            "visibility": list(spec.visibility),
        }
        if spec.csp:
            row["csp"] = spec.csp
        rows.append(row)
    return rows
