# GATE-ROCKET — parallel NOW + EVER Wave

Status: **STRUCTURE GREEN; LIVE TRACE PENDING BYO `ANTHROPIC_API_KEY`**

## Artifact under test

- Pipeline: `motion/palimpsest.pipe`
- Driver: `motion/client.py`
- RocketRide engine: local v3.3.1, SDK v1.3.0
- Graph path: first-party `tool_falkordb` against `127.0.0.1:6401/palimpsest`
- NOW path: `tool_http_request` GET of the live bridge health endpoint

## Structural receipt

On 2026-08-03 the pipeline was built and validated node-by-node against the
running engine. Each cumulative graph returned a validation chain, through the
complete eight-node pipeline:

1. `webhook_1`
2. `question_1`
3. `commander_1` (`agent_rocketride`)
4. `llm_anthropic_1` (exactly one LLM attachment)
5. `memory_internal_1` (exactly one memory attachment)
6. `tool_falkordb_1`
7. `tool_http_request_1`
8. `response_answers_1`

The final validator chain contained all eight nodes. The pipeline fixes
`ttl=0`, requests `pipelineTraceLevel=full`, and subscribes to
`task/summary/flow/output/sse` before sending the decision prompt.

## Live acceptance command

```bash
ANTHROPIC_API_KEY=... python3.12 motion/client.py \
  --trace motion/traces/rocketride-wave-trace.json \
  "Use NOW and EVER to decide whether the current case should escalate."
```

Raw traces are ignored under `motion/traces/`; inspect and sanitize one before
publishing a receipt. This gate becomes GREEN only when that reviewed receipt proves one Wave
contained both `http.http_request` and `falkordb.query` and the final decision
cites both results. Structural validation alone is deliberately not called a
live integration pass.
