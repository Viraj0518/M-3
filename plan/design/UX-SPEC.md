# PALIMPSEST — UX Design Specification

**Status:** design deliverable, for the UI codex to build from. This is a spec, not code.
**Author:** UX design lane (Fable 5). **Date:** 2026-08-03.
**Scope:** two surfaces — (1) the projector WebUI at `app/web/index.html`, and (2) the MCP
interactive-widget surface emitted by `app/bridge/`.
**Source of truth this conforms to:** `GOAL.md`, `plan/synthesis.json` (architecture region [9],
`demo_script`, the COLD|WARM ablation), `plan/research/mcp-widgets-guide.md` (the MEASURED host
matrix), and the live bridge contract in `app/bridge/server.py`.

---

## 0. The one job, and the one sentence

Every screen and every widget exists to make a single sentence physically undeniable to a judge
standing twenty feet from a projector:

> The agent already knows **who you are**, **who it is**, and has a **constant datastream** of what
> is happening right now. It **pulls** the context it needs instead of **asking** for it — so a
> conversation that used to take four turns resolves in **one**.

The design translation of that sentence is a single, repeated visual grammar:

- **"Already knows"** → the graph is *already dense* when the demo opens. No empty state on stage,
  no loading spinner, no "let me look that up." Density is the deliverable.
- **"Pulls, doesn't ask"** → the WARM verdict panel shows **`0 questions asked · 1 turn`**. The COLD
  panel shows the interrogation: **`4 turns`**, each turn a question the agent had to ask because it
  had no memory. The *turns-to-answer counter is the headline number* — bigger than anything else on
  the split screen.
- **"Constant datastream"** → offsets never stop climbing on the bottom strip, even while nothing
  else is happening. Motion is ambient, not triggered.

If a design choice does not serve that sentence, cut it. This document is opinionated on purpose.

---

## 1. What already exists, and this spec's relationship to it

`app/web/index.html` is a real, working 1414-line single-file projector UI with a genuine
MOCK↔LIVE architecture. **The UI was built first and the bridge conforms to it** (see the docstring
at `server.py:856`). So this is not a greenfield spec — it is a **design ratification plus a
correction list**. The existing visual identity is good and should be *kept*; the corrections are
about honesty, the `?bridge=` origin, and the MCP surface that does not exist yet.

**Keep as-is (ratified):** the dark projector palette, the mono+sans pairing, the eyebrow-numbered
four-region layout, the COLD|WARM split with the turns card, the bottom LaserData/gate strip, the
`normalizeGraph` wire contract, the approval card with the 20s countdown.

**Correct (§4.4 Honesty Rules — first-class, non-negotiable):** the ring is currently fabricated
client-side by `findThreeHopPath()` and `run_time_ms` defaults to the literal `2.45` even in LIVE.
That must stop. In LIVE the UI renders **only** what the server returns.

**Add (does not exist yet):** `?bridge=<origin>` support for the hosted Fly bridge; explicit
per-state specs; and the entire MCP widget surface (§5).

---

## 2. Design system — the tokens

The existing token set in `index.html` is well-chosen for a projector and is adopted verbatim as
the system of record. Documented here so the MCP widgets and any new component derive from the same
values rather than reinventing them.

### 2.1 Color

The ground is a **blue-black**, not a neutral black — `#05070a` carries a slight cyan bias so the
cyan accent reads as *of* the surface rather than painted on. Neutrals are picked, not defaulted:
the panel greys (`#0a0e13`, `#0e141b`) and line greys (`#202a35`, `#334252`) all sit on the same
blue axis as the ground.

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#05070a` | projector ground (blue-black, chosen not default) |
| `--panel` / `--panel-raised` | `#0a0e13` / `#0e141b` | region surfaces |
| `--line` / `--line-strong` | `#202a35` / `#334252` | dividers, borders |
| `--text` / `--muted` / `--faint` | `#f4f8fb` / `#91a0af` / `#5e6c79` | type hierarchy, 3 steps |
| `--cyan` | `#4be1f2` | **the accent.** Events, LaserData, WARM, "live/now" |
| `--violet` | `#bba7ff` | Pages, the analyst agent, "history/ever" |
| `--amber` | `#ffc15c` | Actors, the commander, "pending human decision" |
| `--green` | `#69e6a5` | Agents, "approved / fired / healthy" (semantic, not accent) |
| `--red` | `#ff6971` | Cases, COLD, "stale / critical / dismissed" (semantic) |
| `--blue` | `#70a7ff` | Claims/Entities, hyperlinks |

