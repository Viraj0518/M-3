# SPDX-License-Identifier: Apache-2.0
#
# PALIMPSEST ui — the REAL projector (app/web/index.html) on a static host.
#
# Zero npm install: no lockfile, no registry round-trip, no network needed at
# build time. A judge's cold start must not depend on npmjs.org being up.
#
# Was alpine + `busybox httpd`; current Alpine images no longer ship the httpd
# applet in the default busybox binary (verified: `exec: line 14: httpd: not
# found`), and `apk add busybox-extras` would add a network dependency to the
# build. node:22-bookworm-slim + a ~50-line server instead.
#
# BUILD CONTEXT IS THE REPO ROOT so `app/web` is reachable — the page is the
# repo's, not a copy living in deploy/. One projector, one file, two hosts
# (this service on :5173 and the bridge's own /ui/ mount on :8931).
FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PALIMPSEST_UI_PORT=5173

WORKDIR /app
COPY deploy/ui/server.js deploy/ui/healthcheck.js ./
COPY app/web/ /www/

USER node

EXPOSE 5173
CMD ["node", "/app/server.js"]
