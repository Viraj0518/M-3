# GATE-LASERDATA-TAIL — real live tail + wall-clock time-scrub

**Status: GREEN** · 2026-08-03 · live against the laser-stack iggy at `127.0.0.1:8090`, stream `live`, topic `signal.raw` (4 partitions, ~796 durable records — a captured firehose burst spanning **50.4 s**).

Transport: `laser-sdk`, **Log primitive only**, conn string `iggy:laser@127.0.0.1:8090` (never `iggy:iggy@…`). Files changed: `app/bridge/stream.py`, `realtime/laser_io.py`, `realtime/graph_writer.py` — nothing else. Every number below is copied from a live run (`.venv` python3.12), no fabrication.

## SDK ground-truth (verified on THIS wheel)

```
Topic.consumer(self, name, *, partition=0, batch_length=1000, polling="next",
               offset=None, timestamp_micros=None, auto_commit="polling",
               ..., allow_replay=False)          # exposes offset + timestamp_micros + allow_replay
Topic.replay(self, *, batch=None, from_offsets=None)   # per-partition offset DICT, NO timestamp cursor
Producer.send(self, payload, *, headers=None, key=None, partition=None)   # headers accepted
```
Wall-clock seek therefore lives **only** on `Topic.consumer(polling="timestamp")`, exactly as the playbook states.

**Discovery (informs the fix):** `Topic.replay()` yields a bare `Message` exposing only `headers / json / message_id / payload` — **no `offset`, `partition_id`, or `timestamp_micros`**. `Topic.consumer` yields a `ConsumerMessage` that DOES carry all of those. So the old replay-backed tail was doubly broken (below).

---

## (A) MANDATORY CORRECTNESS FIX — the tail now shows the LIVE EDGE

**The bug:** `tail()` called `log.drain_from_zero(topic, max_records=10_000)` (a replay cursor from **offset 0**) then returned `records[-limit:]` — the last `limit` of the FIRST 10k records. On a 50–100 ev/s firehose it freezes at ~offset 9,975 within 2–3 min and never advances, while the MCP tool doc promises "what is happening RIGHT NOW."

**The fix:** a live `polling="last"` consumer per partition across all 4, take the newest `limit` of the union ordered by the log's append clock. `auto_commit="disabled"` → repeated polls are idempotent (verified: two opens return the identical window `[482,483,484]`). The drain-from-0 scan path is deleted.

### Before / after (12 records `n=0..11` published to one partition on a fresh topic; cap=5 stands in for the shipped cap=10 000, edge `n=11` for the firehose's growing edge — identical mechanism)

```
BEFORE  drain_from_zero(max_records=5)[-3:]   n=[2, 3, 4]     offsets=[None, None, None]
        ^ STALE (first-5-from-0, never the edge) AND offset/partition=None
          (the replay cursor's bare Message carries no log coordinate)
AFTER   stream.tail(limit=3)                  n=[9, 10, 11]   offsets=[9, 10, 11]
          source=consumer-last   live_edge={'3': 11}
```
`[2,3,4] → [9,10,11]` and `None → real offsets`: the new tail shows the live edge WITH real coordinates; the old one froze below it and reported no offset at all.

### Fresh-record proof (publish → tail must surface it as the edge)

```
publish {"n":12,"marker":"fresh-bcc95a"}  ->  tail(limit=3) newest:
    offset=12  n=12  marker=fresh-bcc95a  ts=1785796294858729     PASS
```

### New tail on LIVE `signal.raw` (limit=5) — real offsets, real per-record lag, real CRC

```
count=5  offset(max)=484  live_edge(high-water)={'0':484,'1':124,'2':44,'3':144}  source=consumer-last
  p0 off=483 ts=1785782301706101 checksum=4939419735628211999 lag_us=88808
  p2 off=43  ts=1785782301758107 checksum=1194582628234741883 lag_us=88882
  p3 off=144 ts=1785782301811943 checksum=3295439981195937819 lag_us=90653
  p0 off=484 ts=1785782301865109 checksum=8460263909527896884 lag_us=89964   <- p0 live edge (current_offset=484)
  p2 off=44  ts=1785782301918574 checksum=7215519946998850317 lag_us=90507
```
The returned edge offsets equal the partitions' `current_offset` high-water — this is the present, not a scanned window.

---

## (B) HEADLINE — wall-clock time-scrub (`stream_replay(from_timestamp_micros=T)`)

`laser_io` now threads `offset` / `timestamp_micros` / `allow_replay` through `consumer()` / `partition_consumer()` / `consumer_group()` into `Topic.consumer(...)`. `stream_replay` gained a `polling="timestamp"` server-seek (and a `polling="offset"` server-seek replacing the old fetch-everything-then-filter-in-Python).

Because this captured corpus spans only **50.4 s** (min_ts=1785782251516293, max_ts=1785782301918574), "T−12 min" lands past all data; the scrub seeks to **T = live-edge − 30 s**, mid-stream.