**Semantic color is separate from the accent.** Cyan is the brand accent and also happens to mean
"now/warm" — that overload is deliberate and load-bearing (the whole thesis is that *now* is only
meaningful against *ever*). Green/amber/red carry state (good/pending/bad) and never double as the
accent. The **node-type palette is a fixed legend** — Actor=amber, Page=violet, Event=cyan,
Case=red, Agent=green, Claim/Entity=blue, Action=pink `#f19ce4`. This legend is a promise: a judge
learns it once in the first ten seconds and reads the graph by color forever after. Never recolor a
node type.

### 2.2 Type

Two families, deliberately paired, each doing a job the other cannot:

- **Data / mono — `ui-monospace` stack (SFMono, Menlo, Consolas…).** Everything a judge is invited
  to *verify* is monospaced: offsets, `run_time_ms`, node/edge counts, the turns counter, Cypher,
  URLs, agent names. Monospace here is not a stylistic tic — it signals "this is a measured value,
  not a marketing number," and it makes digits column-align under `font-variant-numeric:
  tabular-nums` so a climbing offset reads as motion, not reflow.
- **Narrative / sans — Inter stack.** Verdicts, case titles, the approval question, agent feed
  prose. The human-readable layer.

**CSP note (critical, wifi-off invariant):** both stacks are **system fonts** — no `@font-face`, no
CDN, no webfont URL. `GOAL.md` guardrail #3/#4 and the demo's "wifi physically off" requirement make
a font network request an unacceptable single point of failure. The silent-fallback risk that
normally argues *for* inlining a face argues here *against* linking one at all. Keep it system-only.
This is the one place the studio's usual "pair a characteristic display face" instinct is
correctly overridden by the subject: a control-room projector's typographic personality comes from
the *monospace-as-evidence* move, not from a display serif.

**Scale (projector-tuned, must survive 20 feet):**

| Use | Size | Weight | Notes |
|---|---|---|---|
| The turns-to-answer digit | `clamp(34px, ~39px, …)` mono | 820 | the single biggest number on the split |
| `run_time_ms` value | `clamp(34px, 3.6vw, 56px)` mono | 800 | letter-spacing `-.06em`, tabular |
| Verdict text | `clamp(15px, 1.12vw, 21px)` sans | 780 | the sentence a judge reads |
| Panel title | 19px sans | 735 | region label |
| Eyebrow / label | 10–11px mono | 760 | uppercase, `letter-spacing .12–.13em` |
| Agent feed body | 14px sans | — | line-height 1.42 |
| Thinking-frame body | 12px mono | — | monospace = machine reasoning |

The rule: **the number a judge should remember is always the largest thing in its region.** On the
split that is the turns counter. On the single graph it is `run_time_ms`. On the strip it is the
event/node/rejected triple.

### 2.3 Motion

Motion is meaning, never decoration. Four sanctioned motions, and no others:

1. **Ambient offset climb** — the raw offset on the bottom strip increments continuously. This is
   the "constant datastream." It runs even in the empty/idle state.
2. **Node bloom** — a new node fades+scales in over ~400ms at its force-graph position when it
   arrives from the stream. This is the phone-edit beat ("a node blooms within 2 seconds").
3. **Ring trace** — a path lights node-by-node in sequence (~120ms per hop) along the *exact* ids
   the server returned, ending in the historical cluster. This is the money shot. It fires **only**
   on `fired:true` (§4.4).
4. **Split reveal** — the COLD panel fast-forward-builds while WARM holds, verdicts land together.

`prefers-reduced-motion: reduce` collapses all four to instant state changes (already handled in
the existing CSS — keep it). Nothing pulses, breathes, or shimmers for atmosphere alone except the
single "thinking" status dot, which earns its keep by signaling the agent is alive.

### 2.4 Theme

The projector UI is **deliberately single-theme (dark only)** — a control-room screen in a dark
conference hall. `<meta name="color-scheme" content="dark">` is set and correct. This is a
committed aesthetic choice per the design fundamentals' "neon arcade screen" exemption, not an
omission. **The MCP widgets are the opposite** — they render inside someone else's host (Claude
Desktop light or dark) and MUST be theme-aware by consuming the host's pushed CSS variables (§5.6).
Two surfaces, two correct answers.

---

## 3. SURFACE 1 — The projector WebUI

### 3.1 Layout — the four regions + the split

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  ◉ PALIMPSEST   [ ● MOCK · BRIDGE OFFLINE ]            (R) 3-hop  (S) Cold|Warm  (A) …  │  70px topbar
├──────────────────────────────────────────────────────────┬───────────────────────────┤
│  01  LIVE GRAPH        ·Actor ·Page ·Event ·Case ·Agent   │  02  AGENT LANE   ● Thinking│
│                                                            │  ┌──────────────────────┐ │
│    ╭─ force-directed canvas ───────────────────────╮      │  │ 11:42 watcher·handoff│ │
│    │        (Event)                                 │      │  │ watcher → commander  │ │
│    │       ╱   ╲          nodes bloom in,           │      │  │ 11:42 commander·think│ │
│    │  (Page)   (Actor)    edges as they arrive      │ 1.82fr│ │ Planning Wave 1 ▊    │ │  .92fr
│    │      ╲   ╱   ╲                                  │      │  │ analyst·return       │ │
│    │    (Claim)  (Case)                              │      │  │ EVER: ring_score 0.93│ │
│    │                                                 │      │  └──────────────────────┘ │
│    │   ┌───────────────────────┐                     │      ├───────────────────────────┤
│    │   │ 3-HOP RING · run_time │   24 nodes·32 edges │      │  03  CASE / ACTION        │
│    │   │      2.45 ms          │                     │      │  CASE P-2048 · OPEN  [HIGH]│
│    │   │ MATCH p=(edit)-[*1..3]│                     │      │  ┌─ approval ──── 00:20 ─┐ │  1.08fr
│    │   └───────────────────────┘                     │      │  │ Escalate this        │ │
│    │                                                 │ .72fr│  │ campaign?            │ │
│    ╰─────────────────────────────────────────────────╯      │  │ [Approve] [Dismiss]  │ │
│                                                            │  └──────────────────────┘ │
│                                                            │  Fired actions: Discord…  │
├──────────────────────────────────────────────────────────┴───────────────────────────┤
│  L LaserData  raw 041,208  salient 000,912  lag 0 │ GATE 41,208 in·912·97% rej │ Bridge│  76px strip
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Grid: `70px / minmax(0,1fr) / 76px` rows; workspace `minmax(0,1.82fr) minmax(380px,.72fr)` columns;
right rail `.92fr / 1.08fr`. This is the existing layout and it is correct — the graph dominates
(it *is* the pitch), the rail stacks reasoning-over-decision (the order a judge's eye should travel:
"what is it thinking" → "what does it want to do"), and the strip is a persistent evidence footer.

**Eyebrow numbering (01/02/03) is legitimate here** — not decoration. The numbers encode the
demo's reading order and the data-flow sequence: signal lands in the graph (01), agents reason about
it (02), a case and an action result (03). A judge glancing at the numbers learns the pipeline
direction. Keep them.

### 3.2 The split — COLD | WARM, the headline beat

When `S` is pressed the LIVE GRAPH region swaps its single canvas for a 1:1 split. This is the beat
that wins (`GOAL.md` victory condition #1, never cut). Design requirements:

```
┌─────────────────── COLD ──────────────────┬─────────────────── WARM ──────────────────┐
│ COLD · EMPTY MEMORY      8 nodes·offset 0  │ WARM · FULL HISTORY   24 nodes·3-hop joined│
│                                            │                                            │
│   sparse scatter, red-tinted ground        │   dense cluster, cyan-tinted ground        │
│   (palimpsest_cold, replaying from 0)      │   (palimpsest, the real memory)            │
│                                            │                                            │
│ ┌────┬─────────────────────────────────┐  │ ┌────┬─────────────────────────────────┐  │
│ │ 04 │ VERDICT · insufficient context   │  │ │ 01 │ VERDICT · 0 questions asked      │  │
│ │TURNS│ "Routine edit. No action."      │  │ │TURN │ "Coordinated campaign,          │  │
│ │    │ AGENT: Which account? Which page?│  │ │    │  fourth move. Escalate."        │  │
│ │    │ Has this happened before?        │  │ │    │ Context pulled automatically     │  │
│ └────┴─────────────────────────────────┘  │ └────┴─────────────────────────────────┘  │
└────────────────────────────────────────────┴────────────────────────────────────────────┘
```

- **The turns counter is the protagonist.** `04` on the left, `01` on the right, in the biggest
  mono weight on the screen (820, ~39px). The left counter is red, the right is cyan. This single
  contrast *is* "pulls instead of asks" — four questions vs zero. Consider animating the COLD
  counter *ticking up* 1→2→3→4 as its transcript prints each question, so a judge literally watches
  the interrogation accrue.
- **Ground tint** distinguishes the two worlds pre-attentively: COLD gets a faint red radial, WARM
  a faint cyan radial (already in CSS). At twenty feet the *color of the half* tells you which is
  which before any text is read.
- **Verdict text is the sans narrative layer**, ~21px, 780. Both verdicts land at the same moment;
  the reveal times the COLD panel's fast-forward build to finish as WARM's verdict is already
  showing, so the eye lands on both sentences together.
- **Honesty (§4.4):** the COLD verdict/transcript is a *scripted narration of a real replay result*,
  acceptable as MOCK content and acceptable in LIVE **only** when driven by an actual
  `palimpsest_cold` replay. It must never claim a cold verdict the ablation harness did not produce.

### 3.3 Component inventory

| Component | Region | Data source (LIVE) | State it encodes |
|---|---|---|---|
| Connection badge | topbar | poll success/fail | MOCK / LIVE / STALE (§4.3) |
| Demo control buttons | topbar | keyboard R/S/A | which beat is armed |
| Force-graph canvas | 01 | `GET /graph` → `normalizeGraph` | the memory, live |
| Graph count chip | 01 | `meta.node_count/edge_count` | **exact server counts** (§4.4) |
| Ring result card | 01 | `POST /ring` → `run_time_ms`,`paths` | the money query (§4.4) |
| Legend | 01 | static | the color contract |
| Turns card ×2 | 01-split | ablation harness | **the headline number** |
| Verdict card ×2 | 01-split | ablation harness | cold vs warm decision |
| Agent feed rows | 02 | `GET /stream_tail` | live coordination + SSE thinking |
| Lane status dot | 02 | stream liveness | thinking / idle / dead |
| Case summary | 03 | `case.opened` records | current investigation |
| Approval card | 03 | `ui_prompt` / `ask` verb | **the human gate** (§3.4) |
| Countdown | 03 | 20s timer | default-on-timeout safety |
| Action rows | 03 | `action.executed` records | **real fired actions only** (§4.4) |
| LaserData offsets | strip | `stream_tail` offsets | the constant datastream |
| Salience gate stat | strip | offsets | "memory, not a log dump" |
| Bridge sync chip | strip | poll timestamp | which origin, last sync |

### 3.4 The approval card — the pull-not-ask made literal

The approval card is the *only* place the system asks the human anything, and that is the entire
point: "the only human question ever asked is approval to act." Its design must make that
singularity obvious.

```
┌─ HUMAN APPROVAL REQUIRED ───────────────────── 00:20 ─┐   ← amber label, mono countdown ticking
│ Escalate this campaign?                                │   ← 19px sans, 770 — the one question
│ Recommended: approve. Three-hop history links the      │   ← the agent's own pulled reasoning
│ live edit to three accounts and four pages in 12 min.  │      (proof it did NOT ask — it pulled)
│ ┌───────────────────┐ ┌───────────────────┐            │
│ │   APPROVE ACTION   │ │      DISMISS       │            │   ← green / neutral, equal weight
│ └───────────────────┘ └───────────────────┘            │
└────────────────────────────────────────────────────────┘
   on approve → border green, "✓ APPROVED · responder may execute"
   on timeout → border stays, "× AUTO-DISMISSED · safe default after 20s"
