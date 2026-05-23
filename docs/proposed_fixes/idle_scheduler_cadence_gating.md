# Idle-scheduler cadence gating — proposal

**Date**: 2026-05-22
**Status**: Proposed — needs operator review (touches TIER_IMMUTABLE)
**Triggered by**: 06:18 EEST self-heal alert showing 4 stale cron jobs
  (`error_resolution`, `code_audit`, `workspace_sync`, `self_improve`).

## TL;DR

The idle scheduler runs **39 LIGHT jobs at ~34 cycles/hour** with **zero
wrapper-level cadence gates**. Some of the inner functions handle their
own no-op-when-not-needed logic; some do not. The cumulative overhead
(logging, thread-pool churn, lock contention with chromadb/postgres) is
delaying APScheduler cron jobs by up to **45 minutes**, beyond the 60 s
`misfire_grace_time` window — APScheduler then silently drops those runs.

`code_audit` has not successfully fired since 2026-05-19 17:12 (≥61 h).

## Evidence

### Observed at 2026-05-22 12:25 EEST

- **691 INFO lines in 5 min** = ~140 lines/min sustained log activity.
- Per-job idle-scheduler cycle counts in the last hour (sample):
  ```
   34 wiki-index-reconciler
   34 viability-goal-emitter
   34 version-snapshot
   34 transfer-attribution
   34 tier-graduation
   34 skills-mirror
   …
  ```
- APScheduler dropped-job warnings (sample, last 6 h):
  ```
  Run time of job "run_code_audit" was missed by 0:45:55  ← dropped
  Run time of job "run_error_resolution" was missed by 0:12:14
  Run time of job "sync_workspace" was missed by 0:11:43
  ```
- 27 `was missed by` warnings in 6 h (~4.5/h).

### Direct evidence of bad cadence

1. **`tier_graduation.evaluate_all_graduations`** has the docstring
   `"Called by idle_scheduler weekly. Most evaluations are no-ops; this is
   the slow trickle of trust-building."` — but the wrapper invokes it
   ~34×/hour. Three orders of magnitude off the documented cadence.
2. **`version_manifest.create_manifest`** has no internal frequency
   gate — `_version_snapshot` calls it every cycle, producing ~34
   manifests/hour, then running `cleanup_old_snapshots(keep_latest=10)`
   on every call too.

### Architectural finding

Regex scan of all 39 LIGHT-job wrappers found **0 with any
cadence-guard pattern** (`time.monotonic()`, `_last_`, `min_interval`,
`cooldown`, etc.). The idle-scheduler architecture assumes each called
function handles its own cadence. Some do (e.g. `dlq-drain` reads a
queue — empty = cheap no-op); some don't (the two above).

## Status (2026-05-22 16:00 EEST)

**All three fixes shipped + verified.** Plus a companion fix to
`app/healing/monitors/cron_liveness.py` (footprint path correction for
`self_improve` was misaligned with where SelfImprovementCrew actually
writes).

Live verification (10-min window):
- 0 APScheduler `was missed by` warnings (was ~4.5/h)
- INFO log volume: ~138/min → ~59/min (**57% reduction**)
- All 4 monitored cron jobs touching footprints on schedule
- `code_audit` resurrected (was 61 h dormant)
- `self_improve` resurrected (was 109 h dormant) — Improvement scan
  creating proposals again

