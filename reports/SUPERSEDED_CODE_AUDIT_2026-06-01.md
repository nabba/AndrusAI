# AndrusAI — Superseded / Replaced Code Audit

**Date:** 2026-06-01
**Repo / branch:** `crewai-team` @ `feat/gateway-serving-split`
**Scope:** Find parts of the system that have been replaced — fully or partially — by newer parts. Every claim verified against source.
**Method:** Whole-repo import-reference graph (26,000 import lines / 2,145 files) → production-vs-test importer split for suspects → 7 parallel verification sub-audits (LLM, evolution/self-improvement, self-healing, drills/monitors, storage, dashboards/companion, orphan sweep) → direct re-verification of every load-bearing claim by the author. Citations are `file:line`.

---

## 0. The one thing to understand first

This system almost never *deletes* superseded code. The dominant pattern is **additive, gated supersession**: a new subsystem is built in front of the old one, the old one is left in place behind a default-OFF (or operator-flippable) switch, and a comment explains why it's retained ("flip back on for debugging without resurrecting deleted modules"). That is a deliberate property of the safety model (reversibility, TIER_IMMUTABLE protection, governance ratchets).

**Consequence:** "replaced" here usually means **SUPERSEDED-DORMANT** (old code still on disk, sometimes still wired, but the new path is what runs when enabled), *not* "deleted." The set of genuinely-deleted code is small and confined to two recent hardening passes (the LLM consolidation and the ChromaDB dual-writer fix).

A second structural finding: there was an **aborted "package-boundary" refactor** — several re-export façade packages (`adaptation/`, `control/`, `request/`, `llm_suite/`, `evolution_suite/`, `operations_suite/`, `feedback_suite/`) were created to give "clean boundaries" around the three control planes and the LLM/evolution layers, but callers kept importing the underlying modules directly. These façades are now dead weight.

---

## 1. CRITICAL config nuance — the self-improvement engine

The single most consequential old-vs-new fact in the codebase:

| | Value | Evidence |
|---|---|---|
| **Code default** | `evolution_verified_engine_enabled = False` | `app/runtime_settings.py:317`, getter `:3601-3603` |
| **Operator's live value** | `true` | `workspace/runtime_settings.json:102` |
| **The gate** | hard-cut, **no fallback** | `app/evolution.py:866-879` |

```python
# app/evolution.py:866
_verified_on = get_evolution_verified_engine_enabled()
...
if _verified_on:
    return run_verified_session(max_iterations=max_iterations)   # NEW verified engine
    # on error: "do not fall back to legacy" — returns an error string
# ...falls through to the LEGACY AVO/experiment_runner mutation path
```

**Interpretation:**
- On **this operator's running machine**, the switch is ON → the new Verified Mutation Engine (`app/self_improvement/`) runs and the legacy AVO/`experiment_runner`/`evo_memory` mutation chain is bypassed.
- On a **fresh install / the code default**, the switch is OFF → the *structurally-broken* legacy loop (truncated 8 KB whole-file rewrites, `ast.parse`-only gates, keep-signal that never imported the changed file — see `app/self_improvement/change_spec.py` docstring) is what actually executes.

So the legacy self-improvement stack is best described as **"superseded-by-design but still the shipped default."** It is dormant *only because the operator flipped a non-default switch.* This is the highest-priority item in the report.

### 1.1 Deep re-verification (2026-06-01)

Re-checked end-to-end because this finding is load-bearing. All four sub-claims hold:

1. **One chokepoint, two unconditional triggers** — `run_evolution_session` (`evolution.py:842`) is reached by (a) an APScheduler cron `id="evolution"`, default `EVOLUTION_CRON="0 */6 * * *"`, added unconditionally at `main.py:549` (`scheduler.start()` at `:698`); and (b) an idle HEAVY job at `idle_scheduler.py:1595-1598` — a plain `jobs.append(("evolution", _evolution, …))` with **no `if` gate** (`idle_scheduler.start()` at `main.py:761`). Neither is gated by an evolution-specific master switch (the only kill switch is the Firestore `background_tasks` one, moot at `FIREBASE_ENABLED=0`). So the legacy loop is genuinely *reached*, not merely present.
2. **Hard-cut gate** — the verified-engine check is the *first* statement inside `run_evolution_session` (`evolution.py:866-878`): `if _verified_on: return run_verified_session(...)`; on exception it returns an error string and explicitly does **not** fall back.
3. **Default vs live** — default `False` (`runtime_settings.py:317`); `_load()` (`:1069-1082`) merges disk over defaults ("disk wins for known keys"). The running gateway reads `/app/workspace/runtime_settings.json` (`WORKSPACE_ROOT` default `/app/workspace`, `paths.py:22`, **no env override** in compose/.env; bind-mount `./workspace:/app/workspace`, `docker-compose.yml:103`) → that file's line 102 is `true`.
4. **ON-path is real** — `run_verified_session` exists (`self_improvement/orchestrator.py:183`) and has produced a real CR (§76), so the live machine runs the verified engine rather than erroring into the no-fallback `except`.