```

- **The recommendation line is load-bearing UX**, not filler: it shows the agent arriving *with the
  context already assembled* ("three accounts, four pages, twelve minutes"). That is the pull. The
  human is not being interrogated; the human is being handed a finished brief and asked one yes/no.
- **Default-on-timeout is the single most important borrowed detail** (`unblock_reuse_manifest`,
  AskOpts). The countdown must be visually urgent (amber, tabular mono) and the timeout must resolve
  to the *safe* default (dismiss), so the pitch never freezes at second 60 if nobody taps. Render
  the timed-out state distinctly from an explicit dismiss.
- This card is the WebUI twin of the MCP **approval widget** (§5.4). They are the same interaction
  in two surfaces and must feel identical.

### 3.5 Per-state specification

The UI is a pure projection with five meaningful states. Each must be explicitly designed — the
demo passes through all of them.

| State | Trigger | Graph region | Rail | Strip | Notes |
|---|---|---|---|---|---|
| **Empty / boot** | fresh load, no data yet | faint idle field, legend visible, "awaiting signal" — **never a spinner** | feed shows "consumer group joined, offset 0" | offsets at 0, climbing begins | On stage this state is never shown (firehose pre-warmed 45 min). But it must be dignified for cold-boot rehearsal — an empty graph reads as "ready," not "broken." |
| **Live-streaming** | nodes arriving | continuous bloom, ambient force settling, count chip updating to exact `meta` counts | thinking frames scroll, handoffs land | raw offset climbs, lag ~0 | The default resting state. Motion is constant and calm. |
| **Ring-detected** | `POST /ring` → `fired:true` | path traces node-by-node along `paths[0].ids`, ends in historical cluster; ring card reveals `run_time_ms` + Cypher, opacity 0.82→1 | analyst "return" row: `ring_score` | — | **Fires only on `fired:true` (§4.4).** The animation follows server ids exactly. |
| **Cold vs warm** | `S` pressed | split canvas, red/cyan tints, turns 04 vs 01, dual verdicts | (rail unchanged) | — | The headline. COLD counter may tick up as transcript prints. |
| **Kill-and-resume** | `pkill analyst` then restart | analyst-colored (violet) nodes/edges briefly dim, then a "resumed from handover" marker re-lights; a HANDED_OFF_TO edge highlights | lane goes dead (dot stops), then a cold-boot row: "analyst resumed · offset 41,208 · honoring prior approval" | consumer lag spikes then returns to 0 | Memory meets motion, literally. The lag spike→recovery on the strip is the visible proof it resumed from a committed offset, not restarted. |

**Kill-and-resume is under-specified in the current build and must be designed.** The moment the
lane goes dead and then a row appears reading the agent's *own* handover node out of the graph is the
finale. Give it: (a) a visible "dead" state on the lane status dot (stop the breathe animation, go
faint/red); (b) a distinct "resuming" feed row styled unlike a normal handoff — it is the agent
reading *itself*; (c) the consumer-lag number on the strip visibly spiking and draining back to 0,
because that number *is* "it resumed from where it was." Do not fake the recovery — it must be
driven by the real handover read + offset re-attach.

---

## 4. Projector WebUI — behavioral contracts

### 4.1 Self-contained invariant (the `*.pages.dev` constraint)

The single `index.html` must run with **zero network dependency** when opened from Cloudflare Pages
or a `file://` URL. No CDN, no external font, no external script, no external image. It ships its own
MOCK dataset (already present: `mockGraph`, `mockRing`, `mockFeed`, `mockActions`) and renders a
complete, honest-looking demo with the bridge offline. This is the wifi-off insurance and the
"anyone can open the link" distribution path. **Never introduce an external asset.**

