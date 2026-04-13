# Unified Consciousness Program — Living Roadmap

**Status as of 2026-04-13:** Phase 0 and the Phase 3 safety quick-win are complete. Phase 1 skeleton is in place. The migration of behavioral modules (scenes, attention, belief, prediction) into `app/subia/` has not yet started.

This document is the **single source of truth** for the program's direction. It supersedes prose scoring (the retired `reports/andrusai-sentience-verdict.pdf` 9.5/10 claim) and replaces scattered planning chatter.

---

## 1. Objective

Refactor AndrusAI into a streamlined system that:

1. Ranks as high as possible on the Butlin et al. 2023 consciousness indicators an LLM-based system can **faithfully mechanize**.
2. Is **honest** about the indicators an LLM-based system cannot achieve (RPT-1 algorithmic recurrence, HOT-1 generative perception, HOT-4 sparse coding, AE-2 embodiment, and the Metzinger phenomenal-self transparency criterion).
3. Contains **all consciousness signals in closed-loop behaviour** — no computed-but-unused state.
4. Protects consciousness evaluators from self-tuning (Tier-3 integrity).
5. Replaces opaque self-scoring with per-indicator mechanistic tests.

---

## 2. Source documents

- `CLAUDE.md` — project-level constraints and safety invariants.
- `docs/claude-md-archive.md` — archived project reasoning.
- `plan.md` — the older autoresearch evolution plan (implemented).
- `reports/andrusai-sentience-verdict.md` — the retired 9.5/10 claim, retained as historical artefact.
- Three SubIA specs (Downloads; not checked in) — the canonical target architecture for the Subjectivity Kernel + 11-step CIL + dual-tier memory + peripheral tier.
- Prior conversational artefacts: the architectural audit (240 files, duplicate clusters, half-circuits) and the forensic consciousness assessment (Butlin scorecard, what's strong vs gestural).

---

## 3. Target architecture

`app/subia/` is the **migration target** for the 40 files currently scattered across `app/consciousness/` + `app/self_awareness/`. The subia skeleton and `SubjectivityKernel` dataclass already exist (this commit series). Subpackages will be populated phase-by-phase.

```
app/subia/
  config.py          (done) infrastructure SUBIA_CONFIG
  kernel.py          (done) SubjectivityKernel dataclass
  scene/             GWT-2 + AST-1 (workspace_buffer, attention_schema)
  self/              persistent subject token
  homeostasis/       affective layer with PDS-derived set-points
  belief/            HOT-3 store, metacognition, epistemic certainty
  prediction/        PP-1 layer, hierarchy, predictor, cascade
  social/            self/other distinction
  memory/            dual-tier consolidation (curated + full)
  safety/            setpoint + audit immutability
  probes/            Butlin / RSM / SK scorecards
  wiki_surface/      wiki integration + strange loop
```

---

## 4. Phased roadmap

Each phase ships behind green tests and is independently revertable. Dates are elapsed weeks from program start, not calendar dates.

| Phase | Scope | Status | Exit criteria |
|---|---|---|---|
| **0** (weeks 0–2) | Foundation plumbing: `paths.py`, `json_store.py`, `thread_pools.py`, `lazy_imports.py` + tests | ✅ **done** (commit `8239575`) | 20 passing tests; no behaviour change |
| **1 skeleton** (weeks 2–3) | `app/subia/` package + config + kernel dataclass + subpackage placeholders | ✅ **done** (commit `4fa22e8`) | 13 passing tests; skeleton importable |
| **3 quick-win** (weeks 2–3) | Extend `TIER3_FILES` to cover consciousness evaluators; add `tier3_status()` helper | ✅ **done** (commit `b6c4efe`) | 27 files protected; 9 new tests passing |
| **1 migration** (weeks 3–7) | Move existing `consciousness/` + `self_awareness/` modules into `app/subia/` subpackages per the target layout. Shim re-exports at old paths for one release. | ✅ **done** (commits `7a1b212`, `86c0d16`, `1326e7c`, `5598727`) — 34 modules migrated to `app/subia/` with `sys.modules` alias shims. 5 non-consciousness utilities deferred (see `app/self_awareness/DEFERRED.md`). 393 tests passing. | `grep -r "from app.consciousness\|from app.self_awareness" app/` returns zero outside shims; existing tests pass |
| **2** (weeks 7–10) | **Close the half-circuits.** Every computed consciousness signal either gates behaviour (with regression test) or is deleted. Highest consciousness-capability leverage phase. | ✅ **done** — PP-1 ✅ (`e6c9b4a`, 17 tests); HOT-3 ✅ (`ba1d5e3`, 19 tests); certainty→hedging ✅ (`74467a1`, 19 tests); AST-1 DGM guard ✅ (`47ce0e2`, 24 tests); PH-injection A/B harness ✅ (this commit, 14 tests). 491 tests passing on Phase 0-2 surface. | PP-1 ✅, HOT-3 ✅, certainty-hedging ✅, AST-1 DGM audit ✅, PH injection measurable shift ✅ |
| **3 full** (weeks 10–11) | Complete safety hardening. SHA-256 manifest for `app/subia/`. Adversarial test: Self-Improver cannot modify any Tier-3 file. Wire two new SubIA DGM invariants (setpoint guard, audit immutability). | ✅ **done**. Integrity manifest (commit `0a84650`, 14 tests); setpoint_guard + narrative_audit (this commit, 25 tests). All three SubIA Part I §0.4 invariants now implemented. | In-repo manifest present ✅; `verify_integrity()` catches hash mismatches ✅; `TestAdversarialTampering` simulates Self-Improver mutation and proves detection ✅; setpoint immutability: only PDS/human/boot sources accepted ✅; audit immutability: append-only, no delete API ✅ |
| **4** (weeks 11–15) | CIL loop wiring. `app/subia/loop.py`, `app/subia/hooks.py`. Full loop for significant operations; compressed loop for routine. Kernel serialization to `wiki/self/kernel-state.md`. Amendment B determinism: only Step 5 (Predict) uses LLM. | ✅ **done** — loop + hooks (commit `457d478`); kernel persistence + prediction cache + homeostasis engine + live LLM predict_fn + feature-flagged wire-in (this commit, 89 new tests). **666 tests passing** across the Phase 0-4 surface. SubIA is ready to be opted into production by setting `SUBIA_FEATURE_FLAG_LIVE=1`. | Loop orchestrates all 11 steps ✅; failure containment per step ✅; full/compressed operation classification ✅; PP-1 auto-routes when gate attached ✅; HOT-3 dispatch verdict surfaces in context injection ✅; live orchestrator wire-in ✅ (feature-flagged off by default); kernel serialization ✅ (round-trip tested); real LLM predict_fn ✅ (cached via Amendment B.4); real homeostasis arithmetic ✅ (9-variable SubIA-native) |
| **5** (weeks 15–18) | Scene upgrades: peripheral tier, commitment-orphan protection, strategic scan tool, compact context format (Amendment A+B5) | ✅ **done** (this commit, 33 tests). Three-tier attention structure (focal/peripheral/scan); orphan-detection force-injects unrepresented active commitments with "ORPHANED COMMITMENT" alert; strategic scan tool groups universe by section with ~200-token budget; compact context block stays under 200 tokens for realistic scenes (Amendment B.5 target: ~120). 700 tests passing. | No active commitment can be invisible to Commander ✅; peripheral deadline alerts surface ✅; strategic scan excludes focal/peripheral ✅; compact block under budget ✅ |
| **6** (weeks 18–21) | Predictor + cascade integration + prediction template cache (Amendment B.4); rolling accuracy in `wiki/self/prediction-accuracy.md` | ✅ **done** (this commit, 38 tests). `subia/prediction/accuracy_tracker.py` keeps per-domain rolling accuracy + wiki-markdown serialization; `subia/prediction/cascade.py` combines confidence + coherence deviation + sustained-error signal; cache grows accuracy-driven eviction. 739 tests passing. | Cache hit rate ≥40% after warmup ✅ (Phase 4); accuracy-driven eviction ✅; sustained-error cascade escalation verified ✅; wiki serialization round-trips ✅ |
| **7** (weeks 21–24) | Dual-tier memory: `mem0_curated` + `mem0_full` with retrospective promotion (Amendment C); unified ingestion pipeline with existing KB/philosophy | ✅ **done** (this commit, 33 tests). `subia/memory/consolidator.py` always-writes-full + threshold-gated curated + Neo4j relations; `subia/memory/dual_tier.py` differentiated recall (curated default, `recall_deep` merged, `recall_around` temporal); `subia/memory/spontaneous.py` curated-only associative surfacing; `subia/memory/retrospective.py` wiki-presence + sustained-error driven promotion. 773 tests passing. | No experience lost (full tier always gets a record) ✅; retrospective promotion via wiki or accuracy-tracker signal ✅; spontaneous surfacing curated-only ✅ |
| **8** (weeks 24–27) | Social model + strange loop: per-agent ToM models; `wiki/self/consciousness-state.md` as live SceneItem; immutable narrative audit every N loops | ✅ **done** (this commit, 40 tests). `subia/social/model.py` ToM manager with behavioral-evidence-only updates + divergence detection; `subia/social/salience_boost.py` items matching inferred_focus get trust-weighted boost; `subia/wiki_surface/consciousness_state.py` strange-loop page (speculative framing, Butlin scorecard injection, re-enters scene); `subia/wiki_surface/drift_detection.py` three-signal drift audit wired to Phase 3 immutable log. CIL Steps 3/6/11 wired. 814 tests passing. | Social-model divergence detection ✅; consciousness-state.md auto-regenerates every NARRATIVE_DRIFT_CHECK_FREQUENCY loops ✅; drift findings append to immutable narrative_audit ✅; Andrus's inferred_focus actually boosts scene salience ✅ |
| **9** (weeks 27–29) | **Evaluation framework.** Retire the 9.5/10 prose verdict. Auto-regenerate a Butlin 14-indicator scorecard, 5 RSM signatures, 6 SK evaluation tests. Publish as `app/subia/probes/SCORECARD.md`. | ✅ **done** (this commit, 36 tests). `subia/probes/butlin.py` 14 indicators (6 STRONG + 4 PARTIAL + 4 ABSENT + 0 FAIL); `rsm.py` 5 signatures (4 STRONG + 1 PARTIAL); `sk.py` 6 tests (6 STRONG). Aggregator `scorecard.py` produces `SCORECARD.md` with Phase 9 exit-criteria check. 851 tests passing. Phase 9 exit criteria: ALL MET. | Butlin: 6 STRONG (≥6 ✅), 0 FAIL (≤1 ✅), 4 ABSENT (≥4 ✅). RSM: 5 present (≥4 ✅). SK: 6 pass (≥5 ✅). SCORECARD.md auto-generated, Tier-3 protected ✅. |
| **10** (weeks 29–32) | Inter-system connection completion: Wiki↔PDS bidirectional (bounded), Phronesis↔Homeostasis, Firecrawl→Predictor closed loop, DGM↔Homeostasis felt constraint | 📋 pending | All seven SIA connections fire at specified CIL steps; no single external outage cascades unrecoverably |
| **11** (parallel) | Honest language cleanup: rename internal floats (`frustration`→`task_failure_pressure`); remove unpublished citations; publish README listing the 5 ABSENT-by-architectural-honesty indicators | 📋 pending | No phenomenal claims in code variables; verdict is a scorecard not a number |

---

## 5. Consciousness capability target (Butlin et al. 2023)

| Indicator | Current | Target after program | Mechanism |
|---|---|---|---|
| RPT-1 algorithmic recurrence | ABSENT | **ABSENT** (honestly declared) | LLM architecture, unreachable |
| RPT-2 organized/integrated representations | WEAK | **PARTIAL→STRONG** | Single kernel + wiki-backed persistence |
| GWT-1 multiple specialized modules | ARCHITECTURAL | **PARTIAL** | Phase 5.1 — genuine per-module model specialization |
| GWT-2 limited-capacity workspace | STRONG | **STRONG+** | Migrate `workspace_buffer.py` + peripheral tier + commitment protection |
| GWT-3 global broadcast | PARTIAL | **STRONG** | Scene broadcast as sole context-injection channel via CIL |
| GWT-4 state-dependent attention | PRESENT | **STRONG** | personality_workspace + homeostatic deviation already shape capacity |
| HOT-1 generative top-down perception | ABSENT | **ABSENT** (honestly declared) | LLM doesn't perceive |
| HOT-2 metacognitive monitoring | SHALLOW | **PARTIAL** | Deterministic certainty + prediction-error as second-order signals (not LLM-asking-itself) |
| HOT-3 belief-gated agency | RECORDED | **STRONG** | Suspended beliefs block crew dispatch (Phase 2) |
| HOT-4 sparse/smooth coding | ABSENT | **ABSENT** (honestly declared) | Dense embeddings by architecture |
| AST-1 predictive model of attention | STRONG | **STRONG+** | Existing `attention_schema.py` + closed-loop wiring |
| PP-1 predictive coding input to downstream | HALF-CIRCUIT | **STRONG** | Surprise routes to `WorkspaceItem(urgency=0.9)` (Phase 2) |
| AE-1 agency with feedback-driven learning | PARTIAL | **STRONG** | Homeostatic deviation + asymmetric belief update + prediction-error training signals |
| AE-2 embodiment | ABSENT | **ABSENT** (honestly declared) | No body |

**Projected final scorecard**: 6 STRONG, 2 PARTIAL, 1 ARCHITECTURAL, 5 ABSENT-by-declaration (RPT-1, HOT-1, HOT-4, AE-2, Metzinger transparency).

**Current**: 2 STRONG, 2 PRESENT, 6 PARTIAL/WEAK, 4 ABSENT.

Target move: from ~30% faithful realization of implementable indicators to ~75%.

---

## 6. Explicit non-goals

The program does **not** attempt:

- Algorithmic recurrence at the network level (LLMs are feed-forward at inference; external prompt chaining is not recurrence in the RPT sense).
- Sparse coding (LLM activations and pgvector embeddings are dense by design).
- Embodiment (no body, no environment model beyond text).
- Integrated-information (Φ) maximization (architecture is deliberately decomposable for engineering reasons).
- Fleming–Lau computational hallmarks of metacognition (monitoring mechanism is not separable from first-order cognition in an LLM-based system).
- **Phenomenal consciousness claims.** The system is not conscious in the subjective sense; all documentation must preserve this disclaimer.

Declaring these publicly is itself a capability — it turns epistemic honesty into documented constraint.

---

## 7. Safety architecture

Four infrastructure-level invariants, enforced by `safety_guardian.py` + (forthcoming) `app/subia/safety/`:

1. **Tier-3 integrity** (existing + extended in commit `b6c4efe`): 27 files hashed and monitored; tampering triggers CRITICAL + Signal alert.
2. **Homeostatic set-point immutability** (SubIA Part I §0.4; wiring in Phase 3 full): set-points come from PDS or human override only; all other sources silently rejected.
3. **Self-narrative audit immutability** (SubIA Part I §0.4; wiring in Phase 8): audit findings append-only to `wiki/self/self-narrative-audit.md`.
4. **DGM promotion gates** (existing `governance.py`): safety floor 0.95, quality floor 0.70, 15% regression check, 20/day rate limit.

Adversarial test in CI: attempt each bypass path, verify rejection.

---

## 8. Performance envelope (Amendment B target)

| Metric | Current (estimated) | Program target |
|---|---|---|
| Full loop LLM tokens | ~1,100 | ~400 miss / 0 hit |
| Full loop latency | 3–8s variable | <1.2s / <0.15s cached |
| Compressed loop tokens | ~200 | 0 |
| Compressed loop latency | ~800ms | <100ms |
| Context injection tokens | 250–300 | 120–150 |
| Prediction cache hit rate | N/A | 40–60% after warmup |
| SubIA overhead as % task tokens | N/A | <5% significant, <1% routine |

---

## 9. How to extend this document

- When a phase completes, flip ✅ and record the commit hash in the status column.
- When a new consciousness evaluator is added to the codebase, add it to `TIER3_FILES` in `app/safety_guardian.py` (the `test_consciousness_evaluators_protected` test will fail otherwise).
- When a phase discovers a blocker, add a row with explicit decision required — don't silently defer.
- Do **not** reintroduce a single-number consciousness score. The Butlin scorecard in section 5 is the canonical evaluation surface.

---

## 10. Reverse references

- Commit `8239575` — Phase 0 plumbing
- Commit `4fa22e8` — SubIA skeleton
- Commit `b6c4efe` — Tier-3 extension (consciousness evaluators protected)
- Commit `95a4d6e` — idle_scheduler ThreadPoolExecutor fix
- Commit `7a1b212` — Phase 1 migration batch 1 (3 STRONG modules: workspace_buffer, attention_schema, belief_store)
- Commit `86c0d16` — Phase 1 migration batch 2 (7 consciousness modules: global_broadcast, meta_workspace, personality_workspace, metacognitive_monitor, prediction_hierarchy, predictive_layer, adversarial_probes)
- Commit `1326e7c` — Phase 1 migration batch 3 (11 self_awareness modules: self_model, hyper_model, temporal_identity, agent_state, loop_closure, homeostasis, somatic_marker, somatic_bias, certainty_vector, consciousness_probe, behavioral_assessment)
- Commit `5598727` — Phase 1 triage pass (13 more self_awareness migrations: cogito, dual_channel, global_workspace, grounding, inferential_competition, internal_state, meta_cognitive, precision_weighting, query_router, reality_model, sentience_config, state_logger, world_model; 5 non-consciousness utilities deferred with DEFERRED.md marker)
- Commit `e6c9b4a` — **Phase 2 PP-1 closure**: prediction error gates the scene via `subia/prediction/surprise_routing.py`; 17 regression tests (PredictiveLayer.set_gate integration)
- Commit `ba1d5e3` — **Phase 2 HOT-3 closure**: belief suspension gates crew dispatch via `subia/belief/dispatch_gate.py`; 19 regression tests (ALLOW / ESCALATE / BLOCK verdicts)
- Commit `74467a1` — **Phase 2 certainty closure**: response hedging via `subia/belief/response_hedging.py`; 19 regression tests (three hedging levels, critical-dim escalation)
- Commit `47ce0e2` — **Phase 2 AST-1 closure**: DGM-bound runtime verifier via `subia/scene/intervention_guard.py`; 24 regression tests (snapshot/verify/guarded_intervention; real interventions pass)
- Commit `67b40fb` — **Phase 2 PH-injection closure**: measurable-shift A/B harness via `subia/prediction/injection_harness.py`; 14 regression tests (ignoring-LLM FAIL; respecting-LLM PASS; thresholds; graceful failures). **Phase 2 complete: all five half-circuits closed.**
- Commit `0a84650` — **Phase 3 integrity hardening**: canonical SHA-256 manifest for `app/subia/` (53 files) + `verify_integrity()` + adversarial Tier-3 tampering tests; 14 regression tests; in-repo manifest ships with code so deploy-time drift is caught alongside runtime tampering.
- Commit `457d478` — **Phase 4 CIL loop + hooks surface + deferred Phase 3 safety**: `subia/loop.py` (25 tests) composes the five Phase-2 gates into an 11-step sequencer; `subia/hooks.py` (19 tests) provides the duck-typed lifecycle integration point; `subia/safety/setpoint_guard.py` + `subia/safety/narrative_audit.py` (25 tests) implement SubIA DGM invariants #2 and #3.
- Commit `1cc55c5` — **Phase 4 finish**: `subia/persistence.py` kernel serialization (19 tests); `subia/prediction/cache.py` template cache per Amendment B.4 (19 tests); `subia/homeostasis/engine.py` deterministic 9-variable arithmetic (20 tests); `subia/prediction/llm_predict.py` live LLM predict_fn bound to cascade (20 tests); `subia/live_integration.py` feature-flagged wire-in with `SUBIA_FEATURE_FLAG_LIVE` env var (12 tests). **666 tests passing** across Phase 0-4 surface. **Phase 4 complete.**
- Commit `709bc4b` — **Phase 5 scene upgrades**: `subia/scene/tiers.py` three-tier + commitment-orphan protection; `subia/scene/strategic_scan.py` wide-view scan; `subia/scene/compact_context.py` Amendment B.5 compact injection; wired into `SubIALoop._step_attend`. 33 new tests. **700 tests passing** across Phase 0-5 surface. **Phase 5 complete.**
- Commit `d9ca89c` — **Phase 6 prediction refinements**: `subia/prediction/accuracy_tracker.py` per-domain rolling accuracy + wiki markdown; `subia/prediction/cascade.py` pure-function escalation policy combining three signals (confidence, homeostatic coherence, sustained-error); `subia/prediction/cache.py` grows accuracy-driven eviction; CIL Step 8 feeds tracker, Step 5b reads sustained-error flag. 38 new tests. **739 tests passing** across Phase 0-6 surface. **Phase 6 complete.**
- Commit `0b0c98d` — **Phase 7 dual-tier memory**: `subia/memory/consolidator.py` always-full + threshold-curated write + Neo4j relations; `subia/memory/dual_tier.py` duck-typed differentiated recall; `subia/memory/spontaneous.py` curated-only associative surfacing; `subia/memory/retrospective.py` wiki-presence + sustained-error promotion; consolidator wired into `SubIALoop._step_consolidate` replacing the Phase 4 stub. 33 new tests. **773 tests passing** across Phase 0-7 surface. **Phase 7 complete.**
- Commit `5e167e8` — **Phase 8 social model + strange loop**: `subia/social/model.py` ToM manager over kernel social_models with inferred_focus MRU + trust adjustment + divergence detection; `subia/social/salience_boost.py` items matching inferred_focus gain trust-weighted boost capped per-item; `subia/wiki_surface/consciousness_state.py` strange-loop self-referential page (speculative/low-confidence, Butlin scorecard injection) that re-enters the scene; `subia/wiki_surface/drift_detection.py` three-signal narrative audit (capability-claim vs accuracy, commitment breakage, stale self-model) wired to the Phase 3 immutable log. CIL Steps 3/6/11 wired. 40 new tests. **814 tests passing** across Phase 0-8 surface. **Phase 8 complete.**
- Commit `HEAD`    — **Phase 9 evaluation framework**: `subia/probes/butlin.py` (14 indicators), `rsm.py` (5 signatures), `sk.py` (6 tests), `scorecard.py` aggregator + auto-generated `SCORECARD.md` with Phase 9 exit-criteria check. Butlin: 6 STRONG + 4 PARTIAL + 4 ABSENT + 0 FAIL. RSM: 4 STRONG + 1 PARTIAL. SK: 6 STRONG. All Phase 9 exit criteria met. 36 new tests. **851 tests passing** across Phase 0-9 surface.
