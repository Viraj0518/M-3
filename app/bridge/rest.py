"""SURFACE (a): REST — the projector UI's whole world, generated from THE TABLE.

Nothing here re-implements a verb. Every route is a thin adapter that turns an
HTTP request into ``server.dispatch(verb, args)`` and the returned envelope into
JSON. If a verb is added to ``_VERB_DISPATCH`` it gets a route for free; if a
route needed hand-written logic that would be the bug.

════════════════════════════════════════════════════════════════════════════
THE /v1 RECONCILIATION  (read this before "fixing" the duplicate routes)
════════════════════════════════════════════════════════════════════════════
The dispatch table's paths are ``/v1/graph``, ``/v1/ring``,
``/v1/stream/{topic}/tail``. The projector UI (``app/web/index.html``, line 940)
polls::

    fetchJson("/graph")            fetchJson("/ring")     fetchJson("/stream_tail?limit=80")

— NO ``/v1`` prefix, ``/ring`` as a GET (the table says POST), and a
``stream_tail`` with NO topic segment at all. The UI was built first and is
read-only for this lane, so THE UI IS CANONICAL. Both spellings are served:

  * ``/v1/<table path>`` at the table's own METHOD — the truth the OpenAPI
    spec, Guild Integration and every non-UI client speak. Generated in a loop
    straight off the table, so it cannot drift.
  * bare ``/graph``, ``/ring``, ``/stream_tail`` — aliases for EXACTLY the three
    verbs the UI polls, no more. ``/ring`` accepts GET *and* POST (same handler,
    same verb); ``/stream_tail`` defaults ``topic`` to ``signal.raw`` because
    the UI cannot supply the path segment the table wants.

Adding a fourth alias without a UI call site is how an undocumented second API
is born. The alias set is a closed list (``_UI_ALIASES``) and a test asserts it.

════════════════════════════════════════════════════════════════════════════
WHY EVERY VERB RESPONSE IS HTTP 200 UNLESS IT CARRIES A `code`
════════════════════════════════════════════════════════════════════════════
``fetchJson`` throws on ``!response.ok`` and ``pollBridge`` treats a throw on
/graph as BRIDGE OFFLINE -> the mock graph. A not-yet-implemented verb
(``stream_tail``) is NOT an offline bridge, so an honest stub envelope must ride
on a 200 or the projector goes MOCK on stage while the graph is live. Status is
therefore derived ONLY from ``err()``'s ``code`` field: no code, no error status.

The single exception is ``GET /health``, which is the Windows compose
healthcheck contract and returns 503 + ``FALKORDB_UNAVAILABLE`` when the memory
plane is down — a container orchestrator needs the failure in the STATUS LINE,
not in the body.

CORS is wide open (``allow_origins=["*"]``) on purpose: the projector is
regularly opened as ``file://.../app/web/index.html``, whose Origin is the
string ``null``. This process binds 127.0.0.1 only and holds no credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import subprocess
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from memory import config

from . import graphstore
from . import server as bridge

# ─── MCP streamable-http, guarded exactly like the SDK import in server.py ───
try:  # pragma: no cover - import-environment dependent
    from mcp.server.streamable_http_manager import (  # type: ignore[import-not-found]
        StreamableHTTPSessionManager,
    )

    _MCP_HTTP_IMPORTABLE = True
except Exception:  # noqa: BLE001 - any import failure is the same outcome
    StreamableHTTPSessionManager = None  # type: ignore[assignment,misc]
    _MCP_HTTP_IMPORTABLE = False


#: The bare paths the projector polls. verb -> (alias path, methods, defaults).
#: A CLOSED LIST. Every entry must correspond to a real fetch in
#: app/web/index.html; there is a test that pins this set.
_UI_ALIASES: Dict[str, Tuple[str, Tuple[str, ...], Dict[str, Any]]] = {
    "graph": ("/graph", ("GET",), {}),
    # The table says POST /v1/ring; the UI GETs /ring. Both, one handler.
    "ring": ("/ring", ("GET", "POST"), {}),
    # The UI cannot supply the {topic} path segment, so the alias defaults it.
    "stream_tail": ("/stream_tail", ("GET",), {"topic": config.TOPIC_SIGNAL_RAW}),
}

#: err() codes -> HTTP status. Anything not listed (including an honest
#: not-implemented stub, which carries NO code) rides on a 200.
_STATUS_BY_CODE: Dict[str, int] = {
    "UNKNOWN_TOOL": 404,
    "MISSING_PATH_PARAM": 400,
    "MISSING_CONTENT": 400,
    "MISSING_QUERY": 400,
    "MISSING_ENDPOINT": 400,
    "MISSING_AGENT_ID": 400,
    "BAD_RELATION": 400,
    "BAD_REQUEST": 400,
    "NODE_NOT_FOUND": 404,
    "HANDOVER_WRITE_FAILED": 500,
    "HANDLER_ERROR": 500,
}

#: LaserData's HTTP port on the local stack. A TCP connect is the whole probe —
#: this process must never take a runtime dependency on the laser SDK just to
#: answer a healthcheck.
LASER_PROBE_HOST = "127.0.0.1"
LASER_PROBE_PORT = 8090

#: How long a laser probe result is reused. The healthcheck is polled far more
#: often than the stack changes state, and a TCP connect per poll on a dead port
#: is the classic way a health endpoint becomes the slowest route in the app.
LASER_PROBE_TTL_S = 5.0
LASER_PROBE_TIMEOUT_S = 0.25

_laser_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_git_sha_cache: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# startup facts
# ═══════════════════════════════════════════════════════════════════════════

def git_sha(*, refresh: bool = False) -> str:
    """The commit this process is serving, resolved ONCE at startup.

    Resolved from ``git rev-parse HEAD`` and cached, because a health endpoint
    that shells out on every poll is a health endpoint that fails under load.
    Returns ``"unknown"`` rather than raising when the repo is not a checkout
    (a container built from a tarball) — a missing sha is a reporting gap, not
    a reason to fail the healthcheck.
    """
    global _git_sha_cache
    if _git_sha_cache is not None and not refresh:
        return _git_sha_cache
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(config.REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        _git_sha_cache = out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        _git_sha_cache = "unknown"
    return _git_sha_cache or "unknown"


def _probe_falkordb() -> Dict[str, Any]:
    """``RETURN 1`` against the warm graph. Synchronous — the caller pushes it
    onto a worker thread, exactly like every other FalkorDB call in the bridge.

    ``latency_ms`` is measured round-trip HERE (unlike ``run_time_ms``, which is
    FalkorDB's own execution time): the healthcheck's question is "can this
    process reach the database", so the wire time is the point.
    """
    started = time.perf_counter()
    try:
        graphstore.query("RETURN 1", graph_key=config.GRAPH_WARM, read_only=True)
    except Exception as exc:  # noqa: BLE001
        return {
            "reachable": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "endpoint": "{0}:{1}".format(config.FALKORDB_HOST, config.FALKORDB_PORT),
            "error": "{0}: {1}".format(type(exc).__name__, exc),
        }
    return {
        "reachable": True,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "endpoint": "{0}:{1}".format(config.FALKORDB_HOST, config.FALKORDB_PORT),
    }


def _probe_laser_blocking() -> Dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(LASER_PROBE_TIMEOUT_S)
    started = time.perf_counter()
    try:
        sock.connect((LASER_PROBE_HOST, LASER_PROBE_PORT))
        reachable = True
    except OSError:
        reachable = False
    finally:
        with contextlib.suppress(OSError):
            sock.close()
    return {
        "reachable": reachable,
        "endpoint": "{0}:{1}".format(LASER_PROBE_HOST, LASER_PROBE_PORT),
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "probe": "tcp",
    }


async def probe_laser(*, ttl_s: float = LASER_PROBE_TTL_S) -> Dict[str, Any]:
    """TCP reachability for the log spine, cached for ``ttl_s`` seconds.

    NON-BLOCKING for the event loop in both senses: the connect itself has a
    250 ms socket timeout AND it runs on a worker thread, so a black-holed port
    (the failure mode that makes a TCP connect hang rather than refuse) cannot
    stall the /graph poll the projector is making at the same time.
    """
    now = time.monotonic()
    cached = _laser_cache.get("value")
    if cached is not None and (now - float(_laser_cache.get("at") or 0.0)) < ttl_s:
        return dict(cached, cached=True)
    value = await asyncio.to_thread(_probe_laser_blocking)
    _laser_cache["value"] = value
    _laser_cache["at"] = now
    return dict(value, cached=False)


# ═══════════════════════════════════════════════════════════════════════════
# query-string -> verb args
# ═══════════════════════════════════════════════════════════════════════════
# A query string is all strings. `?gate=false` is the trap: a bare str is
# TRUTHY in Python, so an un-coerced "false" turns the sensing gate ON. The tool
# schema already declares every parameter's JSON type, so the coercion is driven
# off the SAME schema the MCP surface publishes rather than a second table.

_FALSEY = {"0", "false", "no", "off", "null", "none", ""}
_TRUTHY = {"1", "true", "yes", "on"}


def _schema_props(verb: str) -> Dict[str, dict]:
    for tool in bridge.all_tools():
        if tool.name == bridge.tool_name(verb):
            return dict(tool.inputSchema.get("properties") or {})
    return {}


def coerce(value: str, schema: Optional[dict]) -> Any:
    """One query-string value -> the JSON type its schema declares.

    Unknown/absent schema means "leave it a string": the handlers validate their
    own inputs (``_int`` clamps, ``check_graph_key`` allowlists), so a wrong
    guess here would be a SECOND validator to keep in sync.
    """
    kind = (schema or {}).get("type")
    try:
        if kind == "integer":
            return int(value)
        if kind == "number":
            return float(value)
        if kind == "boolean":
            low = value.strip().lower()
            if low in _TRUTHY:
                return True
            if low in _FALSEY:
                return False
            return bool(value)
        if kind in ("array", "object"):
            text = value.strip()
            if text.startswith(("[", "{")):
                return json.loads(text)
            return [part for part in value.split(",") if part] if kind == "array" else {}
    except (TypeError, ValueError):
        return value
    return value


def args_from_query(verb: str, pairs: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    props = _schema_props(verb)
    out: Dict[str, Any] = {}
    for key, raw in pairs:
        out[key] = coerce(raw, props.get(key))
    return out


def status_for(envelope: dict) -> int:
    """HTTP status for one dispatch envelope. See the module header: derived
    ONLY from an err() `code`, never from `ok` — an honest stub is a 200."""
    if envelope.get("ok") is True:
        return 200
    code = envelope.get("code")
    if not code:
        return 200
    return _STATUS_BY_CODE.get(str(code), 400)


# ═══════════════════════════════════════════════════════════════════════════
# the app
# ═══════════════════════════════════════════════════════════════════════════

def _endpoint(verb: str, method: str, defaults: Optional[Dict[str, Any]] = None):
    """ONE adapter, closed over a verb. Every route in this app is this."""
    defaults = dict(defaults or {})

    async def endpoint(request: Request) -> JSONResponse:
        args: Dict[str, Any] = dict(defaults)
        args.update(request.path_params)
        args.update(args_from_query(verb, request.query_params.multi_items()))
        if method not in ("GET", "DELETE", "HEAD"):
            body = await _json_body(request)
            if body is None:
                return JSONResponse(
                    bridge.err("BAD_REQUEST", "request body must be a JSON object"),
                    status_code=400,
                )
            args.update(body)
        envelope = await bridge.dispatch(
            verb,
            args,
            headers=dict(request.headers),
            surface="rest",
        )
        return JSONResponse(
            json.loads(json.dumps(envelope, default=str)),
            status_code=status_for(envelope),
        )

    endpoint.__name__ = "rest_{0}_{1}".format(verb, method.lower())
    return endpoint


async def _json_body(request: Request) -> Optional[dict]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _AsgiProxy:
    """A bound method wrapped so Starlette treats it as an ASGI APP.

    ``Route(path, endpoint)`` inspects the endpoint: a function or a BOUND
    METHOD is wrapped in ``request_response`` (which expects a ``Response``
    return value and would swallow the streaming send), while any other
    callable is used as a raw ASGI app. ``manager.handle_request`` is a bound
    method, so it needs this one-line instance to reach the ASGI branch.
    """

    __slots__ = ("_handler",)

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._handler(scope, receive, send)


def _mcp_manager() -> Tuple[Optional[Any], Dict[str, Any]]:
    """The MCP streamable-http surface, or an honest reason it is absent.

    The SDK's mounting model does NOT fight FastAPI: ``StreamableHTTPSessionManager``
    is a plain ASGI callable, so it mounts as a sub-app at ``/mcp`` on this same
    port. Its ONE requirement is that ``manager.run()`` is held open for the
    lifetime of the process — that is what the lifespan below does, and skipping
    it produces "Task group is not initialized" on the first request rather than
    at boot.
    """
    if not _MCP_HTTP_IMPORTABLE or not bridge.MCP_AVAILABLE or bridge.server is None:
        return None, {
            "mounted": False,
            "reason": (
                "mcp SDK not importable in this interpreter — install it PINNED "
                "(`mcp>=1.28,<2`); the upper bound is load-bearing."
            ),
        }
    manager = StreamableHTTPSessionManager(app=bridge.server, json_response=False)
    return manager, {"mounted": True, "path": "/mcp", "transport": "streamable-http"}


def create_app() -> FastAPI:
    manager, mcp_status = _mcp_manager()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        git_sha()  # resolve once, at startup, per the health contract
        if manager is None:
            yield
            return
        async with manager.run():
            yield

    app = FastAPI(
        title="PALIMPSEST bridge",
        version=bridge.SERVER_VERSION,
        description=bridge.INSTRUCTIONS,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "mcp-session-id"],
    )
    app.state.mcp = mcp_status

    # ── health (the Windows compose healthcheck contract) ───────────────────
    @app.get("/health")
    async def health() -> JSONResponse:
        falkordb = await asyncio.to_thread(_probe_falkordb)
        laser = await probe_laser()
        body: Dict[str, Any] = {
            "ok": bool(falkordb["reachable"]),
            "service": bridge.SERVER_NAME,
            "version": bridge.SERVER_VERSION,
            "git_sha": git_sha(),
            "bridge": "{0}:{1}".format(config.BRIDGE_HOST, config.BRIDGE_PORT),
            "falkordb": falkordb,
            "laser": laser,
            "mcp": app.state.mcp,
            "graphs": {"warm": config.GRAPH_WARM, "cold": config.GRAPH_COLD},
        }
        if falkordb["reachable"]:
            return JSONResponse(body, status_code=200)
        body["code"] = "FALKORDB_UNAVAILABLE"
        body["error"] = (
            "the memory plane at {0} is unreachable — the bridge is up but has "
            "nothing to remember with.".format(falkordb["endpoint"])
        )
        return JSONResponse(body, status_code=503)

    # ── the OpenAPI 3.0 spec generated from THE TABLE (surface c) ───────────
    # NOT FastAPI's own /openapi.json, which describes these adapter routes.
    # This one is the Guild Integration document.
    @app.get("/v1/openapi.json")
    async def table_openapi() -> JSONResponse:
        return JSONResponse(
            bridge.openapi_spec(
                "http://{0}:{1}".format(config.BRIDGE_HOST, config.BRIDGE_PORT)
            )
        )

    # ── (1) the table's own paths, verbatim ─────────────────────────────────
    for verb, (method, path, _builder, _handler) in bridge._VERB_DISPATCH.items():
        app.add_api_route(
            path,
            _endpoint(verb, method),
            methods=[method],
            name="verb_{0}".format(verb),
            summary="{0} ({1} {2})".format(verb, method, path),
        )

    # ── (2) the UI's bare aliases, for exactly three verbs ──────────────────
    for verb, (alias, methods, defaults) in _UI_ALIASES.items():
        for alias_method in methods:
            app.add_api_route(
                alias,
                _endpoint(verb, alias_method, defaults),
                methods=[alias_method],
                name="alias_{0}_{1}".format(verb, alias_method.lower()),
                summary="UI alias for {0} (canonical: {1})".format(
                    verb, bridge._VERB_DISPATCH[verb][1]
                ),
            )

    # ── (3) MCP streamable-http on the SAME port ────────────────────────────
    # BARE `/mcp` MUST NOT REDIRECT. A Starlette Mount only matches `/mcp/...`
    # and 307s the bare path to `/mcp/` — and the MCP Apps contract derives the
    # widget sandbox domain from a sha256 OF THE ENDPOINT STRING
    # (plan/research/mcp-widgets-guide.md §2.3: "must match the connector URL
    # exactly"), so a client that silently follows the redirect ends up on a
    # different endpoint string than the one the domain was computed from.
    # An exact Route first, the Mount second for clients that add the slash.
    if manager is not None:
        asgi = _AsgiProxy(manager.handle_request)
        app.router.routes.insert(
            0, Route("/mcp", asgi, methods=["GET", "POST", "DELETE", "OPTIONS"])
        )
        app.router.routes.append(Mount("/mcp", app=asgi))

    # ── (4) the projector itself, so it is same-origin with the bridge ──────
    web_dir = config.REPO_ROOT / "app" / "web"
    if web_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(web_dir), html=True), name="ui")

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": bridge.SERVER_NAME,
                "version": bridge.SERVER_VERSION,
                "git_sha": git_sha(),
                "ui": "/ui/",
                "health": "/health",
                "openapi": "/v1/openapi.json",
                "mcp": app.state.mcp,
                "verbs": {
                    verb: {"method": m, "path": p}
                    for verb, (m, p, _b, _h) in bridge._VERB_DISPATCH.items()
                },
                "ui_aliases": {v: a for v, (a, _m, _d) in _UI_ALIASES.items()},
            }
        )

    return app


app = create_app()


def serve() -> None:  # pragma: no cover - process entrypoint
    import uvicorn

    uvicorn.run(
        app,
        host=config.BRIDGE_HOST,
        port=config.BRIDGE_PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":  # pragma: no cover
    serve()