### 4.2 MOCK↔LIVE auto-flip + `?bridge=<origin>`

The UI has three data modes and flips between them automatically:

```
                 ┌─────────── poll every 1500ms ───────────┐
   ┌────────┐    │  GET {origin}/graph  succeeds w/ nodes    │   ┌────────┐
   │  MOCK  │────┼──────────────────────────────────────────┼──▶│  LIVE  │
   │(bundled│    │                                            │   │(bridge)│
   │ data)  │◀───┤  2 consecutive poll failures               ├───│        │
   └────────┘    │  1 failure while LIVE → STALE (hold graph)  │   └────────┘
                 └────────────────────────────────────────────┘
```

- **Boot in MOCK.** Always render something instantly; never block on the network.
- **Flip to LIVE** the first time `GET {origin}/graph` returns a graph with ≥1 node.
- **STALE** on a single failure while LIVE: keep the last real graph on screen, badge amber
  "STALE · RETRYING," do **not** silently revert to MOCK mid-demo (a flicker back to fake data on
  stage is worse than a held-stale real graph).
- **Fall back to MOCK** only after 2 consecutive failures from a cold/never-connected state.

**`?bridge=<origin>` (new — must be added).** The origin is currently hardcoded to
`http://127.0.0.1:8931`. Replace with:

