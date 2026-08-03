# LaserData streaming

M-3 uses LaserData as the durable **NOW** event log. FalkorDB remains the
structured memory and GraphRAG layer. The LaserData path is intentionally limited
to `ensure`, `publish`, consumer-group reads, and replay-from-offset.

## Official references

- [Laser SDK](https://github.com/laserdata/laser-sdk)
- [Python SDK reference](https://github.com/laserdata/laser-sdk/blob/main/foreign/python/README.md)
- [Laser SDK quickstart](https://docs.laserdata.com/laser-sdk/quickstart)
- [Laser Stack](https://github.com/laserdata/laser-stack)
- [LaserData Cloud](https://laserdata.cloud)
- [Photon Market reference application](https://github.com/laserdata/laser-example-photon-market)

The SDK's local and hosted connection contract is `LASER_CONNECTION_STRING`.
For local development, use the exact value printed by Laser Stack's
`./scripts/up`; do not invent a username or password in application code.

## Local Laser Stack

```bash
git clone https://github.com/laserdata/laser-stack.git
cd laser-stack
./scripts/up
export LASER_CONNECTION_STRING='copy-the-exact-value-printed-by-scripts-up'
```

In the M-3 checkout, install the pinned Python SDK and run the no-credential
round-trip smoke test:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r app/bridge/requirements.txt
uv pip install --python .venv/bin/python -r realtime/requirements.txt
.venv/bin/python -m realtime.laser_smoke
```

The smoke test publishes one JSON event to `smoke.events`, replays the topic,
and prints its stream coordinate. It does not write FalkorDB.

## M-3 event flow

```text
live/file source
      ↓
realtime.producer
      ↓  Laser SDK: producer.init → send(payload, key)
LaserData stream `live`
      ↓
realtime.consumer / replay
      ↓  commit only after side effects
FalkorDB graph writer
```

The application topics are defined once in `memory/config.py`:

- `signal.raw`
- `signal.salient`
- `case.opened`
- `case.decision`
- `action.executed`
- `agent.handoff`

The producer keys records by wiki/source identity so related records preserve
their ordering while partitions can still be consumed in parallel. The consumer
uses at-least-once delivery, deduplicates by `(partition, offset)`, and commits
only after the graph side effect succeeds. Replays always start from offset zero
when rebuilding a cold graph.

## LaserData Cloud

For Cloud, only the environment changes. The Python SDK requires the service
port in this deployment's connection string:

```bash
export LASER_CONNECTION_STRING='token-or-user-password@your-laserdata-host:8090'
.venv/bin/python -m realtime.laser_smoke --topic smoke.events
```

The Python SDK handles the LaserData host's TLS behavior. The code does not use
LaserData managed graph, memory, query, or KV surfaces; that separation keeps
FalkorDB load-bearing for M-3.
