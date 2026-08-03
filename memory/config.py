"""PALIMPSEST — THE ONE shared constant module.

Every process (bridge, graph_writer, gate, consumer, producer, seed scripts,
the CLI, the eval harness) imports host/port/graph-name/db-path/embed-dim FROM
HERE. Nobody hardcodes them anywhere else.

WHY THIS FILE EXISTS AT ALL (pre-event checklist, item 6):
  falkordblite persistence is PER-FILE-PATH. Two teammates pointing at two
  different db paths get silently different graphs with NO error and NO warning
  — the queries just come back empty and everyone blames the Cypher. One
  constant, committed, ends that class of bug before it starts.

HYGIENE: this module holds NO secret values. Key-shaped settings
(ANTHROPIC_API_KEY / OPENAI_API_KEY / GITHUB_TOKEN / DISCORD_WEBHOOK_URL) are
read from the process environment on demand and are NEVER given a literal
default here, never logged, and never echoed into an error message. See
`.env.example` for the bring-your-own-key contract.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── repo anchor ─────────────────────────────────────────────────────────────
# memory/config.py -> repo root.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    """Env override with a non-secret default. Empty string == unset."""
    val = os.environ.get(name)
    return val if val else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"{name}={raw!r} is not an integer — refusing to boot with an "
            f"ambiguous {name} (crash early, do not silently fall back)."
        ) from None


# ── FALKORDB (the memory of record) ─────────────────────────────────────────
FALKORDB_HOST: str = _env("FALKORDB_HOST", "127.0.0.1")

# 6401 — NOT 6379, NOT 6399.
#   * 6379 is the Redis default. FalkorDB speaks the Redis wire protocol, so a
#     stray `redis-server` on 6379 lets the client CONNECT FINE and then fail
#     with "unknown command GRAPH.QUERY" — a false-green that eats 45 minutes.
#   * 6399 is what the falkordblite recon examples used, and a stray
#     redis-server was VERIFIED holding 127.0.0.1:6399 on the build Mac.
# PALIMPSEST binds 6401 either way. Check with:  lsof -i :6379 -i :6399 -i :6401
FALKORDB_PORT: int = _env_int("FALKORDB_PORT", 6401)

# falkordblite defaults to a UNIX SOCKET in a temp dir, so a Node/TS process or
# the FalkorDB Browser UI cannot see it. Force TCP with this exact dict. NOTE
# the port value is a STRING here — that is the API, not a typo.
FALKORDB_SERVERCONFIG: dict = {
    "port": str(FALKORDB_PORT),
    "bind": FALKORDB_HOST,
}

# Two graph keys on ONE server. `palimpsest` is the warm graph (the real
# memory); `palimpsest_cold` is deliberately EMPTY and is the A/B target for
# the ablation beat — identical event, cold vs warm, opposite verdicts.
GRAPH_WARM: str = _env("PALIMPSEST_GRAPH_WARM", "palimpsest")
GRAPH_COLD: str = _env("PALIMPSEST_GRAPH_COLD", "palimpsest_cold")

# THE shared persistence path. Per-file-path persistence means this constant is
# load-bearing: change it on one box and that box silently forks the graph.
DB_PATH: Path = Path(_env("PALIMPSEST_DB_PATH", str(REPO_ROOT / "demo" / "palimpsest.db")))

# Embedding width. MUST match the embedder's `dimensions=` argument AND the
# HNSW index's `dim:` — a mismatch corrupts the vector index SILENTLY (no
# error, just wrong neighbours). 256 is the pinned value across the stack.
EMBED_DIM: int = _env_int("PALIMPSEST_EMBED_DIM", 256)

# Vector index similarity function. FalkorDB scores are DISTANCES: 0.0 ==
# identical, larger == further. Sort ASC. See memory/SCHEMA.md trap #1.
VECTOR_SIMILARITY: str = "cosine"

# Sensing-gate threshold, ported from unblock_substrate/src/brain/sensing-gate.ts
# (DEFAULT_SALIENCE_THRESHOLD = 0.15, DEFAULT_NEIGHBOR_FANOUT = 5). Applied to
# the top-1 DISTANCE: below the threshold means "too close to something we
# already know" => skip the write.
SALIENCE_THRESHOLD: float = float(_env("PALIMPSEST_SALIENCE_THRESHOLD", "0.15"))
NEIGHBOR_FANOUT: int = _env_int("PALIMPSEST_NEIGHBOR_FANOUT", 5)


# ── LASERDATA (the log spine) ───────────────────────────────────────────────
# TWO NAMES FOR ONE THING — a real trap. `.env.example` documents
# LASER_CONNECTION; laser-stack's ./scripts/up prints an export line for
# LASER_CONNECTION_STRING, and the Python SDK has no connect_env() so YOU read
# the variable yourself. We honor both, SDK name first, and never invent a
# default: the value must be the exact string ./scripts/up printed
# (`iggy:laser@...`, NOT the SDK examples' `iggy:iggy@...`).
def laser_connection() -> str:
    """The laser-stack connection string. Raises if unset — a wrong/absent
    connection string fails downstream as what LOOKS like a network error."""
    val = os.environ.get("LASER_CONNECTION_STRING") or os.environ.get("LASER_CONNECTION")
    if not val:
        raise RuntimeError(
            "LASER_CONNECTION_STRING (or LASER_CONNECTION) is unset. Copy the "
            "EXACT export line that laser-stack's ./scripts/up printed — it is "
            "iggy:laser@..., while the SDK examples hardcode iggy:iggy@... and "
            "the mismatch fails as what looks like a network error."
        )
    return val


LASER_STREAM: str = _env("LASER_STREAM", "live")

# The six topics. LaserData is the ONLY interface between services.
TOPIC_SIGNAL_RAW = "signal.raw"
TOPIC_SIGNAL_SALIENT = "signal.salient"
TOPIC_CASE_OPENED = "case.opened"
TOPIC_CASE_DECISION = "case.decision"
TOPIC_ACTION_EXECUTED = "action.executed"
TOPIC_AGENT_HANDOFF = "agent.handoff"

TOPICS: tuple = (
    TOPIC_SIGNAL_RAW,
    TOPIC_SIGNAL_SALIENT,
    TOPIC_CASE_OPENED,
    TOPIC_CASE_DECISION,
    TOPIC_ACTION_EXECUTED,
    TOPIC_AGENT_HANDOFF,
)

TOPIC_PARTITIONS: int = 4
PRODUCER_BATCH_LENGTH: int = 200
PRODUCER_LINGER_MS: int = 50
CONSUMER_GROUP_GRAPH_WRITERS: str = "graph-writers"


# ── ROCKETRIDE (motion) ─────────────────────────────────────────────────────
ROCKETRIDE_URL: str = _env("ROCKETRIDE_URL", "http://127.0.0.1:5565")

# The self-hosted engine's BUILT-IN dev key, published in the upstream repo's
# .env.template. It is not a credential — the engine is MIT, local, and has no
# signup. Overridable, but this default is intentional and safe to commit.
ROCKETRIDE_API_KEY: str = _env("ROCKETRIDE_API_KEY", "MYAPIKEY")


# ── BRIDGE ──────────────────────────────────────────────────────────────────
BRIDGE_HOST: str = _env("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT: int = _env_int("BRIDGE_PORT", 8787)


# ── EDGE TAP ────────────────────────────────────────────────────────────────
WIKIMEDIA_SSE_URL: str = "https://stream.wikimedia.org/v2/stream/recentchange"
SEED_REPLAY_PATH: Path = REPO_ROOT / "demo" / "seed_replay.ndjson"


# ── SECRETS: read on demand, never defaulted, never logged ──────────────────
def secret(name: str, *, required: bool = False) -> str | None:
    """Fetch a key-shaped setting from the environment.

    NEVER returns a literal default and NEVER includes the value in an error
    message. Callers must not log the result. See `.env.example`.
    """
    val = os.environ.get(name)
    if not val and required:
        raise RuntimeError(
            f"{name} is not set. PALIMPSEST is bring-your-own-key: copy "
            f".env.example to .env and fill it in. Nothing key-shaped is ever "
            f"committed to this repo."
        )
    return val or None


def describe() -> dict:
    """A SAFE, loggable snapshot of the effective config. Contains no secrets —
    key-shaped settings are reported as a bool 'is it set', never a value."""
    return {
        "falkordb": f"{FALKORDB_HOST}:{FALKORDB_PORT}",
        "graph_warm": GRAPH_WARM,
        "graph_cold": GRAPH_COLD,
        "db_path": str(DB_PATH),
        "embed_dim": EMBED_DIM,
        "vector_similarity": VECTOR_SIMILARITY,
        "salience_threshold": SALIENCE_THRESHOLD,
        "laser_stream": LASER_STREAM,
        "laser_connection_set": bool(
            os.environ.get("LASER_CONNECTION_STRING") or os.environ.get("LASER_CONNECTION")
        ),
        "rocketride_url": ROCKETRIDE_URL,
        "bridge": f"{BRIDGE_HOST}:{BRIDGE_PORT}",
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "github_token_set": bool(os.environ.get("GITHUB_TOKEN")),
        "discord_webhook_set": bool(os.environ.get("DISCORD_WEBHOOK_URL")),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(describe(), indent=2))
