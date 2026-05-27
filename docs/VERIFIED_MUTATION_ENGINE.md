# Verified Mutation Engine (2026-05-27)

Replaces the legacy self-improvement loop (`evolution.py` → `avo_operator.py` →
`experiment_runner.py`) with one that **proves a self-modification works by
executing it** before ever proposing it. Default OFF behind
`evolution_verified_engine_enabled`; always operator-gated (no auto-deploy).

## Why the old loop was replaced

A "borderline mutation" alert (Δ +0.0133, `research_crew.py`) was traced to a
pipeline that was structurally incapable of producing working code mutations:

1. **Generation = whole-file regeneration from a truncated view.** AVO's
   implementer read the target file truncated to 8000 chars
   (`avo_operator.py:257`) while telling the LLM to "return the COMPLETE file,
   preserve all functionality" at `max_tokens=8192`. For a 24 KB file the model
   couldn't see it all or emit it all, so it regenerated the canonical CrewAI
   scaffold from its training prior — silently dropping the `.run()` entry point
   every caller depends on.
2. **Gates were blind to semantics.** Local testing was `ast.parse` + a
   dangerous-import scan; self-critique judged only the first 500 chars and
   failed open. A clean-looking scaffold passed both.
3. **The delta was structurally noise.** The "primary" signal (`eval_set_score`,
   70 % weight) called the agent LLM directly and **never imported the changed
   file**; the mutation was applied to a `workspace/` shadow path that was never
   on the import path. No code-type experiment ever ran the code it scored, so
   `+0.0133` was the difference between two noise samples. `delta > 0` → "keep" →
   "borderline" → routed to the operator (the only real check).

## Core principle

> A self-modification is a coding task the system performs on itself. It must
> clear the **same bar as any code change**: it imports, its tests pass, its
> public contracts hold, and it demonstrably beats a held-out benchmark —
> **verified by execution, not guessed.**

The engine's only self-specific jobs are (1) decide *what* to change and (2) own
the immutable benchmark + judge that define "improvement." Everything else is
the coding substrate that already existed (`coding_session`, `code_intel`,
`change_requests`).

## The pipeline (4 stages)

| Stage | Module | Role | Tier |
|---|---|---|---|
| Ground | `change_spec.py` | Truncation-free contract: complete source + public API + external callers (blast radius) + covering tests + executable preservation assertions, via `code_intel` | OPEN |
| Implement | `verified_implementer.py` | Open a real git worktree, apply a focused edit, run real `pytest` (covering tests + a synthesized API-preservation smoke test) via `iterate_until_green`; returns `green` / `api_broken` / `tests_red` / `edit_failed` | OPEN |
| Evaluate | `worktree_eval.py` | Invariants-first verdict. Correctness delta (baseline-vs-candidate failing tests, deterministic) + optional quality delta (held-out benchmark through the real entry point, **paired**, effect-size + win-consistency floor). Verdicts: `REJECT` / `IMPROVED` / `INVARIANTS_ONLY` / `NO_CHANGE` | **TIER_IMMUTABLE** |
| Compose | `pipeline.py` | Wires the above end-to-end → `PipelineResult` (verdict + diff + changed-file contents) | OPEN |

How each old failure mode dies structurally:
- **Scaffold** → the API-preservation smoke test fails to import / lacks `.run`
  on iteration 1 → `api_broken`. (`tests/test_verified_implementer.py::test_scaffold_that_drops_api_is_caught`)
- **Semantic blindness** → verification is real `import` + real `pytest` + pyright + API assertion, not `ast.parse`.
- **Noise-as-signal** → a within-noise quality delta (mean below the effect size
  and/or mixed per-task wins/losses) reads `NO_CHANGE`, never `IMPROVED`.
  (`tests/test_worktree_eval.py::test_within_noise_quality_delta_is_not_improvement`)

## Safety architecture — the load-bearing invariant

> **Generation is mutable and self-improvable. Judgement is immutable and fixed.**

- **OPEN** (improvable): `change_spec`, `verified_implementer`, `pipeline`,
  `evolver_job`, `evolver_spawn`, `orchestrator`. The system can get better at
  *proposing and implementing*.
- **TIER_IMMUTABLE** (cannot be agent-edited): `worktree_eval.py` (registered in
  `auto_deployer.TIER_IMMUTABLE`) and the held-out benchmark directory
  (`app/self_improvement/benchmarks/`, a forbidden prefix in
  `change_requests/validator._FORBIDDEN_PATH_PREFIXES`). An engine that could
  rewrite its own judge — or pad its own exam — could lower its own bar.

## Container topology — "everything in Docker, no host processes"

`runner.run()` is in-process `subprocess` (not bridge-aware), so the worktree
and the test-runner must be **co-located in one container**. Execution therefore
runs in an **ephemeral evolver container**, spawned per run by the gateway via
the existing `docker-proxy` (already `CONTAINERS`/`IMAGES`/`POST` enabled — no
proxy change).