```
const params = new URLSearchParams(location.search);
const BRIDGE = params.get("bridge")              // ?bridge=https://palimpsest.fly.dev
             || (location.protocol === "https:" && location.hostname.endsWith(".pages.dev")
                   ? ""                            // same-origin proxy if the Pages site fronts one
                   : "http://127.0.0.1:8931");     // local default
```

This lets the hosted `*.pages.dev` projector point at the hosted **Fly bridge**
(`?bridge=https://<app>.fly.dev`) for a live remote demo, at `127.0.0.1:8931` for the local demo,
and at nothing (MOCK) for a pure static share. The bridge must send permissive CORS for `GET /graph`,
`/stream_tail`, `POST /ring` (read-only verbs) so the cross-origin `pages.dev → fly.dev` fetch
succeeds. Badge copy should name the origin: "LIVE · fly.dev" vs "LIVE · 127.0.0.1" so the driver
knows which plane is on screen.

### 4.3 Connection badge — three honest states

| Badge | Color | Text | Meaning |
|---|---|---|---|
| MOCK | neutral | `MOCK · BRIDGE OFFLINE` | bundled data, no bridge |
| LIVE | green | `LIVE · {origin}` | real bridge, fresh |
| STALE | red | `STALE · RETRYING` | real bridge, last poll failed, graph held |

The badge is itself an honesty instrument — a judge can always see whether the screen is fake or
real. Never show LIVE while rendering MOCK data.

### 4.4 HONESTY RULES — first-class requirements (from the adversarial audit)

These are not guidelines. A violation is a demo-integrity bug that can lose the room if a judge
inspects the network tab. The bridge already returns everything needed to obey them
(`server.py`: `fired`, `paths`, `run_time_ms`, `meta.{node_count,edge_count,real_edges,derived_edges}`).

**HR-1 — Render a ring ONLY on `fired:true` with real paths.**
In LIVE, the ring animation and the ring result card fire **iff** the `/ring` response has
`fired === true`. The animated path uses **only** `paths[0].ids` from the server. The current
`normalizeRing()` fallback to `findThreeHopPath(graph)` — which *fabricates* a ring by BFS over the
graph whenever the server returns fewer than 4 ids — is **prohibited in LIVE**. On `fired:false` or a
fetch failure, show *no ring*: the ring card stays hidden/neutral ("no ring detected in window"),
the graph keeps streaming. `findThreeHopPath` may remain **only** as the MOCK-mode illustrator and
must be unreachable when `state.mode === "live"`.

