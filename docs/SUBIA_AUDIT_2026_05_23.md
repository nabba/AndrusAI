# SubIA wiring audit — 2026-05-23

Three-round audit of SubIA integrity + producer/consumer wiring across
the past 14 days of substantial new functionality (PROGRAM §44–§64,
roughly Q6 through §63 upgrade-lifecycle).

| Round | Commit | Scope | Files | Tests |
|---|---|---|---|---|
| 1 | `7560d067` | Boundary leak + broken ledger + RPT-1 + HOT-1 parser | 19 | 39 |
| 2 | `e42d2616` | Apply-time re-validation gate | 2 | 4 |
| 3a | `d11dced8` (operator's §63 sweep) | Linter telemetry + HOT-4 telemetry emitter + threads wire | 4 of mine | 7 |
| 3b | `954ce3eb` | HOT-4 reader + AE-2 outcome readers | 4 | 7 |

**Total: 8 net source changes (4 new modules, 4 edits) + 1 doc + 57 pinning tests.**

## Findings + fixes

### 1. App/subia/* boundary leak in the standard CR validator — HIGH

`app/change_requests/validator.validate()` only refused TIER_IMMUTABLE
via exact-match against the ~95-entry `auto_deployer.TIER_IMMUTABLE`
list. No file under `app/subia/` was on that list. The architectural
invariant "agent paths cannot mutate the consciousness layer" was
loose: an agent could file a CR for `app/subia/scene/global_workspace.py`
and the operator gate was the only safety.

**Fix** in `app/change_requests/validator.py`: added
`_FORBIDDEN_PATH_PREFIXES = ("app/subia/",)` + `_FORBIDDEN_INDIVIDUAL_FILES = {"app/affect/goal_emitter.py"}`
checked after the TIER_IMMUTABLE exact-match. Refusal returns
`is_tier_immutable=True` so the lifecycle records `TIER_IMMUTABLE_REFUSED`
not plain `REJECTED`.

### 2. Four broken ledger emissions failing silently for weeks — HIGH

`app/identity/continuity_ledger.py` exposes `record_event(kind=, actor=, summary=, detail=)`.
Four sites imported nonexistent `emit_event` or `append_event`, wrapped
in `try: ... except: logger.debug(...)` so they shipped silently:

| Site | API used | Intended kind | Status |
|---|---|---|---|
| `memory/chromadb_integrity.py:352` | `append_event` | `chromadb_corruption` | fixed |
| `capability_regression/scheduler_job.py:75` | `emit_event` | `capability_regression` | fixed |
| `upgrade_lifecycle/ecosystem_snapshot.py:530` | `emit_event` | `ecosystem_snapshot` | landed via §63 |
| `upgrade_lifecycle/requirements_writer.py:228` | `emit_event` | `ecosystem_snapshot` | landed via §63 |

All three intended kinds (`ecosystem_snapshot`, `chromadb_corruption`,
`capability_regression`) were also MISSING from `IDENTITY_EVENT_KINDS`.
Even if the import had resolved, `record_event` would have rejected
the kind. **Double bug.**

**Fix**: added the three kinds to the frozenset; replaced all four
import sites with correct `record_event` calls.

### 3. cr_apply scorer broken since Q5 — HIGH (pre-existing)

`_scorer_cr_apply` in `app/sentience_experiments/rpt1_self_calibration.py`
imported nonexistent `load_request` from `change_requests/lifecycle`
and read `.state` instead of `.status`. Both swallowed by try/except.
Every cr_apply forecast resolved to `None` (= never scored) since Q5
shipped (2026-05-13). RPT-1 calibration was structurally empty for
its primary claim_kind.

**Fix**: corrected to `from app.change_requests.store import get` and
`getattr(cr, "status", None)` with proper terminal-state mapping
(`applied`/`rejected`/`rolled_back`/`timeout`/`apply_failed`).

### 4. HOT-1 trace parser broken since Q5 — HIGH (pre-existing)

`_load_trace_points` in `hot1_meta_affect.py` read top-level `ts` /
`valence` / `arousal` / `controllability`, but the canonical producer
`affect.core._append_trace` writes rows shaped
`{"affect": {ts, valence, ...}, "viability": {...}}`. HOT-1 was
reading **zero** rows from a `trace.jsonl` with thousands of rows.
All four pattern detectors (`temporal_cluster`, `recurring_trigger`,
`sequence`, `baseline_drift`, `attractor_lock`) had been silently
no-op'd over the trace path.

**Fix**: parser prefers nested `affect` block and falls back to flat
for backward compatibility. Post-fix: 17 rows read from dev workspace
where 0 were read before.

### 5. RPT-1 calibration coverage of new lifecycles — MEDIUM

Only `tier3_approval` and `cr_apply` had forecasts registered. The
Q7–Q18 ship introduced 5 substantial new lifecycles with no
calibration data.

**Fix**: 5 new scorers + 5 register_prediction wires in:

| Claim kind | Scorer reads | Wire site |
|---|---|---|
| `thread_resolve` | `threads.store.get(thread_id).status` | `threads/lifecycle.create_thread` |
| `workflow_run_success` | `workflows.queue.get_run(run_id).status` | `workflows/queue.enqueue` |
| `architecture_request_apply` | `architecture_requests.store.get(req).status` | `architecture_requests/lifecycle.create_request` |
| `executor_run_success` | `autonomous_executor.store.get(run).status` | `autonomous_executor/tools/delegate_tool.delegate_goal` |
| `capability_adoption_apply` | delegates to `cr_apply` (distinct bucket for adoption CRs) | `upgrade_lifecycle/capability_adoption.run_one_pass` |

Scorers are all in `app/sentience_experiments/rpt1_self_calibration.py`;
each is a deterministic outcome resolver (`register_scorer` refuses
LLM/agent module sources).

### 6. HOT-1 lifecycle hook coverage — MEDIUM

The existing `affect_post_llm` hook was the only `compute_affect`
producer. Substantial new high-stakes lifecycle events (executor
BLOCKED transitions, thread closures) had no affect snapshot
boundary.

**Fix**: two new `compute_affect(persist=True)` hooks:
- `autonomous_executor/escalation.escalate_blocker` — emits a snapshot
  after the BLOCKED transition + Signal alert.
- `threads/lifecycle._distill_on_closure_safely` — emits a snapshot
  after thread resolve/abandon.

Both failure-isolated. They run after the lifecycle transition is
persisted, so a broken `compute_affect` never rolls back the
transition.

### 7. PhenomenalLanguageLinter coverage of new LLM producers — LOW–MEDIUM

`threads/approaches._llm_distill` produces "approaches tried" closure
summaries that land in the `lessons_learned` KB. Identity-adjacent —
self-introspection on problem-solving. Was missing the same mechanical
linter pass that gates inquiry / annual_reflection / legacy_essay /
long_term_goal_review / probe_proposals.

`drift_digest.py` was checked — deterministic, no LLM, no linter
needed.

**Fix**: added `PhenomenalLanguageLinter().lint(out)` after the
LLM call; HARD_FAIL → return `""` (caller falls back to deterministic
body builder).

### 8. Apply-time re-validation gap — HIGH (no immediate exposure)

Standard CR `validate()` runs at `create_request` time. `apply_change`
only checked `status == APPROVED` before file write. Validator policy
can tighten between creation and apply (e.g. Round 1 added the
`app/subia/` prefix refusal); a PENDING CR under an older lenient
policy could land via operator approval after the tightening.

**Verified zero immediate exposure**: all 6 PENDING CRs at audit
time targeted `wiki/index.md` or `docs/RESILIENCE_DRILLS.md`, none
under `app/subia/`. Fix is defense-in-depth, not incident response.

**Fix** in `app/change_requests/apply.py`: `apply_change` now
calls `validate(path=cr.path, new_content=cr.new_content)` before
the host-bridge file write. On refusal, CR transitions to
APPLY_FAILED (the legitimate failure state). Failure-isolated on
validator-raise (degrades to pre-fix behaviour rather than fail-
closed across all applies).

### 9. Silent linter rejection — MEDIUM

The Round 1 fix to `threads/approaches.py` introduced a new
silent-degradation path: LLM HARD_FAIL → empty return → caller uses
deterministic body, no operator-visible signal. This was exactly
the antipattern Round 1 was auditing against.

**Fix**: new `app/threads/linter_telemetry.py` module:
- `record_rejection(thread_id, violations, body_text_len)` appends
  to `workspace/threads/linter_rejections.jsonl` (capped 1000 rows)
- updates running summary at `workspace/threads/linter_state.json`
  (`total_rejections`, `last_rejection_ts`, `by_pattern` counter)
- `summary()` reader for operator surfaces (briefing, REST, CLI)

Wired into `_llm_distill` after the linter result is computed.
Failure-isolated end-to-end.

### 10. HOT-4 metacog blind to autonomous_executor — MEDIUM

HOT-4 reads `workspace/observability/loadable_agent_usage.jsonl`,
which only the LoadableAgent path writes. The autonomous executor
(`autonomous_executor/driver._execute_step`) dispatches via
`Commander.handle()` — structurally invisible to HOT-4.

**Fix** in two parts:
- new `app/autonomous_executor/hot4_telemetry.py` with
  `emit_step_telemetry(run, step)` writing per-step rows in the
  HOT-4 schema to `workspace/observability/executor_step_calls.jsonl`.
- `hot4_metacog_monitor._iter_telemetry` now folds both paths.
  Per-agent baselines keep each agent's history separate via
  `agent_id="autonomous_executor:<run_id>"` so the executor doesn't
  pollute researcher/writer baselines.

### 11. AE-2 causal credit blind to executor + drill outcomes — MEDIUM

AE-2 reads four canonical outcome streams (`errors`, `welfare_audit`,
`audit_log`, `loadable_agent_usage`). The autonomous executor's
audit ledger and the resilience drills' audit ledger were not
visible — exactly where rare actionable outcomes (BLOCKED, FAILED,
drill failures) live.

**Fix** in `app/sentience_experiments/ae2_causal_credit.py`:
- two new path getters (`_default_executor_audit_path`,
  `_default_drill_audit_path`)
- two new adapters (`_outcome_kind_from_executor`,
  `_outcome_kind_from_drill`) that filter routine transitions and
  surface only rare outcomes
- `detect_associations` extended with two new reader loops; drill
  loop walks the file directly because drill rows use `completed_at`
  not `ts`

## Things the audit did NOT change

- **No TIER_IMMUTABLE files modified.** Standard +
  Tier-3 amendment surfaces stayed intact.
- **No files under `app/subia/` modified** except the regenerated
  `.integrity_manifest.json` (which is itself the boundary refresh
  and emits an `integrity_regen` ledger landmark automatically).
- **No Tier-3 amendments needed.** All wiring is additive +
  observational + reversible.
- **No new master switches.** No operator policy changes — these
  are bugfixes + observability widening.
- **Butlin scorecard pinning test
  (`test_q5_does_not_change_butlin_scorecard`) still passes.** The Q5
  sentience modules (AE-2, HOT-1, HOT-4, RPT-1) remain ABSENT in the
  scorecard. Anti-Goodhart guarantee intact.

## Bookkeeping deltas

```
SubIA integrity manifest:     166 files, ok=True, 0 drift
IDENTITY_EVENT_KINDS:         28 (was 25 — added 3 new kinds)
RPT-1 scorers:                7 (was 2 — added 5 new lifecycles)
HOT-4 input streams:          2 (was 1)
AE-2 outcome streams:         6 (was 4)
HOT-1 trace parser:           reads canonical nested + flat
app/subia/ refusal gates:     5 — create + apply + arch_req + vacation + evolution
PhenomenalLanguageLinter wires: 5 + telemetry (was 4)
Butlin scorecard:             STRONG=7, PARTIAL=3, ABSENT=4 — unchanged
```

## §63 follow-up tasks spawned

Three operator-facing chips (Round 2 + 3 surfaced these in §63's
working-tree state):

1. **Path guard for `capability_adoption.py`** — early-bail on
   `app/subia/*` candidates before LLM budget spend. Plus
   `apply_hook.py` failure ledger event for AE-2 visibility.

2. **CVE source divergence event** — `record_event(kind="ecosystem_snapshot",
   subkind="cve_source_divergence")` in `cve_sources.py` when OSV
   and GitHub advisories disagree on a finding.

3. **Briefing section for linter rejections + HOT-1 patterns** —
   surface the new telemetry to the daily briefing via the
   `briefing_evolution` proposal/trial/adopted lifecycle.

## Cross-references

- Round 1 commit: `7560d067`
- Round 2 commit: `e42d2616`
- §63 commit (operator): `d11dced8` (absorbed 3 of my Round 3 files)
- Round 3 commit: `954ce3eb`
- Related: `crewai-team/docs/SUBIA.md`, `crewai-team/docs/CONSCIOUSNESS_ROADMAP.md`, `crewai-team/docs/CONSCIOUSNESS_HOT1_OBSERVATIONS.md`
- PROGRAM.md §65 — change-log entry summarizing this audit