**Refined finding after implementation**: the system has **83 total
LIGHT jobs** (not 41 as initially regex-counted). The 41 I covered
are the older jobs that lack internal cadence guards. The other 42 —
predominantly the post-2026-05 additions: `life-companion-*` (12),
`identity-*` (5), `sentience-*` (4), `companion-*` (3), `browse-*`
(2), and singletons like `paper-pipeline`, `lessons-learned`,
`governance-auto-propose`, `health-summary`, `resilience-drills`,
`adapter-performance`, `operator-transition`, `inbox-tick`,
`feedback-router`, `interest-model`, `conversation-memory-index`,
`cross-modal-patterns`, `tension-detector`, `social-graph`,
`person-model`, `graph-features`, `governance-auto-propose`,
`goodhart-enforcing-proposer`, `identity-code-consolidation`,
`identity-elegance-reflection`, `identity-legacy-essay`,
`identity-long-term-goal-review`, `identity-annual-reflection` —
**already handle cadence internally**. Per the docstring at
[companion/loop.py:81](../../app/companion/loop.py#L81):

> All life-companion jobs cadence-check internally and respect the
> LIFE_COMPANION_ENABLED master switch + per-feature flags.

Adding scheduler-level gates for these would be **redundant** at best
and could introduce subtle conflicts at worst (e.g. preventing an
internal wall-clock-aligned check from firing on its right minute).

Remaining log noise is mostly the `_run_single_job` "completed" log
line firing even when the inner function no-ops out via internal
gating. Further reduction would require changing `_run_single_job`
(currently logs unconditionally at INFO) — but that's a deeper
refactor with cross-cutting concerns and not worth the marginal gain.

## Proposed fixes

### Fix A — surgical gate on `_tier_graduation_eval` (24 h)

At [`app/idle_scheduler.py:1337`](../../app/idle_scheduler.py#L1337):

```python
_tier_graduation_last_run: float = 0.0

def _tier_graduation_eval() -> None:
    global _tier_graduation_last_run
    if time.monotonic() - _tier_graduation_last_run < 24 * 3600:
        return  # docstring says weekly; 24 h is still 7× more often
    try:
        from app.tier_graduation import evaluate_all_graduations
        evaluate_all_graduations()
        _tier_graduation_last_run = time.monotonic()
    except Exception:
        logger.debug("idle_scheduler: tier_graduation failed", exc_info=True)
jobs.append(("tier-graduation", _tier_graduation_eval, JobWeight.LIGHT))
```

Impact: 34/h → 1/day. Risk: low — the inner function's docstring confirms
this is the intended cadence.

### Fix B — surgical gate on `_version_snapshot` (1 h)

At [`app/idle_scheduler.py:1435`](../../app/idle_scheduler.py#L1435):

```python
_version_snapshot_last_run: float = 0.0

def _version_snapshot() -> None:
    global _version_snapshot_last_run
    if time.monotonic() - _version_snapshot_last_run < 3600:
        return
    try:
        from app.version_manifest import create_manifest, cleanup_old_snapshots
        create_manifest(promoted_by="system", reason="periodic snapshot")
        cleanup_old_snapshots(keep_latest=10)
        _version_snapshot_last_run = time.monotonic()
    except Exception:
        logger.debug("idle_scheduler: version snapshot failed", exc_info=True)
jobs.append(("version-snapshot", _version_snapshot, JobWeight.LIGHT))
```

Impact: 34/h → 1/h. Risk: low — rollback safety still preserved (one
fresh manifest per hour); `keep_latest=10` window covers 10 h of history.

### Fix C — scheduler-level cadence map (systemic)

Add a per-job minimum-cadence map consulted in `_run_idle_loop`.

```python
# Per-LIGHT-job minimum cadence (seconds). Jobs not listed run every cycle.
# These are MINIMUMS — inner functions may still no-op faster than this.
_LIGHT_MIN_CADENCE: dict[str, float] = {
    "version-snapshot":           3600,   # 1 h
    "tier-graduation":           86400,   # 24 h (docstring says weekly)
    "atlas-stale-check":          3600,   # 1 h
    "atlas-competence-sync":      1800,   # 30 min
    "wiki-index-reconciler":      1800,   # 30 min
    "wiki-hot-cache":              600,   # 10 min
    "skills-mirror":              1800,   # 30 min
    "skill-index":                1800,   # 30 min
    "improvement-narrative":     86400,   # daily
    "self-model-refresh":         3600,   # 1 h
    "feedback-aggregate":         1800,   # 30 min
    "evaluator-sweep":            3600,   # 1 h
    "safety-health-check":         300,   # 5 min
    "health-evaluate":             600,   # 10 min
    "data-retention":            86400,   # daily
    "spans-retention":           86400,   # daily
    "judge-eval-retention":      86400,   # daily
    "log-archival":              86400,   # daily (if registered)
    "entropy-monitoring":         3600,   # 1 h
    "viability-goal-emitter":      600,   # 10 min
    "emergent-infrastructure":    3600,   # 1 h
    "system-monitor":              300,   # 5 min
    "heartbeat-cycle":              60,   # 1 min
    "fiction-ingest":             3600,   # 1 h
    "discover-topics":            3600,   # 1 h
    "transfer-attribution":       1800,   # 30 min
    "decentered-pass":            3600,   # 1 h
    "valve-audit-replay":         3600,   # 1 h
    "capability-regression":      3600,   # 1 h
    "meta-workspace-promotion":   3600,   # 1 h
    "llm-apply-promotions":       1800,   # 30 min
    "llm-refresh-catalog":        3600,   # 1 h
    "human-gate-expire":           600,   # 10 min
    "map-elites-migrate":         3600,   # 1 h
    "map-elites-maintain":        3600,   # 1 h
    "ollama-memory":              3600,   # 1 h
    "backward-counterfactual-replay": 3600,  # 1 h
    "dead-letter-retry":           600,   # 10 min
    "spans-watchdog":              300,   # 5 min
    # Intentionally NO gate — these should run every cycle:
    #   dlq-drain, belief-outbox-neo4j, belief-outbox-chroma
}

_light_job_last_run: dict[str, float] = {}

# Inside _run_idle_loop, just before submitting LIGHT jobs:
def _light_job_allowed(name: str) -> bool:
    min_cadence = _LIGHT_MIN_CADENCE.get(name, 0)
    if min_cadence == 0:
        return True
    last = _light_job_last_run.get(name, 0.0)
    if time.monotonic() - last < min_cadence:
        return False
    _light_job_last_run[name] = time.monotonic()
    return True

# Filter the per-cycle iteration:
for name, fn in light_jobs:
    if not _light_job_allowed(name):
        continue
    futures[light_pool.submit(_run_single_job, name, fn, TIME_CAPS[JobWeight.LIGHT])] = name
```

Impact: expected ~40-job × 34/h = 1360/h → ~80/h after gating.
Cron jobs should fire on schedule within minutes of recovery.

Risk: medium. The cadence numbers are educated guesses. Wrong values
could starve a job that genuinely needs frequent runs. The three
explicitly-omitted jobs (`dlq-drain`, both `belief-outbox-*`) are queue
drainers and need to stay ungated. Recommend deploying behind a
runtime_settings flag so the operator can disable instantly if a job
starves.

## Companion fix — `misfire_grace_time`

`app/main.py:145` sets `misfire_grace_time=60`. With saturation pushing
delays into the 45-min range, 60 s is no longer enough to tolerate the
*current* jitter (it was set when jitter was 3-4 s — see comment at
`app/main.py:135-143`). Recommend bumping to 600 s (10 min) — large
enough that late-fired cron jobs catch up, small enough that genuine
stalls still appear in the `was missed by` warnings.

This is also TIER_IMMUTABLE and would need the same operator path.

## Constraint — TIER_IMMUTABLE

Both `app/idle_scheduler.py` (line 229 of `auto_deployer.py`) and
`app/main.py` (line 85) are TIER_IMMUTABLE. Available paths:

1. **Manual operator edit** — operator applies the diff directly.
2. **Tier-3 amendment via React `/cp/amendments`** — formal protocol
   (PROGRAM §25.1, doc at `docs/TIER3_AMENDMENT.md`).

Change-requests via Signal are refused at validate-time for
TIER_IMMUTABLE paths (per CLAUDE.md "TIER_IMMUTABLE never reaches
Signal").

## Verification plan

After fixes A and B (minimum):
- Confirm `tier-graduation` log line drops from ~34/h to ~1/day.
- Confirm `_version_snapshot` activity drops from ~34/h to ~1/h.
- Confirm APScheduler `was missed by` warnings drop below 1/h.
- Confirm `code_audit` fires at next scheduled UTC hour-multiple-of-4.

After fix C:
- Total idle-scheduler log lines/min should drop by ~85%.
- All APScheduler crons should fire within 60 s of schedule.

## Open questions

- Should `dlq-drain`, `belief-outbox-neo4j`, `belief-outbox-chroma`
  truly run every cycle, or could they accept a 10-30 s minimum?
- Is there any job in the 39 that needs more aggressive scheduling
  than its current ~100 s? (Don't think so, but worth a check.)
- Should the cadence map be sourced from `runtime_settings` so the
  operator can tune without redeploys?
