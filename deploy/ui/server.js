// SPDX-License-Identifier: Apache-2.0
//
// PALIMPSEST ui — static host for app/web/index.html.
//
// Node rather than `busybox httpd`: current Alpine images no longer ship the
// httpd applet in the default busybox binary (verified on this box —
// `exec: line 14: httpd: not found`), and `apk add busybox-extras` would make
// the image build depend on the network, which a judge's cold start must not.
//
// The page it serves is the REAL projector. It hard-codes
// `const BRIDGE = "http://127.0.0.1:8931"` (app/web/index.html:581) and polls
// /graph, /ring and /stream_tail from the BROWSER — so it reaches the bridge
// over the published host port, never over the compose network. That is also
// why the bridge sets `allow_origins=["*"]`: this page is regularly opened from
// a different origin (:5173, or even file://), and the bridge binds 127.0.0.1
// and holds no credentials.
'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const PORT = Number(process.env.PALIMPSEST_UI_PORT || 5173);
const ROOT = '/www';
const BRIDGE = process.env.PALIMPSEST_BRIDGE_PUBLIC_URL || 'http://127.0.0.1:8931';

// Advertised for any future page that prefers injection over the hard-coded
// constant. No secrets pass through here — only a public localhost URL.
const CONFIG_JS = `window.__PALIMPSEST__ = ${JSON.stringify({ bridge: BRIDGE })};\n`;

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

const server = http.createServer((req, res) => {
  let p = new URL(req.url, 'http://localhost').pathname;
  if (p === '/') p = '/index.html';
  if (p === '/config.js') {
    res.writeHead(200, { 'content-type': TYPES['.js'], 'cache-control': 'no-store' });
    return res.end(CONFIG_JS);
  }
  // Contain to ROOT: reject anything that escapes after normalisation.
  const file = path.join(ROOT, path.normalize(p).replace(/^(\.\.[/\\])+/, ''));
  if (!file.startsWith(ROOT)) {
    res.writeHead(403);
    return res.end('forbidden\n');
  }
  fs.readFile(file, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      return res.end('not found\n');
    }
    res.writeHead(200, {
      'content-type': TYPES[path.extname(file)] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    return res.end(buf);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  process.stdout.write(`[ui] serving ${ROOT} on :${PORT} -> bridge ${BRIDGE}\n`);
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => server.close(() => process.exit(0)));
}
