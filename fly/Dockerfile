# PALIMPSEST hosted bridge — ONE self-contained Fly machine.
#
# FalkorDB (redis-server + the graph module) AND the bridge live in a SINGLE
# container, so the public demo backend needs no external database. The public
# Pages UI (palimpsest-740.pages.dev) can then point at a real, live backend.
#
# Base: falkordb/falkordb (Debian trixie) already carries redis-server, redis-cli
# and the graph module at /var/lib/falkordb/bin/falkordb.so. We add Python + the
# bridge on top and swap the entrypoint. Debian trixie ships Python >= 3.12,
# which is what app/bridge/requirements.txt requires.
#
# BUILD CONTEXT IS THE REPO ROOT (so the COPYs below can reach app/ and memory/):
#     docker build -f fly/Dockerfile -t palimpsest-bridge .
FROM falkordb/falkordb:latest

# ── Python + an isolated venv (Debian trixie is PEP-668 externally-managed, so
#    a venv is mandatory — never `pip install` into the system interpreter). ──
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV VENV=/opt/venv
RUN python3 -m venv "$VENV"
ENV PATH="$VENV/bin:$PATH"

# ── bridge python deps (layer cached on requirements.txt alone) ──────────────
COPY app/bridge/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt

# ── the app: memory plane + bridge + the projector UI (served at /ui/) ───────
WORKDIR /app
COPY memory/ /app/memory/
COPY app/ /app/app/

# ── runtime config ───────────────────────────────────────────────────────────
# FalkorDB answers on loopback INSIDE the container (127.0.0.1:6401); the bridge
# binds ALL interfaces (0.0.0.0:8931) because Fly routes external traffic to it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FALKORDB_HOST=127.0.0.1 \
    FALKORDB_PORT=6401 \
    BRIDGE_PORT=8931 \
    BRIDGE_BIND_HOST=0.0.0.0 \
    FALKORDB_DATA_DIR=/data

RUN mkdir -p /data
COPY fly/entrypoint.sh /usr/local/bin/palimpsest-entrypoint.sh
RUN chmod +x /usr/local/bin/palimpsest-entrypoint.sh

EXPOSE 8931
ENTRYPOINT ["/usr/local/bin/palimpsest-entrypoint.sh"]
