#!/usr/bin/env bash
# PALIMPSEST single-container boot: FalkorDB, then the bridge.
#
#   1. start redis-server + the FalkorDB graph module on 127.0.0.1:$FALKORDB_PORT
#      (loopback only — nothing outside the container should reach the database),
#   2. wait until it answers PING,
#   3. seed a real co-edit ring so /graph is non-empty for the Pages UI,
#   4. start the bridge on $BRIDGE_BIND_HOST:$BRIDGE_PORT and forward SIGTERM.
#
# SIGTERM handling: Fly sends SIGTERM on stop/suspend. We run the bridge in the
# background and `wait` on it so a trap can forward the signal to BOTH the bridge
# (graceful uvicorn shutdown) and FalkorDB — `exec`-ing the bridge would strand
# the backgrounded database. The graph is ephemeral (no volume) and re-seeded on
# every boot, which is exactly what a stateless demo backend wants.
set -euo pipefail

FALKOR_PORT="${FALKORDB_PORT:-6401}"
BRIDGE_PORT="${BRIDGE_PORT:-8931}"
BIND_HOST="${BRIDGE_BIND_HOST:-0.0.0.0}"
DATA_DIR="${FALKORDB_DATA_DIR:-/data}"
MODULE="/var/lib/falkordb/bin/falkordb.so"
PY="${VENV:-/opt/venv}/bin/python"

mkdir -p "$DATA_DIR"

REDIS_PID=""
BRIDGE_PID=""

shutdown() {
  echo "[entrypoint] SIGTERM received — shutting down"
  [ -n "$BRIDGE_PID" ] && kill -TERM "$BRIDGE_PID" 2>/dev/null || true
  [ -n "$BRIDGE_PID" ] && wait "$BRIDGE_PID" 2>/dev/null || true
  [ -n "$REDIS_PID" ] && kill -TERM "$REDIS_PID" 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

# ── 1. FalkorDB ──────────────────────────────────────────────────────────────
echo "[entrypoint] starting FalkorDB on 127.0.0.1:${FALKOR_PORT}"
redis-server \
  --bind 127.0.0.1 \
  --port "$FALKOR_PORT" \
  --dir "$DATA_DIR" \
  --save "" \
  --appendonly no \
  --loadmodule "$MODULE" &
REDIS_PID=$!

# ── 2. wait for PING ─────────────────────────────────────────────────────────
ready=""
for _ in $(seq 1 60); do
  if redis-cli -p "$FALKOR_PORT" ping 2>/dev/null | grep -q PONG; then
    ready=1
    break
  fi
  # If redis died, stop waiting for a corpse.
  if ! kill -0 "$REDIS_PID" 2>/dev/null; then
    echo "[entrypoint] FalkorDB process exited before answering PING" >&2
    exit 1
  fi
  sleep 0.5
done
if [ -z "$ready" ]; then
  echo "[entrypoint] FalkorDB did not answer PING in time" >&2
  exit 1
fi
echo "[entrypoint] FalkorDB is up"

# ── 3. seed the warm graph (best-effort; a seed failure must not down the box)
echo "[entrypoint] seeding demo ring into the warm graph"
if "$PY" -m app.bridge.seed_demo; then
  echo "[entrypoint] seed complete"
else
  echo "[entrypoint] seed_demo failed — bridge will still serve (empty warm graph)" >&2
fi

# ── 4. the bridge (backgrounded so the SIGTERM trap can reach it) ────────────
echo "[entrypoint] starting bridge on ${BIND_HOST}:${BRIDGE_PORT}"
BRIDGE_BIND_HOST="$BIND_HOST" BRIDGE_PORT="$BRIDGE_PORT" "$PY" -m app.bridge.rest &
BRIDGE_PID=$!

wait "$BRIDGE_PID"
