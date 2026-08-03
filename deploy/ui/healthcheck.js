// SPDX-License-Identifier: Apache-2.0
// Container healthcheck for the ui static host. Asserts only that it serves its
// own page — it deliberately does NOT probe the bridge. The hard dependency is
// enforced at cold start (`depends_on: bridge: service_healthy`); runtime
// degradation of the bridge is surfaced IN the page, which flips its own badge
// to "MOCK · BRIDGE OFFLINE". A ui healthcheck that failed when the bridge
// blinked would make the removal test ambiguous about which service broke.
'use strict';

const port = Number(process.env.PALIMPSEST_UI_PORT || 5173);
const url = `http://127.0.0.1:${port}/index.html`;

const timer = setTimeout(() => {
  process.stderr.write(`healthcheck: timeout hitting ${url}\n`);
  process.exit(1);
}, 4000);

fetch(url)
  .then((r) => { clearTimeout(timer); process.stdout.write(`${r.status}\n`); process.exit(r.ok ? 0 : 1); })
  .catch((e) => { clearTimeout(timer); process.stderr.write(`healthcheck: ${e.message}\n`); process.exit(1); });
