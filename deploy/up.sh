#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# PALIMPSEST — directive-7 cold start.
#
#   remove old containers AND volumes -> build -> start ->
#   BLOCK until every service reports healthy -> print a readiness summary.
#
# There are no sleeps in here. Readiness comes from container healthchecks.
#
# Usage:
#   ./up.sh                      core stack only
#   ./up.sh --profile logspine   core stack + LaserData log spine
#   ./up.sh --logspine           shorthand for the above
#   ./up.sh --keep-volumes       skip the volume wipe (NOT a directive-7 run)
#
set -euo pipefail

# --- Windows / git-bash path handling ---------------------------------------
# MSYS rewrites any argument that looks like a unix path before handing it to a
# native .exe, which mangles IN-CONTAINER paths (e.g. `docker exec c ls /var/lib`
# becomes `C:/Program Files/Git/var/lib`). Disable that globally, and never pass
# a host path to docker from this script — we `cd` and use a RELATIVE -f instead,
# which needs no conversion either way.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

COMPOSE_FILE="docker-compose.yml"
PROJECT_NAME="palimpsest"
PROFILE_ARGS=()
WIPE_VOLUMES=1
LOGSPINE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)
      [ $# -ge 2 ] || { echo "up.sh: --profile needs a value" >&2; exit 2; }
      PROFILE_ARGS+=("--profile" "$2")
      [ "$2" = "logspine" ] && LOGSPINE=1
      shift 2
      ;;
    --profile=*)
      PROFILE_ARGS+=("--profile" "${1#*=}")
      [ "${1#*=}" = "logspine" ] && LOGSPINE=1
      shift
      ;;
    --logspine)
      PROFILE_ARGS+=("--profile" "logspine"); LOGSPINE=1; shift ;;
    --keep-volumes)
      WIPE_VOLUMES=0; shift ;;
    -h|--help)
      sed -n '3,16p' "$0"; exit 0 ;;
    *)
      echo "up.sh: unknown argument '$1' (see --help)" >&2; exit 2 ;;
  esac
done

