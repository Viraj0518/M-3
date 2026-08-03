# GATE 0 receipt — LaserData local stack RUNNING on the demo Mac

**Status: GREEN** · 2026-08-03 ~12:35Z · both containers `healthy`, `./scripts/smoke` full pass.

## Where it runs

lima VM `laser25` (Ubuntu 25.04, **kernel 6.14.0-37-generic**, VZ backend, rootless docker 29.2.1),
created user-space — no Homebrew, no admin. Ports auto-forwarded to the Mac host:

- iggy TCP → `127.0.0.1:8090` (protocol-probed from host: CONNECT ok)
- iggy HTTP → `127.0.0.1:3000` (CONNECT ok)
- Connection string (exactly as `./scripts/up` printed): `iggy:laser@127.0.0.1:8090`
  — note `iggy:laser@`, NOT the SDK examples' `iggy:iggy@` (pre-registered trap, confirmed real).

Smoke transcript (excerpt): `iggy is healthy. / plane is healthy. / Laser Stack is ready.` →
`Iggy TCP check passed. / Plane readiness check passed. / AGDX hello passed / managed KV set/get passed / Laser Stack smoke test passed.`

## The root cause we proved on the way (sponsor bug, kernel matrix)

`laserdatainc/iggy-server` (the `-ld` fork laser-plane REQUIRES — plane speaks the fork's
VSR-framed TCP; upstream `apache/iggy` accepts TCP but times out `code: 12`) panics at shard
start on kernels missing newer io_uring opcodes (suspect class: `IORING_OP_BIND/LISTEN`, kernel ≥6.11):

| Kernel | Env | seccomp/apparmor | Result |
|---|---|---|---|
| 6.6.87 (WSL2, Windows box) | Docker Desktop | unconfined (both tested) | ❌ `ShardJoinFailures` panic |
| 6.8.0-117 (Ubuntu 24.04, colima VZ) | arm64 native | unconfined (both, verified via inspect) | ❌ identical panic |
| 6.8.0-117 — upstream `apache/iggy` control | same | seccomp unconfined | ✅ boots (proves not-kernel-generic) |
| **6.14.0-37 (Ubuntu 25.04, lima VZ)** | arm64 native | seccomp unconfined (compose default) | ✅ **boots, full smoke pass** |

Also controlled for: `latest` vs `edge` tags (identical), plane on/off (identical), root vs
uid-10001 (identical), `kernel.io_uring_disabled` (0). Bare `--security-opt seccomp=unconfined`
is REQUIRED in all cases (fork uses io_uring syscalls Docker's default seccomp blocks; the
stack's compose already sets it — plus our `docker-compose.override.yml` adds `apparmor:unconfined`).

Sponsor escalation: bug-report draft with this matrix lives with the Windows agent; a HUMAN
posts it to the LaserData Discord (https://discord.gg/QXVbqWxHHb).

## Reproduce from zero (Mac, no admin)

```bash
# 1. user-space toolchain (lima+colima+docker cli+compose) — see scratchpad/install_docker_userspace.sh pattern
# 2. kernel-6.14 VM:
limactl create --name=laser25 --vm-type=vz --cpus=2 --memory=4 --disk=20 --tty=false \
  --set '.images = [{"location":"https://cloud-images.ubuntu.com/releases/plucky/release/ubuntu-25.04-server-cloudimg-arm64.img","arch":"aarch64"}]' \
  template://docker
limactl start laser25
# (if docker.socket fails 216/GROUP: groupadd -f docker + usermod -aG docker, restart)
# 3. stack:
limactl shell laser25 -- bash -c 'git clone --depth 1 https://github.com/laserdata/laser-stack ~/laser-stack && cd ~/laser-stack && printf "services:\n  iggy:\n    security_opt:\n      - apparmor:unconfined\n" > docker-compose.override.yml && ./scripts/up && ./scripts/smoke'
```

Removal test (LaserData): `limactl shell laser25 -- bash -c 'cd ~/laser-stack && docker compose stop'`
→ all inter-service exchange stops; restart + replay from offset 0 re-derives state. (Replay
half exercised by the eval/demo replay path — see plan/synthesis.json fallbacks.laserdata.)
