# Evolution-Engine Consolidation Plan

**Date:** 2026-06-01 · **Repo/branch:** `crewai-team` @ `feat/gateway-serving-split`
**Goal:** Collapse the four overlapping self-improvement loops onto the single **verified mutation engine** (`app/self_improvement/`), feeding one operator-gated change pipeline. Every claim below is code-verified (`file:line`).
**Verdict:** **Stronger** — on safety, cost, and clarity. The only capability "lost" (automated prompt/meta evolution) was running *ungated on a broken signal*, so losing it is a safety gain. Done as the phased sequence below, **SubIA is unaffected and decade-autonomy degrades only in display surfaces, all of which are re-fed by one small wiring change.**

---

# ★★ FINAL PLAN (consolidated, round 5) — authoritative; supersedes the round 1–4 notes below (kept as audit trail)

**The whole subsystem in one sentence:**
> **Self-modification = notice a weakness (SubIA surprise · errors · low test-coverage) → propose a change → prove it by executing it in a throwaway worktree → ask the operator → record it in the identity ledger.** One path. Everything else is deleted.

That single loop replaces four overlapping engines, an ungated prompt-mutator, a broken keep-signal, engine-selection logic, **and three population-era data stores** (`variant_archive`, `evolution_db`, `evolution_roi` — verified loop-era: the loop is their only real writer).

### How it serves the five goals
- **Simplification** — one engine; one record (the existing hash-chained CR audit + identity ledger); **zero parallel stores**. ~12 files + 2 packages + 3 JSON/sqlite stores removed; 3 small modules remain.
- **Elegance** — no shims, no dead concepts, no empty endpoints, no "kept-and-fed" scaffolding. Each survivor has exactly one job.
- **10-year operation** — a future maintainer *(or the system reasoning about itself)* sees ONE mechanism. Fewer stores to back up / migrate / corrupt over a decade; nothing unreachable to trip over.
- **Sentience** — each self-modification is an honest `self_modification` entry in the identity continuity ledger that feeds the annual reflection: a truthful, continuous self-narrative ("what I changed about myself, and the evidence"). Targets are chosen by **interoception** — improve where the system was *surprised* (SubIA prediction error). No fabricated deltas; coherent with the epistemic-honesty gate used everywhere else.
- **Operator service** — one surface (the CR gate already in use), real evidence, **no noise** (the false "borderline mutation" alerts vanish), no surprise prompt edits. Autonomous work; interrupts the operator only with something real, gated, reversible.

### The core change is SMALL and Tier-3-free (the 80/20)
All the value is a handful of reversible edits touching no TIER_IMMUTABLE file:
1. **Stop ungated mutation** — remove the `island` / `parallel` / `meta-evolution` idle-job registrations (3 `jobs.append` lines). *(the safety win)*
2. **One honest engine, one trigger** — `run_evolution_session` unconditionally delegates to `run_verified_session`; **delete the 6h cron**, keep only the resource-aware idle job; add a re-entrancy lock; default the switch ON.
3. **Honest self-narrative** — emit a `self_modification` identity-continuity event when the verified engine files a CR. *(sentience + 10-yr)*
4. **Review the live prompts** — diff `v001`↔current for the 4 roles; operator keeps or reverts.

After steps 1–2 the **entire legacy stack is unreachable** — the system is already safer, simpler in behavior, and coherent, *before any file is deleted*.

### Deferred hygiene — delete the now-inert code WHOLE (never hollowed)
In one later batch: delete `evolution`, `avo_operator`, `shinka_engine`, `mutation_strategies`, `island/parallel/meta_evolution`, `cascade_evaluator`, `deploy_staging`, `experiment_runner`, **`variant_archive`, `evolution_db`, `evolution_roi`**, + the dead `evolution_suite`/`feedback_suite` packages. Rehome only the three genuinely-shared things:
- **target-selector** → a *small new* interoception+code_intel selector in `self_improvement/planning.py` (NOT a relocated 600-line AVO planner — the elegant version reads SubIA surprise + error patterns + coverage). Preserve the SubIA `accuracy_tracker` read.
- **two eval primitives** (`validate_response` + `load_test_tasks`) → `self_improvement/eval_primitives.py`.
- `_map_llm_models` → into `evolution_bridge.py`.

