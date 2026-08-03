# SPDX-License-Identifier: Apache-2.0
#
# PALIMPSEST bridge — THE REAL app/bridge/, containerised.
#
# ── BUILD CONTEXT IS THE REPO ROOT ──────────────────────────────────────────
# Not `deploy/bridge/`. The bridge is a package inside the repo and cannot be
# built from a narrower context:
#   * `app/bridge/server.py`     imports `memory.config` + `memory.taxonomy`
#   * `app/bridge/graphstore.py` imports `memory.config` (port 6401 lives there
#                                and is deliberately never a literal in code)
#   * `app/bridge/rest.py:477`   mounts `config.REPO_ROOT/app/web` at `/ui/`,
#                                so the projector must be inside the image
# `memory/config.py` computes REPO_ROOT as `__file__.parent.parent`, so copying
# the tree to `/app` makes REPO_ROOT `/app` and `/app/app/web` the projector —
# which is exactly the layout below.
#
# ── PYTHON 3.12 IS A FLOOR, NOT A PREFERENCE ────────────────────────────────
# app/bridge/requirements.txt says so. (server.py itself is deliberately still
# importable on 3.9 with nothing installed — both the `mcp` and the `falkordb`
# imports are guarded — but the memory plane and the MCP transport need 3.12.)
#
# ── NETWORK AT BUILD TIME ───────────────────────────────────────────────────
# This image DOES need PyPI for `pip install`, unlike the ui image. That is the
# one honest regression versus shipping a dependency-free placeholder, and it
# buys the real bridge: real Cypher, the real /health contract, the real MCP
# surface. Build once, before you need it; the layer is cached on rebuild
# because requirements.txt is copied on its own line.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Defaults only; docker-compose.yml sets both explicitly. BRIDGE_HOST MUST be
# 0.0.0.0 in a container or uvicorn binds the container's own loopback and the
# published port connect-refuses. memory/config.py defaults it to 127.0.0.1,
# which is right for a laptop process and wrong for a container.
ENV BRIDGE_HOST=0.0.0.0 \
    BRIDGE_PORT=8931

WORKDIR /app

# Dependency layer first, on its own, so an edit to the bridge source does not
# re-run the install.
COPY app/bridge/requirements.txt /app/app/bridge/requirements.txt
RUN pip install --no-cache-dir -r /app/app/bridge/requirements.txt

# The two packages the bridge actually imports. Nothing else from the repo is
# copied: the eval harness, plan/, agents/ and the git history are not runtime.
COPY memory/ /app/memory/
COPY app/ /app/app/

COPY deploy/bridge/healthcheck.py /usr/local/bin/palimpsest-healthcheck.py

# Drop root. The bridge needs no privileges — it opens one TCP client
# connection to FalkorDB and one listening socket.
RUN useradd --create-home --uid 10001 palimpsest \
 && chown -R palimpsest:palimpsest /app
USER palimpsest

EXPOSE 8931

# `-m` so the package-relative imports in rest.py resolve, and so the listen
# host/port come from memory/config.py (BRIDGE_HOST / BRIDGE_PORT) rather than
# a second copy of the truth on a uvicorn command line.
CMD ["python", "-m", "app.bridge.rest"]