DC=(docker compose -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"}")

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
step()  { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn()  { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# --- 0. preflight -----------------------------------------------------------
step "Preflight"
command -v docker >/dev/null 2>&1 || die "docker not on PATH"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not available"
ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?') / compose $(docker compose version --short 2>/dev/null || echo '?')"

# Directive 3: .env is gitignored and holds only local values. Seed it from the
# committed example if the operator has not made one.
if [ ! -f .env ]; then
  cp .env.example .env
  ok ".env created from .env.example (gitignored; edit it, never commit it)"
else
  ok ".env present"
fi

# Load only the couple of values this script itself needs. `set -a` here would
# leak every var into the docker CLI environment, so read them explicitly.
get_env() { sed -n "s/^$1=//p" .env | tail -n1; }
BIND_ADDR="$(get_env PALIMPSEST_BIND_ADDRESS)"; BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
FALKOR_PORT="$(get_env PALIMPSEST_FALKORDB_PORT)"; FALKOR_PORT="${FALKOR_PORT:-6401}"
BROWSER_PORT="$(get_env PALIMPSEST_BROWSER_PORT)"; BROWSER_PORT="${BROWSER_PORT:-3100}"
BRIDGE_PORT="$(get_env PALIMPSEST_BRIDGE_PORT)"; BRIDGE_PORT="${BRIDGE_PORT:-8931}"
UI_PORT="$(get_env PALIMPSEST_UI_PORT)"; UI_PORT="${UI_PORT:-5173}"
TIMEOUT="$(get_env PALIMPSEST_START_TIMEOUT)"; TIMEOUT="${TIMEOUT:-300}"
GRAPH_NAME="$(get_env PALIMPSEST_GRAPH_WARM)"; GRAPH_NAME="${GRAPH_NAME:-palimpsest}"

"${DC[@]}" config -q || die "docker-compose.yml does not parse"
ok "compose file parses"

# Foreign containers squatting on our host ports are the #1 cold-start failure.
# Catch it here with a readable message instead of an opaque bind error.
CHECK_PORTS="$FALKOR_PORT $BROWSER_PORT $BRIDGE_PORT $UI_PORT"
if [ "$LOGSPINE" -eq 1 ]; then
  IGGY_PORT="$(get_env LASER_IGGY_PORT)"; IGGY_PORT="${IGGY_PORT:-8090}"
  IGGY_HTTP="$(get_env LASER_IGGY_HTTP_PORT)"; IGGY_HTTP="${IGGY_HTTP:-3000}"
  CHECK_PORTS="$CHECK_PORTS $IGGY_PORT $IGGY_HTTP"
fi
for p in $CHECK_PORTS; do
  squatter="$(docker ps --filter "publish=$p" --format '{{.Names}}' 2>/dev/null | grep -v "^${PROJECT_NAME}-" || true)"
  if [ -n "$squatter" ]; then
    warn "host port $p is already published by: $squatter"
    warn "  -> 'docker rm -f $squatter'  (or change the port in .env), then re-run"
    die "port conflict on $p"
  fi
done
ok "host ports $(echo $CHECK_PORTS | tr ' ' '/') are free"

# --- 1. destroy the old stack ------------------------------------------------
step "Removing previous stack (directive 7: fresh containers, fresh volumes)"
# Always include the logspine profile on teardown so profiled containers from a
# previous run are removed too, regardless of how this run was invoked.
if [ "$WIPE_VOLUMES" -eq 1 ]; then
  docker compose -f "$COMPOSE_FILE" --profile logspine down --volumes --remove-orphans --timeout 20 || true
  ok "containers + named volumes removed"
else
  docker compose -f "$COMPOSE_FILE" --profile logspine down --remove-orphans --timeout 20 || true
  warn "volumes KEPT (--keep-volumes) — this is not a clean directive-7 run"
fi

# --- 2. build ----------------------------------------------------------------
step "Building images"
# --pull=false so a cached base image is reused; the bridge image still needs
# PyPI for `pip install -r app/bridge/requirements.txt` on a cold build. That is
# the one network dependency in this stack and it is stated, not hidden.
"${DC[@]}" build --pull=false
ok "build complete"

# --- 3. start ----------------------------------------------------------------
step "Starting stack$( [ "$LOGSPINE" -eq 1 ] && echo ' (+ logspine profile)' )"
if ! "${DC[@]}" up -d --force-recreate; then
  printf '\n'
  # `condition: service_healthy` makes compose refuse to start dependants when a
  # dependency never goes healthy. That is correct, but the message is terse, so
  # translate the one failure mode we KNOW the log spine has.
  if [ "$LOGSPINE" -eq 1 ] && "${DC[@]}" logs iggy 2>&1 | grep -qiE 'io_uring|ShardJoinFailures'; then
    cat <<'EOS'
  ------------------------------------------------------------------------
  iggy-server could not start: io_uring opcodes missing from this HOST KERNEL.

  This is a host kernel limitation, not a bug in this stack and not a
  misconfiguration. The `-ld` iggy fork (which laser-plane REQUIRES) issues
  io_uring ops whose opcodes landed in ~Linux 6.11 (suspect class
  IORING_OP_BIND / IORING_OP_LISTEN). The ring sets up fine and then panics
  at runtime:

    Error: ShardJoinFailures { ... Panic { message: "the thread pool is
      needed but no worker thread is running" } }

  VERIFIED KERNEL MATRIX (seccomp AND apparmor unconfined in every row):
    6.6.87    WSL2 / Docker Desktop, Windows 11    amd64   FAIL
    6.8.0-117 stock Ubuntu 24.04, colima VZ        arm64   FAIL
    6.14.0-37 Ubuntu 25.04, lima VZ                arm64   PASS

  It is NOT WSL2-specific: it reproduces on stock Ubuntu 24.04 with no WSL
  involved. Also controlled for: root vs uid-10001, kernel.io_uring_disabled=0,
  :latest vs :edge. Upstream apache/iggy boots clean on every host in the
  matrix, but is NOT a substitute — laser-plane is VSR-locked to the fork and
  fails against upstream with "Timed out waiting for VSR response header,
  code 12".

  Run the log spine on a kernel >= ~6.11 (lima Ubuntu 25.04 recipe:
  plan/gates/GATE0-laserdata-local.md). On an older kernel, run WITHOUT
  --profile logspine: the rest of the stack is unaffected and comes up green.
  ------------------------------------------------------------------------
EOS
  else
    "${DC[@]}" logs --tail 40 2>&1 || true
  fi
  die "compose could not bring the stack up"
fi
EXPECTED="$("${DC[@]}" config --services | sort | tr '\n' ' ')"
ok "requested services: $EXPECTED"

# --- 4. BLOCK until healthy --------------------------------------------------
step "Waiting for every service to report healthy (timeout ${TIMEOUT}s, no sleeps in the gate)"
deadline=$(( $(date +%s) + TIMEOUT ))
expected_count="$("${DC[@]}" config --services | grep -c . )"
last_line=""

while :; do
  ids="$("${DC[@]}" ps -q 2>/dev/null || true)"
  running_count=0; healthy_count=0; pending=""; broken=""

  for id in $ids; do
    running_count=$(( running_count + 1 ))
    svc="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id" 2>/dev/null || echo '?')"
    state="$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo '?')"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id" 2>/dev/null || echo '?')"
    restarts="$(docker inspect -f '{{.RestartCount}}' "$id" 2>/dev/null || echo 0)"

    if [ "$health" = "healthy" ]; then
      healthy_count=$(( healthy_count + 1 ))
    elif [ "$state" = "exited" ] || [ "$state" = "dead" ] || [ "${restarts:-0}" -ge 3 ]; then
      broken="$broken $svc($state,restarts=$restarts)"
    else
      pending="$pending $svc($state/$health)"
    fi
  done

  if [ -n "$broken" ]; then
    printf '\n'
    warn "crash-looping or exited:$broken"
    for b in $broken; do
      s="${b%%(*}"
      printf '\n----- last 40 log lines: %s -----\n' "$s"
      "${DC[@]}" logs --tail 40 "$s" 2>&1 || true
    done
    die "a service failed to start — see logs above"
  fi

  if [ "$running_count" -ge "$expected_count" ] && [ "$healthy_count" -ge "$expected_count" ]; then
    printf '\r%-78s\r' " "
    ok "all $healthy_count/$expected_count services healthy"
    break
  fi

  now="$(date +%s)"
  if [ "$now" -ge "$deadline" ]; then
    printf '\n'
    warn "still not healthy:$pending"
    "${DC[@]}" ps
    die "timed out after ${TIMEOUT}s waiting for health"
  fi

  line="  waiting ($healthy_count/$expected_count healthy)$pending  [$(( deadline - now ))s left]"
  [ "$line" != "$last_line" ] && { printf '\r%-100s\r%s' " " "$line"; last_line="$line"; }
  # 1s poll of the runtime's own health state — this is a poll of TRUTH, not a
  # blind sleep standing in for readiness.
  sleep 1
done

# --- 5. readiness summary ----------------------------------------------------
step "Readiness summary"

FALKOR_C="$("${DC[@]}" ps -q falkordb)"
[ -n "$FALKOR_C" ] || die "falkordb container id not found"

pong="$(docker exec "$FALKOR_C" redis-cli PING 2>&1 || true)"
[ "$pong" = "PONG" ] && ok "falkordb   RESP  ${BIND_ADDR}:${FALKOR_PORT}   PING -> PONG" \
                     || warn "falkordb   RESP  PING -> $pong"

# Real graph round-trip: write a node, read it back, drop it. Proves the graph
# MODULE is loaded, not just that redis answers — which is exactly the false
# green a stray redis-server on 6379/6399 produces (memory/SCHEMA.md §5).
docker exec "$FALKOR_C" redis-cli GRAPH.QUERY "__readiness" "CREATE (:Probe {n:1})" >/dev/null 2>&1 || true
probe="$(docker exec "$FALKOR_C" redis-cli GRAPH.QUERY "__readiness" "MATCH (p:Probe) RETURN count(p)" 2>&1 || true)"
docker exec "$FALKOR_C" redis-cli GRAPH.DELETE "__readiness" >/dev/null 2>&1 || true
case "$probe" in
  *1*) ok "falkordb   GRAPH GRAPH.QUERY create+match round-trip -> 1 node" ;;
  *)   warn "falkordb   GRAPH round-trip returned: $probe" ;;
esac

# NOTE on probing, two traps that both report FAILURE for a WORKING port:
#   1. `timeout N cat < /dev/tcp/host/port` exits non-zero when the connection
#      SUCCEEDS (it blocks, then gets killed). Never use it. Use `nc -z` or
#      PowerShell `Test-NetConnection -Port N -InformationLevel Quiet`.
#   2. `curl -o /dev/null` under MSYS_NO_PATHCONV=1 in git bash: /dev/null is no
#      longer translated, curl cannot write to it, and curl exits 23 *after* a
#      perfectly good HTTP 200. Verified on this box.
# So: no -o at all. Append a sentinel to the write-out and split on it.
http_probe() { # name url [expected_code]
  want="${3:-200}"
  resp="$(curl -sS -m 6 -w '\n__CODE__%{http_code}' "$2" 2>/dev/null || true)"
  code="${resp##*__CODE__}"
  case "$code" in
    "$want") ok   "$1 -> HTTP $code   $2" ;;
    "")      warn "$1 -> unreachable  $2" ;;
    *)       warn "$1 -> HTTP $code (wanted $want)   $2" ;;
  esac
}
http_probe "browser    UI    " "http://${BIND_ADDR}:${BROWSER_PORT}/"
http_probe "bridge     HEALTH" "http://${BIND_ADDR}:${BRIDGE_PORT}/health"
http_probe "bridge     UI    " "http://${BIND_ADDR}:${BRIDGE_PORT}/ui/"
http_probe "bridge     GRAPH " "http://${BIND_ADDR}:${BRIDGE_PORT}/graph"
http_probe "ui         PAGE  " "http://${BIND_ADDR}:${UI_PORT}/index.html"