**HR-2 — `run_time_ms` is the server's number, never a literal.**
The ring card renders `response.run_time_ms` (FalkorDB's own measured execution time, per
`graphstore.py:151`). The current default of `2.45` is acceptable **only** as MOCK seed data. In
LIVE, if `run_time_ms` is absent, render `—`, not `2.45`. The whole "two milliseconds" brag is only
credible if the number on screen is the one the database actually reported.

**HR-3 — Render exactly the node/edge count the server returns.**
The count chip shows `meta.node_count` / `meta.edge_count` verbatim. Do not pad, round, estimate, or
display the length of a client-side array that has been filtered/capped differently from the
server's count. When the server reports `capped:true`, surface it ("260 of 912 shown") rather than
implying the visible set is the whole graph. Consider surfacing `real_edges` vs `derived_edges` —
the typed `[:RELATES]` vocabulary vs structural edges — because "these edges are real attributed
relations, these are structural" is itself a credibility point.

**HR-4 — In LIVE, never claim a sponsor action that did not fire.**
Action rows in LIVE come **only** from real `action.executed` records carrying a real URL. The
bundled `mockActions` (Discord/GitHub with placeholder URLs) render **only** in MOCK. Any
simulated, pending, or pre-recorded action shown in LIVE must be **explicitly labeled**
`SIMULATED` / `PENDING` with distinct styling (dashed border, muted, no green FIRED chip). A green
"FIRED" chip is a claim that the side effect crossed into the real world — it is only permitted
against a real record. The GitHub issue URL a judge opens on their phone must be the one the
responder actually created.

**HR-5 — MOCK is honest about being MOCK.** The badge says so. MOCK exists to make the static share
and the wifi-off fallback *look complete*, not to deceive — and because the badge always tells the
truth, MOCK data is a legitimate illustration, not a fake. The line is bright: fake data is fine
*iff* the screen says the data is fake.

These five rules should live as a comment block at the top of the `index.html` script and be the
first thing the UI codex reads.

---

## 5. SURFACE 2 — The MCP UX with interactive widgets

### 5.1 The measured reality that governs every decision here

From `plan/research/mcp-widgets-guide.md`, **measured on this box, not researched**:

| Host | Renders MCP-Apps widgets | Elicitation |
|---|---|---|
| **Claude Code (CLI)** — likely on-stage host | **NO** (verified, 2.1.220) | **YES** |
| **Claude Desktop / claude.ai** | **YES** | NO |

**Consequence, and it is the whole strategy:** if judges watch a terminal, widgets are invisible and
**elicitation** is the interactive surface; if judges watch Claude Desktop, widgets render and
elicitation silently fails. Therefore **every widget verb ships a plain-text + elicitation fallback
on the same tool**, and the projector — not any widget — stays the primary demo surface. The guide's
**Tier-0-then-stop** ceiling is a hard constraint: widget work must never threaten the four
load-bearing sponsor gates.

### 5.2 What PALIMPSEST feels like through an MCP host

The canonical UX ("already knows, pulls not asks") expresses through MCP as: the host operator types
one natural request, and the tool comes back **with the answer and the evidence already assembled** —
not with a clarifying question. Three verbs carry widgets, chosen by the guide's fit analysis:

| Verb | `ui://` resource | Widget | Text/elicitation fallback | Widget→server round-trip |
|---|---|---|---|---|
| `graph` | `ui://palimpsest/graph-v1` | inline-SVG graph, polls every 1.5s | text: node/edge counts + top-k rows | `graph_delta` (`visibility:["app"]`) |
| `ask` | `ui://palimpsest/approval-v1` | approve / dismiss card, 20s default | `ctx.elicit()`, else text | `approve_action` (`visibility:["app"]`) |
| `stream_tail` | `ui://palimpsest/replay-v1` | replay/rewind control (offset slider, play/pause) | text tail + `ctx.report_progress` | `stream_seek` (`visibility:["app"]`) |

The `ask` widget is the one that *out-earns the projector* (§5.7). The `graph` and `replay` widgets
are MCP-surface receipts, not demo beats — they exist to prove the surface is real, not to be watched
on stage.

### 5.3 The `_meta.ui` declaration shape

Every widget verb declares its UI in two places the host reads, and the tool **always** returns a
real `content` text block so non-rendering hosts degrade cleanly.

