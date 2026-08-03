#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# PALIMPSEST — full teardown. Containers, networks AND named volumes.
# This is destructive by design: directive 7's definition of done is a fresh
# stack, and you cannot prove "fresh" if state survives.
#
# Usage:
#   ./down.sh                 remove containers, networks, volumes (default)
#   ./down.sh --keep-volumes  remove containers/networks, KEEP graph + spine data
#   ./down.sh --images        also remove the locally built bridge/ui images
#
set -euo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

COMPOSE_FILE="docker-compose.yml"
VOLUME_FLAG="--volumes"
RM_IMAGES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-volumes) VOLUME_FLAG=""; shift ;;
    --images)       RM_IMAGES=1; shift ;;
    -h|--help)      sed -n '3,13p' "$0"; exit 0 ;;
    *) echo "down.sh: unknown argument '$1' (see --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }

# Always name the logspine profile so profiled containers are torn down too,
# whether or not this run ever started them.
step "Tearing down PALIMPSEST${VOLUME_FLAG:+ (including volumes)}"
docker compose -f "$COMPOSE_FILE" --profile logspine down \
  ${VOLUME_FLAG:+$VOLUME_FLAG} --remove-orphans --timeout 20
ok "compose down complete"

if [ "$RM_IMAGES" -eq 1 ]; then
  step "Removing locally built images"
  for img in palimpsest-bridge palimpsest-ui; do
    docker image rm -f "$img" >/dev/null 2>&1 && ok "removed $img" || true
  done
fi

step "Remaining PALIMPSEST resources (should be empty)"
docker ps -a --filter "name=palimpsest-" --format '  {{.Names}}\t{{.Status}}' || true
docker volume ls --filter "name=palimpsest_" --format '  volume {{.Name}}' || true
echo
