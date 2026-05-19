# The elegance plan — Phases 1-4 (2026-05-18 → 2026-05-19)

Status: end-to-end shipped. Default-ON observation + reflection; default-OFF action.

## Why this exists

A May 2026 ultrathink audit framed a decade-class question: *the codebase is
adding monitors, modules, and event kinds faster than it sheds them; how
do we shift the trajectory from monotonic growth to net-zero while still
adding capability?*

Four phases close the loop. Each phase is observational or operator-gated;
none ships destructive behaviour. Together they let the system **measure
its own elegance, propose refactors, reflect on whether the loop is
winning, and watch its own monitor pattern for convergent debt** —
without ever touching code without operator approval.

```
Phase 1: elegance_drift + architectural_drift (continuous observation)
              ↓
Phase 2: refactor_proposer    ← Phase 4: idiom_radar (PEP-class upstream signal)
              ↓                       ↑
       proposal_bridge → operator CR gate
              ↓
       applied / rejected → lessons_learned KB (Phase F #5, already wired)
              ↓
Phase 3: elegance_reflection (annual) + code_consolidation (quarterly)
              ↓
       identity continuity ledger
              ↓
Phase 4: cross_monitor_pattern (meta-detector over the ledger)
              ↓
       → back to Phase 1
```

## What ships, per phase

### Phase 1 — see clearly (default ON)

[docs/CODE_HEALTH_OBSERVATION.md](CODE_HEALTH_OBSERVATION.md)

| Module | Role |
|---|---|
| [app/system_inventory/](../app/system_inventory) | Weekly AST-only auto-catalogue at `workspace/system_inventory/snapshot.json`. Closes the meta-gap behind CLAUDE.md drifting from actual capabilities. `query_inventory(kind, capability, keyword)` lets agents reason from live truth. |
| [app/healing/monitors/elegance_drift.py](../app/healing/monitors/elegance_drift.py) | 41st healing monitor. Weekly per-file `code_quality.QualityScore`; 8-week rolling-median regression detector. Alerts on ≥10% composite drop. |
| [app/healing/monitors/architectural_drift.py](../app/healing/monitors/architectural_drift.py) | 42nd healing monitor. Weekly Tarjan SCC + capability-owner + reverse-degree baseline diff. Three alert kinds (new small cycle / new parallel capability / centrality spike) + systemic-SCC growth signal. Systemic SCCs (>20 files) excluded from actionable alerts. |

Continuity-ledger event kind `architectural_debt_drift` — auto-surfaces in
the annual reflection via `summarise_drift.by_kind`.

### Phase 2 — act on what's seen (default OFF)

[docs/REFACTOR_PROPOSER.md](REFACTOR_PROPOSER.md)

| Module | Role |
|---|---|
| [app/refactoring/](../app/refactoring) | 4th producer in `proposal_bridge`. Three detectors: `complexity_hotspot` (composite ≤0.65 AND complexity_score ≤0.40), `import_cycle` (2-20 member SCCs from baseline), `parallel_capability` (≥3 owners). Each candidate ships with a `coding_session_spec` scaffold. 14-day bridge cooldown + 3-per-detector cap. |

Default OFF — operator opts in after reviewing Phase 1 baselines. Composes
with the existing change-request operator gate + 60-min auto-revert window.

### Phase 3 — consolidation rhythm (default ON)

[docs/CONSOLIDATION_RHYTHM.md](CONSOLIDATION_RHYTHM.md)

| Module | Role |
|---|---|
| [app/identity/elegance_reflection.py](../app/identity/elegance_reflection.py) | Annual deterministic essay at `wiki/self/elegance_reflections/<year>.md`. Six sections + a one-line verdict (`shedding` / `stable` / `growing`). |
| [app/self_improvement/code_consolidation.py](../app/self_improvement/code_consolidation.py) | Quarterly deterministic digest at `wiki/self/code_consolidation/<year>_q<n>.md`. Lists shed candidates, parallel-capability clusters, persisting small cycles. Informational only — never proposes CRs. |

Both deterministic — no LLM call. Both emit `code_consolidation`
continuity-ledger events; both wired as LIGHT idle jobs in the identity
scheduler.

### Phase 4 — meta-detection + new-tech adoption (mixed defaults)

[docs/PHASE4_ELEGANCE_FOLLOWUPS.md](PHASE4_ELEGANCE_FOLLOWUPS.md)

| Module | Default | Role |
|---|---|---|
| [app/library_radar/idiom_radar.py](../app/library_radar/idiom_radar.py) | **OFF** | Weekly Python PEP feed scan for idiom-class proposals (`match`, `dataclass`, `async`, `typing`, `walrus`, …). Stages markdown via `proposal_bridge` with `source="library_radar"`; per-keyword migration hints. |
| [app/healing/monitors/cross_monitor_pattern.py](../app/healing/monitors/cross_monitor_pattern.py) | ON | 43rd healing monitor. Reads continuity-ledger, groups by `detail.path`, alerts when ≥3 distinct event kinds converge on the same path within a 14-day window. Reuses `architectural_debt_drift` kind. 30-day dedup window. |