```
tools/list  → { name:"ask", …,
                _meta:{ ui:{ resourceUri:"ui://palimpsest/approval-v1",
                             visibility:["model","app"] },
                        "ui/resourceUri":"ui://palimpsest/approval-v1" } }   # gotcha #4: BOTH forms
resources/read → { contents:[{ uri, mimeType:"text/html;profile=mcp-app",
                               text:"<!doctype html>…",
                               _meta:{ ui:{ domain, prefersBorder:true,
                                            csp:{ connectDomains:[…] } } } }] }
tools/call  → { content:[{type:"text", text:"…"}],       # ALWAYS — the fallback
                structuredContent:{…} }                   # data for the widget
```

`content[]` feeds the model + text-only hosts. `structuredContent` feeds the widget (not the model
context). `_meta` feeds neither. This maps onto the bridge's existing 4-tuple dispatch table as an
**optional 5th field** (`UiSpec`) so REST/OpenAPI/CLI see zero diff — the entire delta is one
emitter reading `.ui` (guide §3.1).

### 5.4 The approval widget (`approval.html`) — design

This is the WebUI approval card (§3.4) rebuilt for an alien host. Same interaction, host-native
skin:

```
┌─ PALIMPSEST wants to act ─────────────────────┐
│  Escalate coordinated campaign P-2048?          │
│  3 accounts · 4 pages · 12 min · score 0.93     │   ← the pulled brief, again
│  ┌──────────────┐  ┌──────────────┐             │
│  │   Approve     │  │   Dismiss     │  ⏱ 20s     │
│  └──────────────┘  └──────────────┘             │
└─────────────────────────────────────────────────┘
```

- **Vanilla JS, dependency-free, no CDN** (loading `@modelcontextprotocol/ext-apps` from esm.sh is
  a known renderer-killer — guide §2.4). The handshake is the ~40-line postMessage block in the
  guide; use it verbatim.
- **Approve** → `send("tools/call", {name:"approve_action", arguments:{action_id, approved:true}})`.
  The button calls a **real bridge verb**; the Discord/GitHub side effect fires server-side. This is
  why the widget out-earns the projector — the approval crosses a boundary the team does not own.
- **20s auto-default** mirrors the projector. On timeout the widget resolves to dismiss and says so.
- **Theme-aware** via host CSS variables (§5.6). It must look native in Claude Desktop light *and*
  dark.

### 5.5 The graph + replay widgets — poll through the host, never fetch localhost

Both read live data by calling a hidden (`visibility:["app"]`) verb through the host via
`tools/call` — **never** by fetching `http://localhost` from the iframe (mixed-content + CSP coin
flip; `text/uri-list`/`externalUrl` is deferred from the MVP — guide §2.5).

- **`graph` widget:** inline-SVG force layout, `setInterval` calling `graph_delta` every 1.5s,
  painting the delta. Same node-type color legend as the projector (§2.1) so the two surfaces are
  visibly one product. Fallback text: exact node/edge counts + top-k Cypher rows.
- **`stream_tail` / replay widget:** an **offset slider + play/pause**. The slider *is* the
  LaserData rewind control — dragging it to 0 and pressing play replays the log from offset zero,
  the same mechanic as the projector's COLD build. Calls `stream_seek`. Fallback:
  `ctx.report_progress(n, total, msg)`, which Claude Code renders as live terminal progress because
  it sends a `progressToken` on every call (measured, guide §2.6).

### 5.6 The gotchas that decide render-or-nothing

From the guide's five measured claude.ai gotchas — bake all into the emitter and widget template:

1. **`_meta.ui.domain` is an undocumented render gate.**
   `sha256("<MCP endpoint URL incl. /mcp, no trailing slash>").hexdigest()[:32] + ".claudemcpcontent.com"`.
   Deterministic, self-computed, **not a credential**. Omit it → claude.ai tells the model a widget
   rendered but never places the iframe. Compute it in the emitter (`widget_domain(endpoint)`).
2. **Send `ui/notifications/initialized` unconditionally** (on any result-bearing reply + a timeout
   fallback), or the iframe stays reserved-but-hidden.
3. **`size-changed` params must be real numbers** — a null width throws an uncaught host error that
   breaks the whole tool call.
4. **Declare the resource URI twice** — nested `_meta.ui.resourceUri` and flat `_meta["ui/resourceUri"]`.
5. **`mimeType` must be `text/html;profile=mcp-app`** on both `resources/list` and `resources/read`.

Two protocol versions coexist and must not be crossed: `2025-11-25` on the wire (server↔host),
`2026-01-26` inside the iframe (view↔host).

### 5.7 Demo-value call — where a widget out-earns the projector, and where it does not

**One widget out-earns the projector: the approval card, and only in Claude Desktop.** A judge
tapping "Approve" *inside the host they already trust* and watching the real Discord post + GitHub
issue fire is a categorically stronger proof than a tap in a web app the team wrote — it proves the
human gate crossed a boundary the team does not control. That is worth exactly one widget.