```
┌─ gateway ─────────────────────────┐        ┌─ evolver (ephemeral) ─────────────────┐
│ • ground (change_spec over /app)  │  spawn │ • git worktree (Local backend, /app)   │
│ • orchestrator: gate + file CRs   │ ─────▶ │ • implement + iterate_until_green      │
│ • operator approval (Signal/cp)   │ ◀───── │ • worktree_eval (run benchmark)        │
└───────────────────────────────────┘ verdict└────────────────────────────────────────┘
```

- **Image** (`Dockerfile.evolver`): built from the repo *including `.git`* +
  pytest; working tree materialized from HEAD (lean — no `workspace/` bloat).
  Co-located `/app` layout so the `code_intel` index path and `coding_session`
  defaults work unchanged. Built under the `evolver` compose profile so
  `docker compose up` never starts it.
- **Constraints respected**: `VOLUMES: 0` on the proxy → no host bind-mounts. The
  job spec goes in via `AAI_EVOLVE_JOB` env; the verdict comes back via
  sentinel-wrapped stdout in the container logs (`<<<EVOLVER_RESULT>>>…<<<EVOLVER_END>>>`).
- **Editor**: anchored search/replace (`evolver_job.make_anchored_editor`) — can
  only replace exact, unique substrings, so it *cannot* regenerate a whole-file
  scaffold, and its output is tiny (no truncation). This is the robust answer to
  both old failure modes at once.

## Activation routing

- **Idle/autonomous**: `evolution.run_evolution_session` hard-cuts to
  `orchestrator.run_verified_session` when the switch is on (returns on error —
  no silent fallback to the broken legacy path).
- **Operator-initiated / "route via autonomous_executor"**:
  `orchestrator.make_self_improvement_adapter` (wired into
  `autonomous_executor.scheduler_job` with a safe fallback) dispatches a run
  whose step description is a JSON job (`{target_file, approach, budget_usd}`)
  deterministically through `run_verified_cycle` — giving the executor's audit /
  budget / `/cp/delegate` visibility while still running the verified pipeline
  (not a prose Commander step). `orchestrator.enqueue_self_improvement` creates
  such a run.

## Operator surfaces

- **`/cp/settings`** → `VerifiedEngineCard`: master toggle + per-cycle budget
  (`evolution_verified_engine_enabled`, `evolution_verified_per_cycle_budget_usd`;
  dispatcher branches in `config_api.py`).
- **Change-requests** carry real evidence, not a noise delta — e.g. "fixed N
  test(s) / benchmark Δ=… (k↑/j↓) / +X−Y lines / API preserved", filed one per
  changed file through the standard operator gate.

## Held-out benchmark

`app/self_improvement/benchmarks/*.json` (TIER_IMMUTABLE). Each task's `input`
runs through the real entry point in **both** the baseline and candidate
worktree and is LLM-judged (DGM-separated model). A change is `IMPROVED` on the
quality axis only when the candidate beats baseline by the immutable effect size
**and** wins on more tasks than it loses. Ships empty (only `README.md` + a
`.template`) — most changes land `INVARIANTS_ONLY` (correctness proven, operator
decides) until the operator curates tasks. See `benchmarks/README.md`.

## Activation steps

1. `docker compose --profile evolver build evolver` (rebuild alongside the
   gateway so its baked HEAD matches the deployed code).
2. Flip `evolution_verified_engine_enabled` → true (in `/cp/settings`, or
   `runtime_settings.set_evolution_verified_engine_enabled(True)`).

Default OFF → zero production impact until both are done.

## Tests

`tests/test_{change_spec,verified_implementer,worktree_eval,pipeline,evolver_job,evolver_spawn,orchestrator}.py`
— 58 host tests (real git + real pytest for the worktree paths; injected stubs
for LLM/Docker). Pydantic-gated edits (`evolution.py`, `orchestrator.py`,
`scheduler_job.py`, `auto_deployer.py`, `validator.py`, `config_api.py`,
`runtime_settings.py`) are `py_compile`-verified on host; exercised in CI/Docker.

## Files

New: `app/self_improvement/{change_spec,verified_implementer,worktree_eval,pipeline,evolver_job,evolver_spawn,orchestrator}.py`,
`app/self_improvement/benchmarks/{README.md,research_crew.example.json.template}`,
`Dockerfile.evolver`, `dashboard-react/src/components/VerifiedEngineCard.tsx`,
plus 7 test files.
Edited: `evolution.py` (hard-cut guard), `auto_deployer.py` (+worktree_eval
immutable), `change_requests/validator.py` (+benchmarks forbidden prefix),
`autonomous_executor/scheduler_job.py` (adapter routing), `runtime_settings.py`
(+2 keys), `api/config_api.py` (+2 dispatcher branches), `docker-compose.yml`
(+evolver service + `EVOLVER_IMAGE`), `dashboard-react/src/api/queries.ts` +
`SettingsPage.tsx`.
