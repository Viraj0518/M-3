# Deploy

## Public demo (Cloudflare Pages)

The projector UI is deployed as a static site — it runs self-contained in **MOCK mode**
(deterministic canned data) with no backend, so the public page is a working preview of
the live demo without needing the local stack.

- **Live:** https://palimpsest-740.pages.dev
- Project: `palimpsest` (Cloudflare Pages), production branch `main`.

Redeploy after a UI change:

```bash
npx wrangler pages deploy app/web --project-name palimpsest --branch main
```

When the bridge is reachable at its origin the same page flips from MOCK to LIVE
(graph + ring off real FalkorDB); on `*.pages.dev` there is no bridge, so it stays MOCK.

## Local full stack (the real demo)

Everything runs locally — no cloud dependency, bring-your-own-key (see `.env.example`):

- **FalkorDB** (memory) — container, `127.0.0.1:6401`
- **LaserData** (log spine) — laser-stack iggy+plane; requires a **kernel ≥ ~6.11** host
  (the `-ld` iggy fork needs recent io_uring opcodes — see `plan/gates/GATE0-laserdata-local.md`
  for the kernel matrix and a no-admin macOS recipe via lima Ubuntu 25.04)
- **Bridge** (REST + MCP + OpenAPI + CLI) — `127.0.0.1:8931`, `GET /health`
- **UI** — `app/web/index.html`, served by the bridge at `/ui/` or any static host

The container compose for the full stack lands under `deploy/` (final-deploy lane).
Definition of done for any milestone: commit → fresh container build → old containers
removed → full end-to-end walkthrough on the fresh stack.