Live state at audit time: `crewai-team-gateway-1` was `Up`, so this is the actual running configuration, not hypothetical. **Net:** operator's live gateway → verified engine; any default-config deployment → the broken legacy loop, actively scheduled (every 6h + each idle slot), burning LLM spend and emitting noisy "borderline" alerts.

---

## 2. Category A — GENUINELY DELETED / fully replaced (clean removals)

These are gone from the tree and have **zero live importers**. Verified via `git ls-files` + repo-wide grep.

### 2.1 LLM consolidation → OpenRouter + Ollama only (commits `4968bd76`, `dbcb43a1`, `be930d23`; ancestors of HEAD — already merged, *not* still on an unmerged branch as CLAUDE.md implies)

| Deleted artifact | Replaced by | Evidence |
|---|---|---|
| `app/llms/credit_aware_anthropic.py` | `app/llms/budget_aware.py` (`BudgetAwareCompletion`, per-call cap) | untracked; deleted in `4968bd76` |
| `app/prompt_cache_hook.py` (litellm monkeypatch) | `app/llm_cache_control.py` (in-path `cache_control` injection, `budget_aware.py:136`) | untracked; deleted in `dbcb43a1` |
| `AnthropicClientHandle`, `anthropic_client_for_role` | factory path via OpenRouter (`llm_factory.chat_completion_for_role`) | 14 repo refs, **all comments/docstrings** — 0 live `import` |
| `_build_claude_llm` | `_build_anthropic_entry` emits `provider=openrouter` (`llm_catalog_builder.py:336,478`) | grep: no matches |
| `"anthropic"` runtime **mode** | `RUNTIME_MODES = (free, budget, balanced, quality, insane)` | `llm_catalog.py:111` |