**The projector stays primary for everything else.** The graph and replay widgets are strictly worse
than the projector for on-stage legibility (a phone-sized iframe cannot be read from twenty feet) —
build them as MCP-surface *receipts* that prove compliance, never as demo beats. And the on-stage
host is likely Claude Code (CLI), which renders **no** widgets — so **rehearse the elicitation
fallback as the primary path** and treat a rendered widget as a bonus, not a dependency.

### 5.8 Phased build recommendation (honor the Tier-0 ceiling)

| Tier | Scope | Effort | When | Risk |
|---|---|---|---|---|
| **Tier 0 — NOW** | Add `UiSpec` 5th field; attach `_meta.ui` to `graph`/`ask`/`stream_tail`; ship 3 static widget HTML files with the handshake block; wire the `--app-info` inspector probe into `plan/gates/` as a saved receipt | **~30 min** | before the sprint's discretionary time | near-zero — text path unchanged, cannot break gates |
| **Tier 1 — LATER** | Make the `ask` approval card actually *render + round-trip* in Claude Desktop (real `approve_action` firing Discord/GitHub); wire `graph_delta`/`stream_seek` | **~60–90 min first widget, ~20 min each after** | **only if all four sponsor gates are green at T+5:00** | real — 5 undocumented reqs, a host that renders nothing on a clean exchange, a 275MB Playwright download for the render check |

**Tier 0 buys a falsifiable "our MCP surface is MCP-Apps compliant" claim with a saved gate
artifact, costs nothing in demo time, and cannot regress the text path. Then STOP.** Widget work is a
poor bet against the team's own cut order — `GOAL.md` already ranks judge-taps-approve as the
*second* thing to cut, so the widget's only load-bearing beat is pre-agreed expendable. **Never let
widget work touch the rewind A/B.**

---

## 6. Both surfaces, one thesis

Every element on both surfaces is a rendering of the same four-part promise. The mapping:

| Canonical promise | Projector rendering | MCP rendering |
|---|---|---|
| **Knows who you are** | actor nodes carry full attributed history; ring traces to an actor's past | `graph` widget / text shows the actor's prior edits pulled in one traversal |
| **Knows who it is** | per-agent node color; the kill-and-resume "reads its own handover" moment | the tool returns already-contextualized results; `ask` names the case it opened |
| **Constant datastream** | offsets climb continuously on the strip; nodes bloom in | `stream_tail` widget / `report_progress` shows the live tail |
| **Pulls, not asks** | WARM = `0 questions · 1 turn` vs COLD = `4 turns`; approval card arrives with a finished brief | `ask` returns the brief pre-assembled; the human answers one yes/no, never a clarifying question |

The turns-to-answer counter and the approval card's recommendation line are the two places this is
*most* visible — protect them above all.

---

## 7. Implementation handoff — for the UI codex

### 7.1 Build first (in order)

1. **The honesty corrections (§4.4) in `index.html`.** Gate the ring on `fired===true`; use
   `paths[0].ids` only; render server `run_time_ms` (no `2.45` literal in LIVE); render exact
   `meta.node_count/edge_count`; confine `mockActions` and `findThreeHopPath` to MOCK mode. These are
   correctness bugs in a working file — smallest change, highest integrity payoff, do them first.
2. **`?bridge=<origin>` + CORS-aware polling (§4.2).** Unblocks the hosted `pages.dev → fly.dev`
   live demo and the pure-static share from one file. Add origin-named badge copy.
3. **The kill-and-resume state (§3.5).** It is the finale and it is currently under-built: dead-lane
   state, the distinct "resumed from handover" feed row, and the consumer-lag spike→drain on the
   strip driven by the *real* offset re-attach.

Then, only if sprint time and green gates permit: **Tier 0 MCP widgets (§5.8)** — the 30-minute,
zero-risk compliance floor.

### 7.2 Explicitly out of scope

- Any external asset (font, CDN, script, image) in `index.html` — violates the wifi-off invariant.
- A light theme for the projector — it is committed dark by design (§2.4).
- Tier 1 MCP widget rendering *before* the four sponsor gates are green — it is a bet against the
  cut order (§5.8).
- Redesigning the graph wire format, the node-type color legend, or the eyebrow numbering — these are
  ratified contracts, not open questions.
- Fetching `http://localhost` from inside any MCP widget iframe — poll through the host instead
  (§5.5).
- Any trust/attestation badge — the team ships none it cannot demonstrate on request
  (`unblock_reuse_manifest` fence; judge_qa "what would you do with another day").

### 7.3 The one-line test for every change

*Would this survive a judge opening the browser network tab, or `guild session events`, or the
GitHub issue on their own phone?* If a pixel implies something the server did not actually return,
it is a bug, not a polish item. The whole design wins by being **checkable** — build it so it
withstands the check.