```
data live-edge ts = 1785782301918574   ->   SCRUB T = 1785782271918574   (edge − 30.0s)
stream_replay(signal.raw, from_timestamp_micros=T, limit=6)  ->  source=consumer-timestamp  count=24
  p0 off=233 ts=1785782272019059 (T+0.10s)  OK>=T
  p0 off=234 ts=1785782272076601 (T+0.16s)  OK>=T
  p0 off=235 ts=1785782272216679 (T+0.30s)  OK>=T
  p0 off=236 ts=1785782272327029 (T+0.41s)  OK>=T
  p0 off=237 ts=1785782272430725 (T+0.51s)  OK>=T
ASSERT: every one of the 24 returned records has timestamp_micros >= T   PASS
```
Step-forward (rewind one record later): `T2 = T_first + 1µs` → first offset becomes **234** (was **233** at T). The window advances by exactly one record — the "step forward and the verdict flips" motion is real.

```
Topic.replay() has NO timestamp seek — proven above; wall-clock ONLY works via Topic.consumer(polling="timestamp").
```

### (B2) offset server-seek (replaces fetch-all-then-filter)

```
stream_replay(signal.raw, from_offset=200, limit=4)  ->  source=consumer-offset
  first (partition,offset) = [(0,200),(0,201),(0,202),(0,203)]     every offset >= 200   PASS
```

---

## (C) `normalize_record()` enrichment — the lag strip + CRC parity are now REAL

Added `timestamp_micros`, `origin_timestamp_micros`, `checksum`, `headers`, `header_kinds` (all `getattr` with `None` default → safe on the invariant-test fakes). Verified present on every live `ConsumerMessage`:

```
per-record lag = timestamp_micros - origin_timestamp_micros ≈ 88–91 ms (real, see table in (A))
checksum       = the record CRC (per-record, see table) — the ablation can now assert BYTE parity, not just topology
```

## (D) headers passthrough (self-contained parts) — attribution rides the log record

`laser_io.publish` / `stream.publish` accept `headers=` and forward to `Producer.send(headers=...)` only when present (the key-only path — and its contract fake — is byte-for-byte unchanged). `graph_writer.write(headers=)` prefers a header-supplied `author_agent`, else its default.

```
publish(topic, {...}, headers={"author_agent":"editor-x","case_id":"c-42"})  ->  {ok:true, attributed:true, published:1}
read back off the log:  rec["headers"] == {"author_agent":"editor-x","case_id":"c-42"}          PASS (round-trip)
GraphWriter.write(SAMPLE, headers={"author_agent":"editor-x"}):
    actor -> author_agent  ==  {"DefaultUser":"watcher", "HdrUser":"editor-x"}                  PASS (backward-compatible)
```

**Follow-up (files owned by other agents, NOT touched here):** `server.py::_h_stream_publish` passing `ctx.agent` as a header, `pipeline.py` passing `rec["headers"]` into `writer.write`, and `server.py::_h_stream_replay` exposing `from_timestamp_micros` to the MCP/REST surface. Until those land, the wall-clock scrub and the header-sourced author are exercised by direct calls (proven above), not by the bridge HTTP surface. This is the same boundary as the **UI scrubber wiring in `app/web/index.html`, a codex follow-up**.

---

## Tests (LIVE iggy + FalkorDB, `.venv` python3.12)

```
python3 -c "import ast; ast.parse(open('app/bridge/stream.py').read()); ast.parse(open('realtime/laser_io.py').read()); ast.parse(open('realtime/graph_writer.py').read())"   # OK

python -m realtime.laser_smoke                 # ok:true  replay_matches:1  (Log round-trip GREEN)

python -m pytest realtime/tests/ -q            # 20 passed  (18 + test_replay_parity 2/2 when the 42 MB
                                               #   demo/seed_replay.ndjson fixture is present; it is not
                                               #   materialised in the isolated worktree — env, not a regression:
                                               #   the diff touches none of producer.py / replay.py / pipeline.py)
python -m pytest app/bridge/tests/ -q          # 137 passed  (stream verbs / rest / surfaces / ring / projection)
```

`test_consumer_invariants.py`, `test_laser_io_contract.py`, `test_ablation.py`, `test_graph_writer.py`, `test_digest_attribution.py` all green — the new kwargs are default-`None` passthroughs, so the fakes (which expose none of the new fields) are unaffected.

## What's live vs. follow-up after this lane

- **LIVE (proven above):** `stream.tail` = live-edge consumer tail; `stream.stream_replay` = wall-clock + offset server-seek + full rewind; `normalize_record` richer coordinate; `publish(headers=)`; `graph_writer` header-sourced author.
- **Follow-up (other-agent files):** MCP/REST wiring of `from_timestamp_micros` (`server.py`), header population (`server.py` + `pipeline.py`), and the UI time-scrubber (`app/web/index.html`, codex).

## Reproduce

```
export LASER_CONNECTION_STRING='iggy:laser@127.0.0.1:8090'
export PYTHONPATH="$PWD"
.venv/bin/python -m realtime.laser_smoke
.venv/bin/python -m pytest realtime/tests/test_ablation.py realtime/tests/test_consumer_invariants.py app/bridge/tests/ -q
```
`stream.tail` / `stream.stream_replay(from_timestamp_micros=…, from_offset=…)` return the live edge / the seek window against whatever is currently on `signal.raw`.