The single native-Anthropic island that remains is **intentional and correctly fenced**: `app/computer_use/` + `app/tools/computer_use_tool.py` (needs the `computer-use-2025-01-24` beta OpenRouter can't proxy). `get_anthropic_api_key()` is called in exactly one place: `computer_use/runner.py:270`. The pinning test `tests/test_llm_factory_route_invariants.py::...test_no_anthropic_sdk_imports_outside_factory` genuinely enforces this (3 regexes, fails with file:line) and reality shows **0 violations**.
*Note:* `litellm` is **not** dead — it is the live OpenAI-compatible transport for OpenRouter (`llm_factory.py:837`, `requirements.txt:35`). The consolidation removed the *native-Anthropic dialect + monkeypatch*, not litellm.

### 2.2 ChromaDB dual-writer corruption fix (PROGRAM §55)

| Removed | Replaced by | Evidence |
|---|---|---|
| Orphan `chromadb` HTTP-server **container** in compose | embedded `PersistentClient` only | `docker-compose.yml:117-118, 233-248` (removal documented; no service block) |
| `chromadb.HttpClient(` usage (7 modules) | `get_client()` / `get_client_for_path()` | **0 hits** of `chromadb.HttpClient(` in `app/`; pin `tests/test_subsystem_wiring.py:385` |
| Per-`KnowledgeStore()` client creation (the 8 GB OOM leak) | path-keyed client cache | `app/memory/chromadb_manager.py:303-321` (`get_client_for_path`, recycle-aware); fix commit `3d6a7f77` (2026-06-01) |

### 2.3 In-place rewrites (old buggy logic deleted, not gated)

- **Mem0 embedder** `huggingface → ollama` (§83, `7ab6d054`): `app/memory/mem0_manager.py:155-167`; no leftover HF embedder config; pin `tests/test_mem0_provider_selection.py::TestMem0EmbedderBackend`. Plus CPU-only torch `ARG TORCH_VARIANT=cpu` (`Dockerfile:30-35`).
- **migration_drill** redesign (2026-05-16): the old logic that "walked `migrations/0*_*.sql` against a fabricated `_schema_migrations` table" is **gone** — no grep hit for `_schema_migrations` anywhere. `deploy/scripts/migration-drill.sh:201,211` now drills the production code path (`startup_migrations.apply_all`); the Python monitor is a freshness-watcher only.
- **`retention.py`** 3 confirmed destructive bugs fixed in place (`app/healing/monitors/retention.py:106-107, 275-421, 462-506` — timestamp-less records excluded from deletion, `_validate_worktree_path()`, `_ATT_SAFE_PREFIXES`).
- **`chromadb_hygiene`** orphan-scan bug fixed: now queries `segments.id` not `collections.id` (`app/healing/monitors/chromadb_hygiene.py:338`, with the incident documented at `:322-338`).
- **Resilience drills v1 → v2** (PROGRAM §60): all 11 drills migrated to the `DrillResult` v2 contract via the single `runner.invoke_drill()` orchestrator; the old "past-due auto-run" hot-loop and per-drill `append_result`/lock/landmark calls are gone (the one remaining direct `append_result` in `kill_the_gateway.py:276` is a deliberate external-report ingestion path, not a v1 remnant).

---

## 3. Category B — SUPERSEDED-DORMANT (old code retained behind a gate; new path is canonical)

These are the heart of "old replaced by new." The new implementation exists and is the intended path; the old code is still on disk and often still *wired*, but inert under current/normal configuration.

### 3.1 The legacy evolution / self-improvement stack

Superseded by the **Verified Mutation Engine** (`app/self_improvement/` — `change_spec.py`, `verified_implementer.py`, `worktree_eval.py`, `orchestrator.py`). Governed by the §1 switch.

| Module | Status | Live wiring today | Notes |
|---|---|---|---|
| `app/evolution.py` | legacy core, **default-live / dormant-when-switch-on** | `idle_scheduler.py:1597` (`evolution` HEAVY job), `main.py:550`, `commands.py:343/353` | the AVO body after the §1 gate |
| `app/avo_operator.py` | mutation path superseded; **planner reused** | `evolution.py` + `self_improvement/orchestrator.py:153-180` | new engine reuses only `_phase_planning` (target selection); discards the broken `content[:8000]` mutator |
| `app/experiment_runner.py` | **PARTIALLY replaced** — see §4.1 | goodhart_guard, proposals, external_benchmarks | mutation/keep loop dead; eval primitives still load-bearing |
| `app/island_evolution.py` | SUPERSEDED-DORMANT | `idle_scheduler` HEAVY job | population variant |
| `app/parallel_evolution.py` | SUPERSEDED-DORMANT | `idle_scheduler` HEAVY job | MAP-Elites archive variant |
| `app/meta_evolution.py` | SUPERSEDED-DORMANT | `idle_scheduler` `meta-evolution` HEAVY job; `evolution_api` read | second-order; TIER_IMMUTABLE |
| `app/modification_engine.py` | SUPERSEDED-DORMANT | `idle_scheduler` `modification-engine` MEDIUM job | TIER_IMMUTABLE |
| `app/shinka_engine.py` | vestigial-but-wired | reachable only if `_is_shinka_available()` (the `shinka` pkg is an optional `--no-deps` install, normally absent) | inline `coding_session/evolution_bridge.py` is the maintained ShinkaEvolve path |
| `app/sandbox_runner.py` | SUPERSEDED-DORMANT | only reachable via `experiment_runner` (legacy chain) | `recovery/strategies/sandbox_execute.py` reimplements the Docker logic; verified engine uses ephemeral evolver containers |
| `app/deploy_staging.py` | SUPERSEDED-DORMANT | single caller in the cut-off legacy branch (`evolution.py:784`) | `auto_deployer` + host deploy webhook are current |
| `app/evo_memory.py` | legacy support | evolution, avo_operator, pattern_library, backup_planner | backs the legacy loop |
| `mutation_strategies.py`, `evolve_blocks.py` | legacy support | within legacy/island loops | — |

**Top-level `ShinkaEvolve/` directory:** present at the *outer* path (`/Users/andrus/BotArmy/ShinkaEvolve`) but **not in this repo branch**; the inline bridge is the live path.

### 3.2 Other gated-dormant supersessions

- **`app/firebase/` (whole package) + `app/firebase_reporter.py` + `firebase-service-account.json`** — vestigial-by-default. `_firebase_enabled()` defaults `FIREBASE_ENABLED=0` (`app/firebase/infra.py:31-40`); `_get_db()` returns `False` so all 40+ `report_*` calls and listeners short-circuit. Superseded by **Postgres `control_plane.*` + `app/observability/` + the React dashboard's `/api/cp/*` HTTP reads**. `firebase_reporter.py` is itself just a back-compat re-export shim into `app/firebase/`.
  - Concrete dead-by-default paths: Firestore chat (`report_chat_message`, `start_chat_inbox_poller`) → replaced by `POST /cp/chat/send`; Firestore task tracking → replaced by `control_plane.crew_tasks`.
  - **Cleanup wart:** ~9 Firestore pollers are still started unconditionally at boot (`main.py:714-752`) and spin 3 s poll loops forever purely to satisfy the `listener_heartbeat` monitor's "known listener must touch its heartbeat" contract — idle daemon threads doing nothing useful when `FIREBASE_ENABLED=0`.

---

## 4. Category C — PARTIALLY replaced (new exists; old still load-bearing or additive)

Items where a naive "delete the old thing" would break production.

### 4.1 `experiment_runner.py` — mutation half dead, eval half live
The "structurally broken" critique applies to the *mutation/keep loop*, not to the module's **eval primitives**. These are sound and live:
- `goodhart_guard.py:112` → `validate_response` (live `goodhart-check` idle job)
- `proposals.py:633` → `load_test_tasks`, `validate_response`
- `external_benchmarks.py:522` → `validate_response`

`experiment_runner.py` must stay (also TIER_IMMUTABLE, `auto_deployer.py:80`). Retiring the legacy loop must **re-home these helpers**, not delete the file.

### 4.2 `briefing_evolution/` is ADDITIVE, not a replacement
The old static composers (`app/life_companion/daily_briefing.py:_compose_morning/_evening/_weekly`) remain the **primary** structure; the dynamic-section subsystem appends adopted sections to the **tail** (`daily_briefing.py:1104-1148`, try/except-isolated). No old composer was removed.

### 4.3 Belief outbox reconcilers are ADDITIVE
`app/memory/belief_outbox.py` is a Postgres→Neo4j *convergence reconciler* layered on top of the still-live fire-and-forget direct-write path in `subia/belief/store.py`. Dual-write + reconcile is the intended architecture, not old-vs-new.

### 4.4 `anomaly_detector.py` — newer monitor builds ON it
`app/observability/error_monitor.py:431-438` *reuses* `anomaly_detector`'s alert deque/window. The newer error monitor extends rather than replaces it.

### 4.5 `auditor.py` — older error-resolution loop running in PARALLEL
`auditor.py` runs two live APScheduler crons (`code_audit` 4 h `main.py:534`, `error_resolution` 30 min `main.py:540`). Its `run_error_resolution` overlaps conceptually with the newer `healing/error_diagnosis` + `observability/error_monitor`, but both run. **Consolidation opportunity, not a clean supersession.**

### 4.6 Settings dispatcher — registry + 2 intentional explicit branches
The `~100` hand-written `if key in payload:` branches were replaced by the declarative `_build_setter_registry()` (`app/api/config_api.py:306`). Two explicit branches remain **by design** (typed-phrase gates: `person_correlation_social_graph_enabled` `:760`, `graph_suggestions_enabled` `:778`); other `if key in payload` hits are in *separate* endpoints. This is the correct final state, not leftover debt.

---

## 5. Category D — DEAD / orphaned (removable; not half of a gated pair)

Genuinely unreferenced by production code. Safe-to-remove candidates (subject to the TIER_IMMUTABLE caveat in §7).

| Module / file | Why dead | Superseded-by | Confidence |
|---|---|---|---|
| **`main.py`** (top-level) | docstring literally says **"Stale top-level entry point — DO NOT USE."** | `app/main.py` | HIGH |
| **`app/adaptation/`** | re-export façade of `app.governance`; **0 external importers** | callers import `app.governance` directly | HIGH |
| **`app/control/`** | re-export façade of `app.idle_scheduler`; **0 external importers** | callers import `app.idle_scheduler` directly | HIGH |
| **`app/request/`** | re-export façade of `Commander`+`vetting`; **0 external importers** | callers import those directly | HIGH |
| **`app/llm_suite/`** | 27-line re-export façade; **0 importers** (app + tests) | direct factory/selector imports | HIGH |
| **`app/evolution_suite/`** | re-export façade; only importer is a `system_inventory` docstring / `test_island_evolution` | direct imports | HIGH |
| **`app/operations_suite/`** | re-export façade; **0 production importers** | direct imports | HIGH |
| **`app/feedback_suite/`** | re-export façade; **0 production importers** | — | HIGH |
| **`app/meta_learning.py`** | imported only via dead `feedback_suite` | `app/self_improvement/meta_agent/` | MED-HIGH |
| **`app/implicit_feedback.py`** | imported only via dead `feedback_suite` | — | MED-HIGH |
| **`app/lazy_imports.py`** | **0 importers** (the `_lazy_imports` in `browse_api.py` is an unrelated local fn) | circular-import problem solved differently | HIGH |
| **`app/contracts/`** | architectural-reference Python; only `firestore_schema` imported by **one test**; `firestore_schema` self-describes as runtime-unused | — | HIGH |
| **`app/cascade_evaluator.py`** | only importer is the dead `evolution_suite` façade | verified engine `worktree_eval.py` | MED (TIER_IMMUTABLE) |
| **`app/differential_test.py`** | no runtime caller; appears only in TIER lists + a refactoring-proposer string + a test | — | MED (TIER_IMMUTABLE) |
| **`dashboard/public/index.html`** | ~400-line vanilla-JS "Agent Monitor"; copied into image (`Dockerfile:108`) but **never mounted** | React SPA at `dashboard/build` (`main.py:3413`) | HIGH |
| **`dashboard/server.mjs`, `firebase.json`** (legacy Firebase Hosting) | unused by the gateway (no import/mount/serve) | React SPA + FastAPI static mount | HIGH |
| **`docker-compose.firecrawl.yml`** | **0 references** in any script/code/compose; only mentioned in markdown | — (abandoned self-host experiment) | HIGH |

Façade cluster verdict: `adaptation/ control/ request/ llm_suite/ evolution_suite/ operations_suite/ feedback_suite/` are the residue of the aborted package-boundary refactor — pure re-exports nobody imports. Removing them is zero-risk to the import graph (all re-exported symbols are imported directly from source elsewhere).

---

## 6. False alarms — look old, are actually ACTIVE (do **not** delete)

These score low on a naive `app.<module>` import count but are live; listed so a cleanup pass doesn't break the system.

- **`app/liveness.py`, `app/boot_state.py`** — the *newest* generation (2026-06-01 watchdog / boot-starvation fixes), wired at `main.py:349` and the idle warm-up gate (`from app import boot_state`).
- **`fault_isolator.py`, `failure_taxonomy.py`, `confidence_tracker.py`, `backup_planner.py`, `failure_modes.py`** — each has only one importer (`lifecycle_hooks.py`) but register as **immutable lifecycle hooks** fired by the live Commander on every task/LLM/tool/delegation/error (`lifecycle_hooks.py:509-572`).
- **`health_monitor.py`** — boot-initialised + `health-evaluate` idle job; different surface from `healing/monitors/`.
- **`circuit_breaker.py`** — foundational LLM-cascade primitive (9 live importers).
- **`eval_sandbox.py`** — live via `modification-engine` idle job + `training_pipeline` safety probes (TIER_IMMUTABLE).
- **`chaos_tester.py`, `pattern_library.py`, `reference_tasks.py`, `knowledge_compactor.py`, `canary_deploy.py`** — each on a live idle-scheduler or auto_deployer path.
- **`middleware.py`, `cron/`, `settings_genealogy.py`, `tool_hook_bridge.py`, `llm_factory_probe.py`, `llm_provider_classify.py`, `llm_rehydrate.py`, `llm_registry_scanner.py`, `llm_completion_guard.py`, `ollama_native.py`, `llm_mode.py`, `app/llms/`** — all single-but-live imports on the boot/LLM hot path.
- **`map_elites.py`, `map_elites_wiring.py`, `variant_archive.py`, `evolution_roi.py`, `evolution_db/`** — used well outside the evolution loop (Commander prompt stochasticity, drift scoring, ROI throttle, eval infra); **not** safe to sweep up in a "retire old evolution code" pass.
- **`recovery/`** — the current refusal-recovery subsystem (`orchestrator.py:3086,3639`); coexists with `healing/` by design.
- **`proactive/`** — narrow but live post-task trigger scanner (`commander/execution.py:188`); distinct role from `companion/` and `life_companion/`.
- **`conversation_store.py` vs `conversation_memory/`** — distinct roles (rolling SQLite message window vs non-vector audit-log recall index); both live.

---

## 7. Recommended cleanup (prioritized)

**Tier 1 — zero-risk deletions (no production importer, not protected):**
1. Top-level `main.py` (self-declared stale orphan).
2. Façade cluster: `app/adaptation/`, `app/control/`, `app/request/`, `app/llm_suite/`, `app/operations_suite/`, `app/feedback_suite/`, `app/evolution_suite/` — and with them `meta_learning.py`, `implicit_feedback.py` (only reachable through the dead façades).
3. `app/lazy_imports.py`; `app/contracts/` (move to docs if still useful).
4. `dashboard/public/index.html` + `dashboard/server.mjs` + `firebase.json` (legacy Firebase Hosting); `docker-compose.firecrawl.yml`.

**Tier 2 — needs a decision, not just a delete:**
5. **Decide the self-improvement default.** Either flip `evolution_verified_engine_enabled` to default `True` in `runtime_settings.py:317` (so fresh installs don't run the broken legacy loop), or formally retire the legacy AVO chain. If retiring: re-home `experiment_runner`'s eval primitives (`validate_response`, `load_test_tasks`) first (§4.1).
6. **Consolidate the error-resolution loops** — `auditor.run_error_resolution` (30-min cron) vs `healing/error_diagnosis` + `observability/error_monitor` run in parallel (§4.5).
7. Once #5 lands, retire the SUPERSEDED-DORMANT evolution siblings (`island_evolution`, `parallel_evolution`, `meta_evolution`, `modification_engine`, `sandbox_runner`, `deploy_staging`, `shinka_engine`, `evo_memory`) and unregister their idle jobs.
8. Decommission firebase: either set the pollers to not spawn when `FIREBASE_ENABLED=0` (kill the ~9 idle heartbeat threads) or delete the package.

**Tier 3 — TIER_IMMUTABLE / governance-gated (operator approval required):**
9. `cascade_evaluator.py`, `differential_test.py`, `modification_engine.py`, `experiment_runner.py`, `eval_sandbox.py` are in `auto_deployer.TIER_IMMUTABLE` and/or `governance_amendment/self_quarantine.py`. Even the dead ones (`cascade_evaluator`, `differential_test`) require editing those protected lists, which is an explicit operator action per the safety invariant.

**Minor / cosmetic:**
10. Stale docstrings/comments referencing deleted LLM machinery (`llm_mode.py:20` still describes the removed `anthropic` mode; comments in `llm_catalog.py`, `llm_factory.py`, `budget_aware.py`, `logging_filters.py` reference deleted `credit_aware_anthropic`/`prompt_cache_hook`/`anthropic_client_for_role`).
11. CLAUDE.md says the LLM consolidation is on an "unmerged branch" — it is in fact merged into this branch's history.
12. Route the one startup-only un-cached `chromadb.PersistentClient(` at `app/knowledge_base/business_store.py:134` (`discover_existing`) through `get_client_for_path` for consistency with the recycle path (low severity — one transient client per boot).

---

## 8. Appendix — verification ledger (author-confirmed, not agent-reported)

| Claim | Confirmed at |
|---|---|
| verified-engine default `False` | `app/runtime_settings.py:317` |
| live operator value `true` | `workspace/runtime_settings.json:102` |
| hard-cut gate, no fallback | `app/evolution.py:866-879` |
| `adaptation/control/request` are re-export façades | `app/{adaptation,control,request}/__init__.py` |
| `llm_suite` is a 27-line façade | `app/llm_suite/__init__.py` |
| top-level `main.py` self-declares stale | `main.py:1` |
| firebase default OFF, single chokepoint | `app/firebase/infra.py:31-56` |
| only `dashboard/build` (React) mounted at `/cp` | `app/main.py:3407-3414` |

Sub-audit agents independently verified the LLM-consolidation deletions (git ls-files), the evolution-stack import graph, the self-healing hook wiring, the drill v1→v2 migration, the storage-layer migrations, and the orphan sweep. Where two agents disagreed (e.g. `differential_test` DEAD vs LOW-BUT-LIVE), the conservative classification is used and the nuance is stated inline.