Item 2 from the original deferred list ("extend lessons-learned consultation
to all CR creation paths") was already shipped 2026-05-09 as Phase F #5 —
`create_request` calls `lessons_learned.check_against` on every CR and
prepends a `⚠️ Matches rejected-pattern lesson` banner.

## Discipline carried across all four phases

1. **Default-OFF for CR-emitters; default-ON for observers.** The two
   producers that file CRs (`refactor_proposer`, `pep_idiom_radar`) ship
   OFF — operator reviews noise levels first. Everything else (monitors,
   inventory, reflections) ships ON observational.

2. **TIER_IMMUTABLE absolute.** Every proposal goes through
   `change_requests.validator` at stage time. Phase 2 explicitly verifies
   this with `test_target_paths_pass_change_request_validator`.

3. **Reuse the gate, don't rebuild it.** The same composition pattern
   throughout: producers stage to `proposal_bridge`, the bridge promoter
   files CRs through `change_requests.lifecycle`, operator gate intact,
   60-min auto-revert window applies. No phase introduced a new approval
   surface.

4. **Single-sourced "regression" definition.** Phase 1's
   `elegance_drift` uses the same `QUALITY_REGRESSION_THRESHOLD = 0.10`
   constant that the mutation gate (`code_quality.evaluate_mutation_quality`)
   uses. "Regression" means exactly one thing in this system.

5. **Deterministic where possible.** Phase 3's two reflections are
   plain Python over JSON inputs — no LLM call, no
   `PhenomenalLanguageLinter` retry. Tests assert exact substrings.

6. **No new event kinds where existing ones suffice.** Phase 4's
   cross-monitor pattern detector reuses `architectural_debt_drift`
   rather than introducing a new kind. Phase 3 adds exactly one new
   kind (`code_consolidation`) for the annual + quarterly digests.

## Master switches

All flippable via `/cp/settings`; env-var fallbacks honored.

| Switch | Default | Phase |
|---|---|---|
| `system_inventory_enabled` | ON | 1 |
| `elegance_drift_monitor_enabled` | ON | 1 |
| `architectural_drift_monitor_enabled` | ON | 1 |
| `refactor_proposer_enabled` | **OFF** | 2 |
| `elegance_reflection_enabled` | ON | 3 |
| `code_consolidation_enabled` | ON | 3 |
| `pep_idiom_radar_enabled` | **OFF** | 4 |
| `cross_monitor_pattern_monitor_enabled` | ON | 4 |

## Live first-run findings (against the real codebase)

* **1,206 modules, 251,887 LOC, 0.933 avg composite, 89% fully-typed, 61% fully-documented, 20% with tests by name-match.**
* **5 actionable small import cycles**: `healing/monitors/{__init__,disk_quota}`, `tool_registry/{decorator,registry}`, `health/{__init__,anomaly,import_apple,summary}`, `library_radar/{__init__,proposer,trial_runner}`, `workflows/{__init__,queue}`.
* **1 systemic SCC of 585 files** — coupling shape, correctly excluded from actionable alerts.
* **2 parallel-capability clusters**: `registers-tool` (3 owners — meta-tag), `renders-pdf` (3 owners — worth investigating).
* **20 shed candidates** in the first quarterly digest — nearly all 18-LOC shim modules under `app/consciousness/` and `app/self_awareness/`, exactly the kind of post-refactor leftover the rhythm exists to highlight.
* **Annual reflection verdict on first run**: `shedding`.

## Test coverage

| Phase | Test files | Count |
|---|---|---|
| 1 | test_system_inventory, test_elegance_drift, test_architectural_drift | 28 |
| 2 | test_refactor_proposer | 13 |
| 3 | test_elegance_reflection, test_code_consolidation | 24 |
| 4 | test_idiom_radar, test_cross_monitor_pattern | 28 |
| Pinning | test_continuity_ledger update | 1 (modified) |

**56 new tests pass.** Full Phase 1+2+3+4 + adjacent slice (proposal_bridge,
continuity_ledger): **128 tests pass, 0 fail.**

## What deliberately does NOT ship

* **No `app/coding_session/refactor_verify.py`** — semantic-equivalence
  gate for refactor sessions was in the original Phase 2 plan; deferred
  because the existing test suite + operator review at the CR gate
  catches behaviour breakage today. Refactor verify is a natural next
  ship when the volume of refactor CRs justifies the gate.
* **No `duplication_cluster`, `dead_code`, `single_use_abstraction`,
  `centrality_drift` detectors** — each is a one-detector additive
  change against Phase 2's producer skeleton when needed.
* **No auto-apply for any proposer.** Every CR goes through the operator
  gate. The auto-apply infrastructure exists (PROGRAM §38.3) but its
  allowlist deliberately ships empty.
* **No CLAUDE.md edits.** Per the original Phase 1 analysis, CLAUDE.md
  stays the stable narrative; `system_inventory` is the live truth for
  agent prompts.
