# Gateway Serving Plane — architecture, volume layout, runbook

**Shipped 2026-06-12 (PROGRAM §86).** Root-cause hardening of the gateway
against the 2026-06 wedge class: the asyncio event loop blocking on
synchronous bind-mount I/O under background load → `/health` dark →
host-watchdog restart → post-restart job herd → re-wedge (3 wedge-restarts
on 2026-06-12 alone before this shipped).

## The architecture in one paragraph

Source-of-truth layers (Postgres, hash-chained per-KB source ledgers under
`workspace/`) are durable, host-visible, warm-spare-replicated. ChromaDB is a
**derived, rebuildable index** (§56) — so its physical files need no host
visibility and now live on a Docker **named volume** (`chroma_data`, mounted
at `/chroma`), on the Docker VM's native filesystem, immune to the macOS
bind-mount fsync amplification that gridlocked the gateway. The serving plane
(event loop) never performs blocking disk I/O — logging is queue-decoupled,
inbound persistence runs off-loop — and a permanent **loop sentinel** dumps
every thread's stack the moment the loop ever stalls again. The idle
scheduler self-regulates: cadence gates persist across restarts (no catch-up
herds) and back-pressure defers MEDIUM/HEAVY work whenever disk, memory, or
**the event loop itself** degrades.

## What lives where

| Data | Location | Why |
|---|---|---|
| chroma.sqlite3 + HNSW segment dirs (7 KBs) | named volume `chroma_data` → `/chroma/<kb>/` | derived index; fast native FS; no host visibility needed |
| `.source_ledger.jsonl`, `.source_ledger_history/` | `workspace/<kb>/` (bind mount) | the durable truth; host-visible, warm-spare-replicated |
| `.sqlite_snapshots/` (daily), `.rebuild_backups/` | `workspace/<kb>/` (bind mount) | recovery artifacts must survive volume loss |
| texts/entries/patterns (KB source files) | `workspace/<kb>/` | source artifacts, not derived |
| Everything else (audit.log, healing state, …) | `workspace/` | unchanged |

Path resolution: `app/paths.py` — `CHROMA_DATA_ROOT` env (default =
`WORKSPACE_ROOT`, i.e. split inactive), `chroma_kb_dir(kb)`, `chroma_root()`,
`chroma_split_active()` (env-keyed). Every consumer routes through these:
chromadb_manager, the 7 per-KB configs (per-KB env overrides still win),
chromadb_integrity (discovery/quarantine/snapshots), chromadb_hygiene,
db_backup, dr/export_kbs, chromadb_rebuild. Drill scratch dirs deliberately
do NOT redirect.

**Single-writer discipline (§55)**: the worker service gets NEITHER the
volume mount NOR the env (pinned by `tests/test_chroma_data_root.py`);
`_guard_worker()` fail-closes in code. Never run a second
`PersistentClient` against live KBs from `docker exec` either.

## Operational guardrails

- **Never `docker compose down -v`** — it destroys `chroma_data`. If it
  happens anyway: the boot guard detects "empty chroma root + non-empty
  ledgers", Signal-alerts loudly, and the source-ledger daemon's next pass
  (~5 min post-boot) replays every KB from its ledger (re-embedding takes
  minutes–hours of Ollama time; nothing is lost).
- **Volume disk**: lives on the Docker VM disk. Visibility: substrate
  snapshot `chroma_disk_free_gb`, disk_quota monitor second probe (alert tag
  `disk_quota_chroma`), and heavy idle work defers below the same floors.
  Grow via Docker Desktop → Resources if it runs low (`docker system df`).
- **Loop stalls**: any >5s event-loop stall writes
  `workspace/healing/loop_stalls/<ts>.txt` (all thread stacks — the asyncio
  thread's frame names the blocking call), Signal-alerts via the
  `loop_stall` monitor, and auto-defers MEDIUM/HEAVY idle work for 10 min
  (`substrate/policy.py` `event_loop_degraded`). A dump is a bug report:
  fix the named call, don't tune the threshold.
- **Cadence state**: persisted in `workspace/memory/idle_job_state`
  (`last:<name>` keys). Deleting that file resets all cadences (= one
  catch-up herd, spread by jitter). Every MEDIUM/HEAVY job MUST have a
  `_HEAVY_MIN_CADENCE` entry — CI enforces
  (`tests/test_idle_cadence_invariants.py`).

## Migration / rollback (Phase 1b, executed 2026-06-12)

Performed: watchdog paused (`launchctl bootout gui/$UID/org.andrus.botarmy.gateway-watchdog`)
→ `docker compose stop gateway` → `scripts/migrate_chroma_to_volume.sh`
(copies sqlite + WAL/SHM + UUID segment dirs per KB, excludes
ledgers/snapshots, PRAGMA integrity_check each, chown 1000) → row-count
compare src vs volume (all MATCH) → set `CHROMA_DATA_ROOT: /chroma` in
compose → `docker compose up -d --no-deps gateway` → boot scan all-ok →
watchdog resumed (`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/org.andrus.botarmy.gateway-watchdog.plist`).

**Rollback** (any time in the 7-day retention window): remove the
`CHROMA_DATA_ROOT` env line from compose, `docker compose up -d --no-deps
gateway` — the untouched bind-mount originals at `workspace/<kb>/` take
over. After 7 clean days, archive them:
`mv workspace/<kb>/chroma.sqlite3 workspace/<kb>/chroma.sqlite3.pre_volume_<ts>`
(+ the `-wal`/`-shm` and UUID segment dirs).

## Worker re-enable (Phase 4 — gated)

Preconditions: 48h with **zero** watchdog wedge-restarts
(`grep "Threshold breached" workspace/healing/.gateway_watchdog.log`) and
**zero** new loop-stall dumps. Then:
1. `git rm docker-compose.override.yml` (its LIGHT-serialization +
   SYS_PTRACE diagnostics are superseded by the root fixes + sentinel).
2. `docker compose --profile worker up -d worker`.
3. Verify: worker log `role=worker → 17/N jobs run here`; gateway shows the
   complement; `docker stats` worker memory steady.
4. Rollback: `docker compose stop worker` — jobs resume gateway-side on
   their persisted cadences.

## Verification surfaces

- `/health` SLO: p99 < 100 ms (2026-06-12 post-ship sample: p50 21 / p95 44 /
  p99 63 ms, 0 failures).
- Watchdog log: zero `Threshold breached` lines during soak.
- `workspace/healing/loop_stalls/`: empty during soak; any file = a named
  culprit to fix.
- Boot: `main: chromadb integrity scan complete — ok=[all 7 KBs]`.
- latency_slo monitor: baseline re-establishes post-migration (≥50 samples).

## Deferred follow-up

Postgres (`workspace/mem0_pgdata`) and Neo4j (`workspace/mem0_neo4j`) remain
on the bind mount — same fragility class but separate containers that cannot
wedge the gateway loop. Operator decision 2026-06-12: fold their named-volume
move into the PG16→18 migration window
(`deploy/POSTGRES_PG18_MIGRATION.md`).