# The health BODY, not just its status line — `falkordb.reachable` is the field
# that distinguishes "the bridge is up" from "the bridge can remember".
health="$(curl -sS -m 6 "http://${BIND_ADDR}:${BRIDGE_PORT}/health" 2>/dev/null || true)"
case "$health" in
  *'"reachable":true'*) ok "bridge     GRAPH falkordb reachable from inside the bridge" ;;
  "")                   warn "bridge     HEALTH returned no body" ;;
  *)                    warn "bridge     HEALTH says the memory plane is NOT reachable: $health" ;;
esac

if [ "$LOGSPINE" -eq 1 ]; then
  IGGY_HTTP_PORT="$(get_env LASER_IGGY_HTTP_PORT)"; IGGY_HTTP_PORT="${IGGY_HTTP_PORT:-3000}"
  http_probe "iggy       HTTP  " "http://${BIND_ADDR}:${IGGY_HTTP_PORT}/"
else
  warn "logspine   OFF   (re-run with --profile logspine on a kernel >= ~6.11; see README)"
fi

step "Stack is up"
"${DC[@]}" ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

cat <<EOF

  graph (RESP)     redis-cli -h ${BIND_ADDR} -p ${FALKOR_PORT}
  graph browser    http://${BIND_ADDR}:${BROWSER_PORT}
  bridge health    http://${BIND_ADDR}:${BRIDGE_PORT}/health
  bridge openapi   http://${BIND_ADDR}:${BRIDGE_PORT}/v1/openapi.json
  bridge mcp       http://${BIND_ADDR}:${BRIDGE_PORT}/mcp        (streamable-http)
  projector        http://${BIND_ADDR}:${BRIDGE_PORT}/ui/        (same-origin)
  projector (alt)  http://${BIND_ADDR}:${UI_PORT}                (static host)
  warm graph       ${GRAPH_NAME}

  tear down        ./down.sh
EOF
