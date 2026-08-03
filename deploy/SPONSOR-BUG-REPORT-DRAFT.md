# DRAFT — not posted. A human posts this to discord.gg/QXVbqWxHHb.

**Title:** `laserdatainc/iggy-server` fork panics at boot on kernels < ~6.11 (upstream `apache/iggy` unaffected)

## Summary

The LaserData iggy fork fails to start on any host kernel below ~6.11, on both amd64 and
arm64, under every security configuration we could control for. Upstream `apache/iggy` boots
cleanly on the same hosts, so this is specific to the fork's io_uring usage. `laser-plane`
cannot fall back to upstream because it speaks the fork's VSR-framed TCP protocol, so the
whole local stack is blocked on affected machines.

## Failure

```
thread 'shard-0' panicked at compio-driver-0.12.4/src/asyncify.rs:118:25:
the thread pool is needed but no worker thread is running
ERROR main server_ng::bootstrap: shard thread panicked shard_id=0
Error: ShardJoinFailures { failures: [ShardJoinFailure { shard_id: 0,
        kind: Panic { message: "the thread pool is needed but no worker thread is running" } }] }
```

The server's own diagnostic reports that io_uring **setup succeeds** and the failure occurs at
**runtime**, when an issued operation is not supported and is not silently offloaded to a worker
thread.

## Kernel matrix (the load-bearing evidence)

| Host | Kernel | Arch | Result |
|---|---|---|---|
| Docker Desktop / WSL2 (Windows 11) | 6.6.87 | amd64 | **FAIL** |
| colima, stock Ubuntu 24.04 (macOS) | 6.8.0-117 | arm64 | **FAIL** |
| lima, Ubuntu 25.04 (macOS) | 6.14 | arm64 | **PASS** — shards start, all listeners up, stable |

`IORING_OP_BIND` / `IORING_OP_LISTEN` landed in Linux 6.11, which fits the boundary exactly.

## Ruled out (please don't re-suggest these — all controlled for)

- **Not seccomp** — `compose.yaml` already ships `security_opt: [seccomp:unconfined]`; also
  reproduced with `--security-opt seccomp=unconfined` passed explicitly, and with
  `apparmor:unconfined`, both confirmed applied via `docker inspect`.
- **Not the sysctl** — `kernel.io_uring_disabled = 0` on the failing hosts.
- **Not permissions** — reproduced as `--user root`.
- **Not the image tag** — `:latest` (pushed 2026-08-03 05:45Z) and `:edge` (2026-07-31) fail
  identically, so this is not a same-day regression.
- **Not the architecture** — fails on amd64 and arm64 alike.
- **Not WSL2** — it reproduces on a stock Ubuntu 24.04 VM under colima with no WSL involved.
  (The `Environment: WSL2 (Microsoft kernel fork detected)` banner on one host is incidental.)

## Minimal repro

```bash
docker run --rm --user root --security-opt seccomp=unconfined \
  -e IGGY_ROOT_USERNAME=iggy -e IGGY_ROOT_PASSWORD=<any> \
  docker.io/laserdatainc/iggy-server:latest
# panics as above on kernel < ~6.11; boots on 6.14
```

## Why upstream isn't a workaround

`apache/iggy` boots clean on all three hosts, but pointing `laser-plane` at it yields:

```
Timed out waiting for VSR response header, code 12
```

so `plane` requires the fork.

## Ask

1. Confirm the minimum supported kernel, and **state it in the laser-stack README** — today
   `./scripts/up` just reports `iggy did not become healthy within 120s`, which sends people
   debugging compose DNS (`plane` logs `Failed to connect to iggy:8090 ... Name or service not
   known`) rather than the kernel.
2. If ≥6.11 is required, consider a runtime preflight that fails fast with that message.
3. If the opcodes are optional, a fallback path would restore support for Docker Desktop/WSL2
   and stock Ubuntu 24.04 — between them a large share of hackathon laptops.

## Environment

Windows 11 + Docker Desktop 28.5.1 (WSL2 6.6.87, amd64) · macOS + colima (Ubuntu 24.04,
6.8.0-117, arm64) · macOS + lima (Ubuntu 25.04, 6.14, arm64). Images: `iggy-server:latest`
@2026-08-03 05:45Z and `:edge` @2026-07-31; `laser-plane:latest`.