Repoint the ~6 display readers of `variant_archive`/`evolution_db` to the CR audit + identity ledger — this **improves** the weekly alignment-audit (real changes, not noise variants). Move `goodhart_guard`'s `rollback_silence` + `auto_deployer`'s rollback-tracking onto the CR audit's `ROLLED_BACK` status (one record). Tier-3 touches: deleting `experiment_runner`/`meta_evolution`/`shinka_engine` + delisting them in `auto_deployer.py`.

### Keep (genuinely live, nothing to do with the loop)
`map_elites` + `map_elites_wiring` (Commander prompt stochasticity), `pattern_library`, `prompt_registry`, `sandbox_runner`, `eval_sandbox`, `evo_memory` (now the verified engine's memory), `workspace/meta/*`.

### Invariants (re-confirmed across 4 adversarial passes)
SubIA untouched + surprise-read elevated to a feature · decade-autonomy *improved* (self-modification recorded in the identity ledger the reflection already reads, not a side store) · no boot break (all importers lazy+try/except) · "can't lower its own bar" is *stronger* now (TIER_IMMUTABLE `worktree_eval` judge + operator gate replace the loop's runtime self-hash) · single trigger + lock prevents concurrent evolver OOM (§76).

---

## Audit trail — how the plan evolved (rounds 1–4, retained for rationale)

## ⚑ Red-team round 4 (2026-06-02) — 6 refinements to the no-shim design (1 real bug avoided)

The no-shim design is sound (no boot break, SubIA-safe, decade-autonomy-safe — all re-confirmed), but the adversarial pass found one production-grade bug and several precision fixes. These refine the ◎ section below.

1. **★ REAL BUG — concurrent evolver spawns.** `self_improvement/orchestrator.py` has **no concurrency guard** (verified: no Lock/`_in_flight`), and the ◎ rewire pointed *both* the 6h APScheduler cron (`main.py:546`, started unconditionally `:698`) **and** the idle HEAVY job at it. Plus operator-initiated runs hit `run_verified_cycle` via the executor. Two overlapping sessions each spawn evolver containers → the exact §76 OOM-the-gateway failure mode. **Fix:** (a) **delete the 6h cron** — keep a single, *resource-aware* trigger: the idle HEAVY job (it already yields to user tasks + defers under the §82 memory-ceiling brake, which a blunt timer can't); and (b) add a module-level **re-entrancy lock** in `run_verified_session`/`run_verified_cycle` (skip-if-running) since the idle job and an operator executor run can still overlap. This also retires the now-vestigial `EVOLUTION_CRON` + `settings.evolution_iterations` config (config-level dead code).

2. **`eval_primitives.py` is just TWO functions** — `validate_response` + `load_test_tasks`. Precise import-stmt grep: those are the only externally-imported eval primitives (`external_benchmarks`, `goodhart_guard`, `proposals`). `eval_set_score`, `verify_eval_integrity`, `compute_eval_hash` have **zero external importers** → loop-only → deleted with `experiment_runner`. Smaller, cleaner extraction than the ◎ table implies.

3. **Deleting `verify_eval_integrity` is NOT a safety regression.** It was the legacy loop's runtime self-hash ("eval scoring file unchanged"). The verified engine's safety is structurally stronger and already in place: the judge `worktree_eval` is **TIER_IMMUTABLE** (the Self-Improver cannot lower its own bar) and every change is **operator-gated**. The integrity property is preserved by a better mechanism, not lost.

4. **Planner: surgical relocation, not from-scratch rewrite.** Move `_phase_planning`'s *target-selection core* into `planning.py` and surgically strip only the `MutationSpec` submission tail + the `mutation_strategies` prompt section — preserving proven behavior. **MUST keep the SubIA surprise-bridge read** (`subia.prediction.accuracy_tracker`, `evolution.py:283`) that lives in `_build_evolution_context` — a from-scratch rewrite risks silently dropping it (a SubIA must-preserve). Code_intel-grounding is a *later* refinement, not part of the cut-over.

5. **Phase 1.5: default to operator DIFF-review, not blind rollback.** `v001` is the 2-month-old operator baseline; the live `v116/v121/v111/v107` are unreviewed but not necessarily *worse*. Blind `rollback(role, 1)` is itself a behavior change. Default action = surface the `get_diff(role, 1, current)` for each role and let the operator **review→keep or revert**. Rollback stays the one-click option, not the automatic one.

6. **`auto_deployer.py` is itself TIER_IMMUTABLE** (`:78`) → delisting the deleted files from its `TIER_IMMUTABLE`/`TIER_GATED` arrays is **Tier-3-gated**. But stale entries are **harmless** (`get_protection_tier` simply never matches a non-existent path), so: leave them, or batch the delist into the same Tier-3 amendment that deletes `experiment_runner`/`meta_evolution`/`shinka_engine`. Also note: the no-shim version deletes more files → **bigger CI surface** (the `experiment_runner`/`avo_operator`/`evolution` test files + any `test_subsystem_wiring` import-pins must be updated in the same PR).

**Net after round 4: the plan is correct, safe, and simpler** (2-function extraction; one resource-aware trigger + lock; integrity preserved by the immutable judge). The one must-do beyond the ◎ section is the **single-trigger + lock** fix — without it, making the verified engine autonomous risks concurrent evolver OOM.

---

## ◎ Elegant end-state (2026-06-02, round 3) — NO SHIMS; supersedes the "hollow to a facade" guidance everywhere below

**Principle (operator directive):** no file or function survives only to forward a call. Every kept symbol moves to its *logical owner*; every dead *concept* is removed, not stubbed; every retired file ends with **zero importers** and is deleted outright. The verified engine becomes a genuinely self-contained subsystem — **planner + measurement + memory + judge + gate all living under `self_improvement/`** — borrowing nothing from the corpse of the old loop.

### Complete rewire map (every external importer verified by grep)

| Symbol (current home) | External prod importers | Logical home / disposition |
|---|---|---|
| `run_evolution_session` (evolution.py) | `idle_scheduler.py`, `main.py`, `self_improvement/orchestrator.py`, `agents/commander/commands.py` | **rewire all 4 → `self_improvement.orchestrator.run_verified_session`** (delete the name) |
| `_build_evolution_context` (evolution.py) | `self_improvement/orchestrator.py` | **→ new `self_improvement/planning.py`** |
| `_phase_planning` (avo_operator.py) | `self_improvement/orchestrator.py` | **reimplement clean in `planning.py`** (LLM target-select from code_intel + skills + program.md + `evo_memory` recent-failures; **drop `MutationSpec` + `mutation_strategies`** — AVO-mutator-only) |
| `get_journal_summary` (evolution.py) | `agents/commander/commands.py` | **→ caller uses `results_ledger.format_ledger`** (it's already a 1-line shim to that) |
| `_select_evolution_engine` (evolution.py) | `api/evolution_api.py` | **DELETE concept** — only one engine now |
| `_is_shinka_available` (evolution.py) | `api/evolution_api.py` | **DELETE concept** |
| `_get_subia_safety_value` (evolution.py) | `api/evolution_api.py` | **DELETE** (endpoint reads SubIA directly if still wanted) |
| `_map_llm_models` (shinka_engine.py) | `coding_session/evolution_bridge.py` | **move into `evolution_bridge.py`** (its only consumer) |
| `validate_response`, `load_test_tasks`, `eval_set_score`, `verify_eval_integrity` (experiment_runner.py) | `external_benchmarks`, `goodhart_guard`, `proposals`, `sandbox_runner`, `llm_discovery` | **→ new `self_improvement/eval_primitives.py`**; repoint all 5 (keep the `eval_primitives ↔ sandbox_runner` import lazy to avoid a cycle) |
| `store_success`/`store_failure`/`format_memory_context`/`recall_similar_failures` (evo_memory.py) | *(loop only)* | **verified engine now calls `store_success`/`store_failure` on each verdict; `planning.py` reads `format_memory_context`/`recall_similar_failures`** → evo_memory stays a live, real module |
| `recall_similar_successes` (evo_memory.py) | `backup_planner.py` | unchanged (evo_memory kept) |

### Dead concepts → remove the surface too (no empty endpoints/cards)
- `api/evolution_api.py`: **delete `/engine` and `/meta`** (engine-selection + meta-evolution no longer exist — an endpoint that always returns `{}` is the API version of a shim) + remove their React cards. Keep `/variants` (now fed by the verified engine), `/summary`, `/results`, `/metrics`.
- Delete the dead aggregator packages `evolution_suite/` + `feedback_suite/` and scrub their mentions in `system_inventory/__init__.py`.

### Final file disposition (no file left as a forwarder)
- **DELETE OUTRIGHT** (zero importers after the rewire): `evolution.py`, `avo_operator.py`, `shinka_engine.py`, `mutation_strategies.py`, `island_evolution.py`, `parallel_evolution.py`, `meta_evolution.py`, `cascade_evaluator.py`, `deploy_staging.py`, `experiment_runner.py`, `evolution_suite/`, `feedback_suite/`.
- **NEW logical homes** (real content, single responsibility — not shims): `self_improvement/planning.py` (the planner), `self_improvement/eval_primitives.py` (the measurement). `_map_llm_models` folded into `evolution_bridge.py`.
- **KEEP as real modules** (genuine content + live consumers): `evo_memory.py` (now written by the verified engine; *optional* rename → `self_improvement/improvement_memory.py` for naming hygiene), `variant_archive.py`, `evolution_roi.py`, `evolution_db/`, `map_elites.py`, `map_elites_wiring.py`, `pattern_library.py`, `prompt_registry.py`, `sandbox_runner.py`, `eval_sandbox.py`, `workspace/meta/*`.

### The one "dead code inside a kept file" resolved
The earlier draft kept `experiment_runner.py` (eval-primitive home) with its dead `ExperimentRunner.run_experiment` mutation half left inside — that's an empty body by another name. The elegant fix is the row above: **extract the 4 eval primitives to `eval_primitives.py`, then delete `experiment_runner.py` entirely.** `experiment_runner.py` is TIER_IMMUTABLE → its deletion is **Tier-3-gated** (the only Tier-3 item the no-shim end-state adds beyond `meta_evolution`/`shinka_engine`).

### Result
The verified engine stands alone: `planning.py` (plan) → evolver worktree (implement) → `eval_primitives.py` + `worktree_eval` (measure/judge) → `change_request` (gate), with `evo_memory` as its memory and `variant_archive` as its telemetry. **No facade, no stub, no empty endpoint.** Scope is larger than the facade approach (real planner reimpl + eval-primitive extraction + API/React trim + 1 extra Tier-3), but it is what "logical, no empty bodies" requires. Phasing unchanged (0 → 1 → 1.5 → 2 → 3); the rewire lands in Phase 2, deletions in Phase 3.

---

## ★ Validation re-scan (2026-06-02) — supersedes the buckets/phases below where noted

A full adversarial re-scan (3 agents + direct reads) **confirmed the plan is import-safe, SubIA-safe, and decade-autonomy-safe**, and surfaced **2 corrections + 1 critical live remediation**.

**Confirmed safe:**
- **No boot ImportError.** Every production importer of every retirement target is a *lazy in-function* import wrapped in `try/except`, or lives in the dead aggregator packages `evolution_suite/` + `feedback_suite/` (zero prod importers). `main.py`/`idle_scheduler.py` have **no** module-level imports of any target.
- **All `api/evolution_api.py` routes** wrap evolution reads in `try/except` → degrade to empty, never 500. (`/meta`, `/engine` go empty post-retirement — document so the dashboard isn't misread.)
- **`variant_archive.add_variant(...)` exists** (`variant_archive.py:88`) with exactly the fields the verified engine has (hypothesis, change_type, fitness_before/after→delta, test_pass_rate, status, files_changed) — Phase-0.2 wiring is real.
- No non-idle trigger of island/parallel/meta exists — unregistering the idle jobs fully stops them.

**Correction 1 — `shinka_engine` HOLLOW, not delete.** `coding_session/evolution_bridge.py:432` (the kept inline-ShinkaEvolve feature, default-ON) imports `shinka_engine._map_llm_models`. Keep that symbol (or move it into `evolution_bridge.py`); delete the rest.

**Correction 2 — `modification_engine` is OUT OF SCOPE.** It is a *separate, feedback-driven* subsystem (not the evolution stack), it is **TIER_IMMUTABLE**, and it is gated: Tier-2 changes need **owner Signal approval** (`awaiting_approval`/`reject_tier2`), Tier-1 auto-promotes but is **rate-limited** (10/day, 30/wk). My earlier "same ungated risk as island" was wrong — drop it from this plan; flag the Tier-1 auto-promote path for a *separate* review.

**Correction 3 — `evolution.py` + `evo_memory.py` HOLLOW (not delete); planner RELOCATE (mandatory).**
- `evolution.py` (5 lazy keep-surface importers) → hollow to a facade keeping `run_evolution_session` (→delegate to `run_verified_session`), `_build_evolution_context`, `get_journal_summary`, `_select_evolution_engine`, `_get_subia_safety_value`, `_is_shinka_available`. (Bonus: ~5 tests `open("app/evolution.py")` — keeping the file avoids FileNotFoundError.)
- `evo_memory.py` → keep `recall_similar_successes` (used by `backup_planner.py`).
- **Mandatory:** relocate `_phase_planning` + `_build_evolution_context` into a new `self_improvement/planning.py`. If *deleted* instead, `orchestrator._plan_target` silently returns `None` forever → the verified engine can never pick a target (silent functional regression). `experiment_runner` + `pattern_library` stay (the planner imports them).

**★ CRITICAL ADDITION — Phase 1.5: audit/rollback the already-promoted live prompts.** Stopping `island_evolution` does **not** undo what it already promoted. The live agent prompts are island-evolution products, **never human-reviewed**, all promoted 2026-05-27 at `fitness=1.000` (a suspiciously-maxed LLM-judge score): **`coder=v116`, `commander=v121`, `researcher=v111`, `writer=v107`**. Operator-authored `v001.md` (seeded from `app/souls/<role>.md`) exists for each. The plan **must** surface these for an operator decision: (a) review-and-approve current versions, or (b) `prompt_registry.rollback(role, to_version=1)` to baseline and re-introduce improvements via the verified path. Reversible (registry keeps every version). **This is the highest-impact item for "is the system better off"** — without it the system keeps running on unreviewed auto-evolved prompts.

**Safe deletion order (no intermediate ImportError):** (1) delete `evolution_suite/` + `feedback_suite/` packages + scrub their `system_inventory/__init__.py` mentions; (2) create `self_improvement/planning.py`, repoint `_plan_target`; (3) hollow `evolution.py`/`shinka_engine.py`/`evo_memory.py`; (4) remove the island/parallel/meta/legacy-`evolution` `jobs.append` lines in `idle_scheduler.py`; (5) delete leaves `avo_operator`/`island_evolution`/`parallel_evolution`/`meta_evolution`/`mutation_strategies`/`deploy_staging`/`cascade_evaluator`; (6) KEEP `experiment_runner`/`eval_sandbox`/`sandbox_runner`/`variant_archive`/`evolution_roi`/`evolution_db`/`map_elites`/`pattern_library`/`prompt_registry`/`workspace/meta/*`.

**Test/CI impact:** ~15 test files import the legacy modules (lazy → test-level failures, not collection errors); 5 also `open()`/`read_text()` the sources (FileNotFoundError if deleted — mitigated for `evolution.py` by hollowing). Update/remove in the same PR. Not a runtime risk.

**Operational precondition:** autonomous verified runs only produce CRs if the evolver image is built (`docker compose --profile evolver build evolver`, CLAUDE.md §73). Failure mode is graceful (gate returns an error string, no crash). Honest caveat: post-retirement, autonomous self-improvement cadence = "verified CRs only" — lower volume than the old loop, but every one is real + operator-gated (the old loop's volume was noise).

**Net after re-scan: still STRONGER, and with Phase 1.5 it materially improves live safety *today*.** No boot break, no SubIA break, no decade-autonomy break, all endpoints safe.

---

## 0. What exists today (verified)

Four self-improvement loops run, only one gated to the verified path:

| Loop | Trigger | Target | Verification / gate | Verdict |
|---|---|---|---|---|
| Verified engine (`self_improvement/`) | main `evolution` job + executor adapter | code (`.py`) | execution + immutable judge + **operator CR** | **keep — the survivor** |
| AVO main loop (`evolution.run_evolution_session` body) | same job, when switch OFF | code (broken) + skills | `eval_set_score` that never imported the change | retire |
| `island_evolution` (idle job `:2228`) | independent HEAVY idle | **live agent prompts** | **LLM-judge fitness > 0.7 → `promote_version` (NO operator gate)** `island_evolution.py:704-724` | **retire — safety hole** |
| `parallel_evolution` (idle job `:2240`) | independent HEAVY idle | prompt variants | scratch only (inert) | retire |
| `meta_evolution` (idle job `:1606`) | independent HEAVY idle | `workspace/meta/*` params | 3/week cap; also runs the broken loop `meta_evolution.py:300-305` | retire |

**The safety finding:** `island_evolution` (and `modification_engine.py:287,437`, same path) promote mutated prompts straight into `workspace/prompts/<role>/active.txt` — the live prompt `get_active_prompt` serves to agents — gated only by a noisy LLM-judge score, no operator approval. This is the one thing in the system that mutates live agent behaviour without the CR gate. Retiring it is the single biggest reason consolidation makes the system **safer**.

---

## 1. Constraint check — SubIA: **NO negative impact** (verified)

- **No `app/subia/*` file imports the evolution loop.** The only `subia → self_improvement` imports (`subia/self/competence_map.py:60-139`) target the **keep** modules (gap/skill registry), not the legacy loop; all are `try/except`-wrapped. `subia/tsal/evolution_feasibility.py` is SubIA-internal, unrelated.
- **Coupling is inversion-of-control, one-way:** SubIA *publishes* the kernel; the loop *reads* it — `get_active_kernel().homeostasis["safety"]` (`evolution.py:910,1586`) and the surprise-targeting `accuracy_tracker` read (`evolution.py:283`). SubIA consumes **no** loop output as a dependency.
- The single loop→SubIA write — `store_causal_belief(source="evolution_session")` (`evolution.py:1266`) — has **no SubIA reader** that filters on it. Safe to drop.
- **Integrity manifest:** `subia/integrity.py` enumerates only `app/subia/*.py`. Every retirement target is top-level `app/*.py` → **zero manifest impact, no regen.**
- SubIA idle jobs (`reverie`/`understanding`/`shadow`/`backward-replay`) and the `affect/goal_emitter ↔ companion.grand_task` dedup are **fully independent** of the evolution stack.

**SubIA must-preserve list (small):** (1) keep the surprise-bridge `accuracy_tracker` read — it lives in `_build_evolution_context`, a *keep/extract* target; carry it into the new planner. (2) Optionally carry the kernel-safety aggressiveness modulation into the verified engine. Nothing else.

## 2. Constraint check — decade-autonomy: **display-only degradation, one fix** (verified)

Every consumer of legacy evolution telemetry is failure-isolated and degrades to empty — **nothing breaks or asserts presence:**
- `variant_archive` readers — `alignment_audit.py:223`, `api/evolution_api.py:159` (`/variants`), `observability/publishers.py:462`, `firebase/publish.py:952`, `commander/commands.py:550` (`variants` cmd), `knowledge_compactor.py:240` — all `try/except` → empty.
- `evolution_roi`: its only *enforcement* consumer (`should_throttle`, `evolution.py:885`) is **inside the retired loop** → no orphan. `goodhart_guard`'s `rollback_silence` signal just goes dormant.
- Continuity ledger / `annual_reflection` / `drift_digest`: **neither the legacy loop nor the verified engine emits evolution events today** → retiring changes nothing (pre-existing latent gap).
- `self_improvement/velocity.py` + `metrics.py` (the verified engine's own dashboards): CR-based, **unaffected**.
- **The decade-autonomy self-improvement trigger already uses the verified engine:** `autonomous_executor/scheduler_job.py:156-160` (`make_self_improvement_adapter`) → `run_verified_cycle`.
- No healing monitor or resilience drill asserts the evolution path ran (`cron_liveness` tracks `self_improve` via `SelfImprovementCrew`, out of scope; `silent_regression_detector`/`pattern_learner` don't exist as files).

**The one required decade-autonomy action:** the verified engine writes **nothing** to `variant_archive`/`improvement_narrative`/continuity today (only the CR). Wire `variant_archive.add_variant(...)` into `run_verified_cycle` per filed CR (it has approach/target/diff + a **real measured delta** from `verdict.evidence`) so `/variants`, the Signal `variants` command, publishers, firebase, and alignment_audit stay populated — with *better* (execution-grounded) data than the old noise.

---

## 3. Buckets: keep / extract / retire

**KEEP (used outside the loop — do not delete):** `variant_archive`, `evolution_roi`, `evolution_db` (eval-sets/judge), `map_elites` + `map_elites_wiring` (Commander prompt stochasticity, drift, dashboards), `experiment_runner` **eval primitives** (`validate_response`/`load_test_tasks`/`eval_set_score`/`verify_eval_integrity` — live deps of `goodhart_guard:112`, `proposals:633`, `external_benchmarks:522`), `eval_sandbox`, `sandbox_runner`, `prompt_registry` (operator/CR-driven prompt versioning stays).

**EXTRACT (sever the verified engine's dependency on to-be-deleted files):** the planner — `orchestrator._plan_target` currently imports `avo_operator._phase_planning` + `evolution._build_evolution_context` (`orchestrator.py:153-180`). These are **entangled** (importing `_build_evolution_context` pulls `ExperimentRunner` via `evolution.py`'s module top-level). Reimplement the target-selection + context-gathering (incl. the SubIA surprise read) in a new `app/self_improvement/planning.py`.

**RETIRE** *(see ★ re-scan for the corrected hollow-vs-delete split — `evolution.py`, `shinka_engine`, `evo_memory` are HOLLOWED not deleted; `modification_engine` is OUT OF SCOPE)*: AVO body in `evolution.run_evolution_session`, `avo_operator`, `island_evolution`, `parallel_evolution`, `meta_evolution`, `cascade_evaluator`, `deploy_staging`, `mutation_strategies`. Plus delete the dead `evolution_suite/` + `feedback_suite/` packages first. **Do NOT delete `experiment_runner`** — keep it as the eval-primitive home; just make its mutation half (`ExperimentRunner.run_experiment`) unreferenced.

**Approval table (verified against `auto_deployer.py` + `self_quarantine.py`):**

| File | Protection | To retire/edit needs |
|---|---|---|
| `evolution.py`, `avo_operator.py` | TIER_GATED (`:223`) | operator override / canary |
| `island_evolution.py`, `parallel_evolution.py`, `cascade_evaluator.py` | TIER_GATED (`:224,:226`) | operator override / canary |
| `experiment_runner.py`, `eval_sandbox.py` | **TIER_IMMUTABLE** (`:80`) | **KEEP** (don't delete) |
| `meta_evolution.py` (`:128`), `shinka_engine.py` (`:132`), `modification_engine.py` (`:95`) | **TIER_IMMUTABLE** | **Tier-3 amendment** to delete |
| `sandbox_runner.py`, `eval_sandbox.py` | **self_quarantine** (human PR only) | **KEEP** |
| `mutation_strategies.py`, `evo_memory.py`, `deploy_staging.py` | unprotected | normal PR |
| `idle_scheduler.py`, `runtime_settings.py`, `self_improvement/*`, `variant_archive.py`, `improvement_narrative.py` | not in IMMUTABLE/GATED lists — **confirm TIER status, treat as normal process** | normal PR |

---

## 4. Phased migration (each phase shippable, reversible, and ordered so the high-value/low-risk work lands first)

### Phase 0 — Standalone + telemetry parity (zero deletions, zero TIER_IMMUTABLE touch, fully reversible)
0.1 Create `app/self_improvement/planning.py`; reimplement target-selection (port `_build_evolution_context` data-gathering **including the SubIA surprise read `evolution.py:283`** + `_phase_planning`'s LLM/meta-prompt logic). Repoint `orchestrator._plan_target` to it. → verified engine now imports **none** of the retirement targets.
0.2 Wire `variant_archive.add_variant(...)` into `run_verified_cycle` per filed CR (decade-autonomy parity). Optional: `evolution_roi.record_evolution_cost`; optional new `continuity_ledger` `self_modification` event (closes a pre-existing narrative gap).
0.3 Repoint `improvement_narrative` to read `change_requests/audit.jsonl` + `self_improvement/velocity` (or mark it for retirement in favour of `velocity_digest`).
0.4 Flip code default `evolution_verified_engine_enabled = True` (`runtime_settings.py:317`) so fresh installs never run the broken loop (operator's live value is already `true`).
**Verify:** verified engine plans+runs with no import of `evolution.py`/`avo_operator`; `/variants` shows verified runs. **Rollback:** revert the import + default.

### Phase 1 — Neutralize the ungated live mutators (the safety win; no file deletion)
1.1 Unregister the `island-evolution`, `parallel-evolution`, `meta-evolution` idle jobs (delete the 3 `jobs.append(...)` at `idle_scheduler.py:1606,2228,2240`). Stops the ungated `promote_version` path immediately.
1.2 **`modification_engine` is OUT OF SCOPE** (re-scan Correction 2): it is a separate, TIER_IMMUTABLE, feedback subsystem — Tier-2 owner-Signal-gated, Tier-1 rate-limited. Leave it; flag its Tier-1 auto-promote for a *separate* review.
**Verify:** the three island/parallel/meta idle jobs no longer registered; live prompts no longer mutated by `island_evolution`. **Rollback:** re-add the appends.

### Phase 1.5 — ★ Audit/rollback the already-promoted live prompts (live-safety remediation)
`island_evolution` already promoted unreviewed prompts to live `active.txt`: `coder=v116`, `commander=v121`, `researcher=v111`, `writer=v107` (all `fitness=1.000`, 2026-05-27). For each role, the operator either **approves the current version** or **rolls back to baseline** (`prompt_registry.rollback(role, to_version=1)`), after which improvements re-enter via the verified path. Reversible — the registry retains every version. **Do not skip:** stopping island freezes these prompts live; it does not review them.

### Phase 2 — Route both triggers through the verified engine
2.1 In `evolution.run_evolution_session`, make the delegation unconditional (always `return run_verified_session(...)`; delete the AVO body below the gate). `evolution.py` is TIER_GATED → operator override/canary.
2.2 The 6h cron (`main.py:549`) already calls `run_evolution_session` → now always delegates; no change needed.
**Verify:** neither cron nor idle executes any AVO/`experiment_runner` mutation code.

### Phase 3 — Delete dead files (after a soak; respects the approval table)
- Normal PR: `mutation_strategies.py`, `evo_memory.py`, `deploy_staging.py` (once `planning.py` is independent of them).
- Operator override/canary: `avo_operator.py`, `island_evolution.py`, `parallel_evolution.py`, `cascade_evaluator.py`, `evolution.py` body.
- **Tier-3 amendment:** `meta_evolution.py`, `shinka_engine.py`, `modification_engine.py`.
- Update `auto_deployer.py` TIER lists + remove dead imports. **Keep** `experiment_runner`, `eval_sandbox`, `sandbox_runner`.

---

## 5. The three "lost" capabilities — explicit decisions

1. **Skill-file authoring** (`change_type="skill"`, `skills/*.md`): the verified engine drops non-`.py` plans. Re-home as a thin proposer feeding the CR gate (belongs with `proposal_bridge`/companion ideation), **or drop** (its output was noise).
2. **Prompt/soul evolution:** if wanted, rebuild to feed the **operator-gated CR pipeline** with real verification (not LLM-judge auto-promote). Default recommendation: **drop the auto path** — operator-authored prompt CRs are safer and already supported by `prompt_registry`.
3. **Meta-evolution:** **drop.** Letting the engine rewrite its own planning/judging prompts undercuts the immutable-judge property that makes the verified engine trustworthy.

---

## 6. Net effect
- **Safety:** closes the ungated live-prompt-mutation hole (island/modification_engine) and the broken-loop auto-deploy path; every self-modification now goes through execution-verification + operator CR.
- **Cost/noise:** stops 3–4 LLM loops that produced noise + "borderline" alerts.
- **Clarity:** one engine, one gate, one audit trail; no dual-default footgun.
- **SubIA:** unaffected (verified). **Decade-autonomy:** preserved by the single `variant_archive.add_variant` wiring in Phase 0.2.

**Recommended first step:** execute **Phase 0** (standalone + telemetry parity + default-on) — zero deletions, zero TIER_IMMUTABLE touches, fully reversible — then **Phase 1** (the safety stop). Phases 2-3 are governance-gated cleanup that can soak.
