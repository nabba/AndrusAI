# Phases A–E + follow-ups — operator runbook

**Shipped 2026-05-22.** Single source of truth for the post-PIM /
post-§56 productionisation push: 265 task units, ~13k LOC of Python,
~2k LOC of TypeScript, ~310 host-runnable tests, zero TIER_IMMUTABLE
touches, zero new Tier-3 amendments.

Every master switch added here ships **default OFF** unless explicitly
noted otherwise. Failure-isolated patterns throughout: a switched-off
or broken subsystem must never block legitimate work elsewhere.

## At a glance

| Phase | Theme | Headline ship |
|---|---|---|
| A.1–A.4 | Executor + zoning observability | `coding_session_iterate` agent tool · `ExecutorStep.cr_ids` attribution · `pre_classified_zone` parameter · `governance_ratchet` events on widening approval |
| B.1–B.5 | Correctness hardening | `inspect.signature` over TypeError-matching · centralised test stubs in `conftest.py` · `Decimal` arithmetic for caps · `daily_call_cap` semantics · `pydantic_settings` dev pin |
| C.1–C.5 | Code-intel + benchmarks + capability tags | `code_intel_coverage` / `code_intel_deps` tools · benchmark suite (15 YAML tasks, 5 scorers, REST + React) · pyright in Dockerfile + strict-paths · code-intel stats endpoint · 12 tools registered additively |
| D.1–D.3 | Cost ceilings | 4 free-tier feed sources + 2 dependency-radar callers under `@with_connector_budget` · Anthropic per-day USD cap with REST + React + Signal |
| E.1–E.3 | Reusable primitives | `JsonlLedger` (15+ stores' shared shape) · `MasterSwitch` registry · `@switch_gated` decorator (4 real-site migrations) |

## Quick reference — switches added

All names use `runtime_settings.get_<name>()` / `set_<name>()` unless
noted. The React `/cp/settings` page has a card for the ones flagged
"React".

| Name | Default | Notes |
|---|---|---|
| `iterate_loop_enabled` | OFF | A.1. Agent tool `coding_session_iterate`. React: `RecentSubsystemsCard`. |
| `benchmarks_enabled` | OFF | C.3. HEAVY idle pass over 15-task catalog. React: `BenchmarksPage` banner toggle + `RecentSubsystemsCard`. |
| `pyright_sidecar_enabled` | OFF | Phase 3 v2. Sidecar for `iterate_until_green` + `coding_session_submit`. |
| `connector_budgets_enabled` | OFF | Connector primitive. React: `ConnectorBudgetCard`. |
| `code_intel_enabled` | OFF | Symbol index refresh job. |
| `widening_proposer_enabled` | OFF | Phase 4 piece 1. |
| `two_reasoner_review_enabled` | OFF | Phase 4 piece 2. |
| `risk_classifier_enabled` | OFF | Zone-classification of CR paths. |
| `capability_regression_enabled` | OFF | Tool-deletion detector. |
| `fast_route_extended_patterns_enabled` | **ON** | Phase 4 fast-route patterns. |
| `verification_extension_enabled` | OFF | Epistemic gate v2. |
| `autonomous_executor_enabled` | OFF | Background executor. |
| `autonomous_executor_llm_planner_enabled` | OFF | LLM-driven planner v2. |
| `anthropic_daily_cap_usd` | `None` | D.3. Float ≥ 0 to enable; null to disable. **React + REST + Signal `/budgets`.** |
| `PAPER_PIPELINE_PEPS_ENABLED` (env) | **ON** | E.3-migrated to `@switch_gated`. |
| `PAPER_PIPELINE_W3C_ENABLED` (env) | **ON** | E.3-migrated. |
| `PAPER_PIPELINE_HF_ENABLED` (env) | **ON** | E.3-migrated. |
| `PAPER_PIPELINE_OPENREVIEW_ENABLED` (env) | **ON** | E.3-migrated. |

## What shipped per phase

### Phase A — Executor + zoning observability

**A.1 — `coding_session_iterate` agent tool.** Iterate-until-green
exposed as a CrewAI tool with hard caps (≤20 iterations, ≤$5
budget). Master switch `iterate_loop_enabled`. The coder agent can
now call `coding_session_iterate(session_id, target_file, test_argv,
max_iterations, budget_usd)` and get back a structured iteration
record. Composes with the existing pyright sidecar.

**A.2 — Post-step CR attribution.** `ExecutorStep` gained a
`cr_ids: list[str]` field; after each step ends, the driver scans
`change_requests.store.list_all()` for CRs filed by the
`executor:<run_id>:*` requestor in the step's time window and stamps
the IDs back. Surfaces in `delegate.ts` + `DelegatePage.tsx` as yellow
CR badges linking to `/cp/changes`.

**A.3 — `pre_classified_zone` parameter on `create_request`.** The
risk-classifier (Phase 4) can now classify a path **once**, then call
`create_request(..., pre_classified_zone=...)` to skip the
re-classification inside `lifecycle.py`. Pure performance + audit
clarity.

**A.4 — `governance_ratchet` events on widening approval.** When the
operator approves a widening proposal, the resulting list-mutation now
emits a `governance_ratchet` event into the identity continuity ledger
— same event-kind annual reflection already surfaces. Closes the
auditability gap.

### Phase B — Correctness hardening

**B.1 — `inspect.signature` over TypeError matching.** The
`coding_session/iterate.py` `_invoke_diagnosis_fn` previously caught
`TypeError` and tried to detect "unsupported kwarg" via string-match
on the exception message. Replaced with `inspect.signature(fn)` that
introspects accepted parameters and filters kwargs accordingly. Same
behavior, far more robust.

**B.2 — Centralised test stubs in `conftest.py`.** The 50+ test files
that each install module-level psycopg2 + crewai stubs now share one
`tests/conftest.py` that installs the stubs at module-load (not
fixture) time. Stub for crewai's `@tool` decorator carries `.name` +
`.func` for downstream tooling-tests. Fixed cross-pollution between
`test_travel_tools.py` and `test_connector_budget.py`.

**B.3 — `Decimal` arithmetic for caps.** Connector-budget cap arithmetic
moved from `float` to `decimal.Decimal` with `Decimal(repr(value))`
construction (no FP drift). Previously the cap could be hit 1-2 calls
early because `0.10 + 0.005 != 0.105` in float space. Tests tightened
back to exact-integer call counts.

**B.4 — `daily_call_cap` semantics in `@with_connector_budget`.** New
mutually-exclusive `daily_call_cap: Optional[int]` parameter
(XOR with `daily_cap_usd`). For genuinely free APIs (Aviationstack
free tier, OSV.dev, etc.), the cap is on call count not synthetic USD.
Exception carries `today_calls_made` + `daily_call_cap` fields with
a different message format. Aviationstack moved from
`daily_cap_usd=0.003, estimated_cost_usd=0.001` (synthetic) to
`daily_call_cap=3, estimated_cost_usd=0.0`.

**B.5 — `pydantic_settings` pinned as dev extra.** New
`requirements-dev.txt` documents the host-runnable subset.

### Phase C — Code-intel + benchmarks + capability tags

**C.1 — Capability-tag migrations (additive).** 12 previously-unregistered
tools now appear in the tool registry by name, all using EXISTING
capability vocabulary (no Tier-3 amendment introduced):

| Tool | Capabilities |
|---|---|
| `web_fetch` | `searches-web` |
| `firecrawl_scrape` / `_search` / `_extract` / `_map` / `_crawl` | `searches-web` |
| `create_pdf` / `_docx` / `_xlsx` / `_pptx` | `renders-pdf` + `renders-document` |
| `ocr_extract_text` | `reads-attachment` |
| `research_orchestrator` | `searches-web` + `reads-knowledge-base` |

The `Phase C.1 no-new-capability-category` pin is enforced by a test
(`test_tool_registry_migrations_c1.py::TestNoNewCapabilityCategory`).

**C.2 — `code_intel_coverage` + `code_intel_deps` agent tools.** Both
go through the existing `code_intel` symbol index:

- `code_intel_coverage(name, test_root="tests/")` → tests that
  reference `name`. Useful for blast-radius analysis before refactor.
- `code_intel_deps(file_path)` → sorted+deduped module names that
  `file_path` imports. Useful for coupling audit. AST-walks
  `Import` + `ImportFrom` (relative imports kept in dotted form).

`ALL_CODE_INTEL_TOOLS` grew from 4 → 6.

**C.3 — Benchmark suite.** New `app/benchmarks/` package
(`models.py`, `scorers.py`, `store.py`, `catalog.py`, `runner.py`,
`aggregator.py`, `scheduler_job.py`, ~1900 LOC). 15 YAML tasks
across 6 categories (arithmetic, code_understanding, factual_recall,
instruction_following, structured_output, summarization). 5 pure-
function scorers (`exact_match`, `contains`, `regex_match`,
`json_keys_present`, `length_within`) + registry. 5 REST endpoints
under `/api/cp/benchmarks/*`. React `BenchmarksPage` with model
leaderboard sorted by mean score, by-task hardest-first view, refresh
button, window selector (24h / 7d / 30d / 90d). HEAVY idle scheduler
tuple gated by `benchmarks_enabled`.

**C.4 — Pyright in Dockerfile.** `pyright>=1.1.350` installed via pip
during gateway build. `pyproject.toml` `[tool.pyright].strict` list
seeded with `connector_budget`, `capability_regression`, `code_intel`,
`autonomous_executor`, `risk_classifier`.

**C.5 — Code-intel stats endpoint + JSONL design doc.** New
`/api/cp/code-intel/stats` returns rows/bytes/last-indexed-at/age.
Module docstring updated with "Architecture decision: JSONL chosen
over Postgres" section + 5-point rationale + re-evaluation criteria.

### Phase D — Cost ceilings

**D.1 — 4 free-tier feed sources wrapped.** `@with_connector_budget`
on each of the four `fetch_*` in `app/episteme/feed_sources.py`:

| Connector key | Cap |
|---|---|
| `paper_pipeline_peps` | 5 calls/UTC-day |
| `paper_pipeline_w3c` | 5 calls/UTC-day |
| `paper_pipeline_huggingface` | 5 calls/UTC-day |
| `paper_pipeline_openreview` | 5 calls/UTC-day (per pass; inner per-venue fan-out is 1 budget tick) |

Pattern: master switch checked BEFORE budget tick so disabled feeds
don't burn budget. `ConnectorBudgetExceeded` caught and degraded to
`[]` so the rest of the pipeline continues.

**D.2 — Dependency-radar OSV + GitHub callers wrapped.** Two new
connector keys:

| Connector key | Cap |
|---|---|
| `dependency_radar_osv` | 50 calls/UTC-day (OSV.dev `/v1/querybatch` POSTs) |
| `dependency_radar_github` | 500 calls/UTC-day (5× headroom over ~100 direct deps) |

Both wrapped through helper functions (`_budgeted_osv_post`,
`_budgeted_github_get`); the call-site code is unchanged.
`library_radar/proposer.py` consumes JSONL only — no wrap needed.

**D.3 — Anthropic per-day USD cap.** New `app/llm_anthropic_budget.py`
+ `app/control_plane/anthropic_budget_api.py`. Vendor-wide rolling-24h
USD ceiling complementing the existing reactive
`circuit_breaker["anthropic_credits"]`. Setting
`anthropic_daily_cap_usd` to a positive float enables; `None`
disables. Spend is read from `audit_log` so the cap matches what the
React Cost dashboard shows.

**Operator surfaces (D.3)**:
- React: `AnthropicBudgetCard` on `/cp/settings` (set/clear/probe).
- REST: `GET /api/cp/anthropic-budget/state`, `POST /cap`, `POST /pre-check`.
- Signal: `/budgets` slash command now shows the cap above the
  per-connector breakdown, with graded ⚠️ at 75% / 90% utilisation.

**D.3 wired into 5 high-volume call sites** (post-phase):
- `creativity.analogy_populator._default_llm_call` ($0.005/call)
- `brainstorm.idea_evolution._default_mutator` ($0.001/call)
- `brainstorm.idea_evolution._default_judge` ($0.0005/call)
- `inbox.handlers.image_vision.run` ($0.015/call)
- `inbox.handlers.pdf_extract.run` ($0.02/call)
- `qos.answer_regression._default_judge_fn` ($0.001/call)

Pattern (3 lines per site):

```python
from app import llm_anthropic_budget
if not llm_anthropic_budget.call_or_skip(
    estimated_cost_usd=0.005, source="brainstorm:idea_mutator",
):
    return ""  # caller picks its own empty sentinel
# ... proceed with the Anthropic call
```

The 8 remaining Anthropic call sites follow the same pattern; each
takes ~5 minutes to wire when an operator decides it's worth it.

### Phase E — Reusable primitives

**E.1 — `JsonlLedger` (`app/utils/jsonl_ledger.py`).** Collapses the
~15-module-replicated "append-only JSONL + iter + stats +
reset_for_tests" pattern into one generic class. Usage:

```python
_ledger: JsonlLedger[MyRecord] = JsonlLedger(
    name="my_subsystem",
    default_path=lambda: workspace_root() / "subsystem.jsonl",
    rehydrate=MyRecord.from_dict,
    ts_field="ts",  # default
)

_ledger.append(record)
records = _ledger.load_all()
stats = _ledger.stats()      # {"rows": N, "bytes": B, "last_ts": str}
```

Failure-isolated reads (malformed rows skipped with debug log),
thread-safe writes, late-bound path resolution, custom serialise
callbacks. Demo migration: `app/benchmarks/store.py` uses it.

**E.2 — `MasterSwitch` registry (`app/utils/master_switches.py`).**
Declarative DSL for new boolean runtime_settings. Usage:

```python
REGISTRY = SwitchRegistry()
REGISTRY.register(MasterSwitch(
    name="my_subsystem_enabled",
    default=False,
    description="Turn on the my_subsystem idle scheduler tuple.",
))
REGISTRY.bind(globals())  # auto-generates get_/set_ on this module
```

Existing hand-written getter/setter pairs in `runtime_settings.py`
are NOT migrated (zero risk). Use this for NEW settings going
forward.

**E.3 — `@switch_gated` decorator (`app/utils/switch_gated.py`).**
Short-circuits a function when a named runtime_setting / env var is
OFF. Usage:

```python
@switch_gated(
    "PAPER_PIPELINE_PEPS_ENABLED", on_disabled=list, default=True,
)
def fetch_python_peps(*, lookback_days=90, max_items=10):
    # actual work — runs only when ON
    ...
```

Three-source resolution: `runtime_settings.get_<name>()` →
`os.environ[name]` → `default`. Failure-OPEN posture (gate bugs
never block legitimate calls). Demo migrations: 4 feed sources in
`app/episteme/feed_sources.py`.

**`on_disabled` accepts** any value OR any callable (treated as a
factory called fresh each request — `list`, `dict`, `set`, `tuple`,
or custom). Lambda factories are fine. Plain values returned as-is.

## Common operator tasks

### Check current cost ceilings

```signal
/budgets
```

Returns the Anthropic vendor-level cap state (or "DISABLED" line),
then the per-connector breakdown sorted by 7-day spend.

### Set the Anthropic daily cap

React: `/cp/settings` → "Anthropic per-day cap" card → enter USD
value → Save.

REST: `POST /api/cp/anthropic-budget/cap` with body `{"cap_usd": 25.0}`.
Body `{"cap_usd": null}` disables.

### Check what the cap would do

```signal
# In the React AnthropicBudgetCard:
[Probe] estimated_cost_usd=0.50  →  ✅ Would proceed. Headroom after: $X
                                   🚫 Would REFUSE: spent $X of $Y...
```

REST equivalent: `POST /api/cp/anthropic-budget/pre-check` with body
`{"estimated_cost_usd": 0.50}`.

### Enable the benchmark suite

Either flip on `benchmarks_enabled` in `/cp/settings` →
`RecentSubsystemsCard`, or click "Enable" on the `/cp/benchmarks`
status banner. Then "Refresh now" for an immediate pass, or wait
~24h for the scheduled tick.

### Run one operator-initiated benchmark pass

React: `/cp/benchmarks` → "Refresh now" (bypasses both the master
switch and the cadence guard).

REST: `POST /api/cp/benchmarks/refresh?force=true`.

### Check the catalog

React: `/cp/benchmarks` → scroll to "Catalog" section.

REST: `GET /api/cp/benchmarks/catalog`.

### Override a connector budget

Either editor in `/cp/settings` → "Connector budgets" → "Add
override", or PATCH `connector_budget_overrides` via
`POST /api/cp/settings`.

Per-connector cap + estimate keys: `{daily_cap_usd, estimated_cost_usd}`.

### Force a code-intel index rebuild

Operator-initiated: `python -c "from app.code_intel.refresh import
run_refresh; print(run_refresh(force=True))"`. Bypasses both the
master switch and the cadence guard. The result dict reports
`{"ran": True, "stats": {"symbols": N, "references": M, "indexed_files": K}}`.

### Verify connector budgets are doing anything

```signal
/budgets
```

If both blocks say "DISABLED" / "no spend recorded", the subsystem
is dormant. Flip `connector_budgets_enabled` in /cp/settings to enable.

### Approve a Tier-3 amendment

This existed pre-A — but: react `/cp/amendments` is the canonical
surface (Signal-mediated approval is intentionally NOT supported for
Tier-3 changes; the operator must be at the dashboard).

## What is NOT in this push

- **No new TIER_IMMUTABLE files touched.** Every change either lives
  in a non-TIER_IMMUTABLE module or extends an existing
  TIER_IMMUTABLE-adjacent module via additive-only patterns.
- **No new Tier-3 amendments** introduced. The capability vocabulary
  was specifically held constant in C.1.
- **No new IDENTITY_EVENT_KIND.** `governance_ratchet` (A.4) reuses
  an existing kind.
- **No new healing-monitor registrations.** The benchmarks scheduler
  job is HEAVY-tier in idle_scheduler, but not a monitor.

## Tests

**307 host-runnable tests across these phases**, all passing on a
host without pydantic_settings / chromadb / fastapi / crewai /
anthropic SDK installed (those are gateway-env deps).

Test files added:

```
tests/conftest.py                                  # B.2
tests/test_iterate_type_aware_diagnosis.py         # A.1 + B.1
tests/test_coding_session_iterate_tool.py          # A.1
tests/test_executor_cr_attribution.py              # A.2
tests/test_create_request_pre_classified_zone.py   # A.3
tests/test_widening_governance_ratchet.py          # A.4
tests/test_connector_budget.py                     # B.3 + B.4
tests/test_connector_budget_overrides.py           # B.3 + B.4
tests/test_travel_tools.py                         # B.2
tests/test_travel_connector_budget.py              # B.4
tests/test_code_intel_v2_tools.py                  # C.2
tests/test_code_intel_stats.py                     # C.5
tests/test_code_intel_type_check_tool.py           # adjacent
tests/test_coder_code_intel_wiring.py              # adjacent
tests/test_benchmarks_suite.py                     # C.3
tests/test_tool_registry_migrations_c1.py          # C.1
tests/test_phase_d_connector_wraps.py              # D.1 + D.2
tests/test_llm_anthropic_budget.py                 # D.3
tests/test_budgets_command_anthropic.py            # D.3 follow-up
tests/test_anthropic_call_or_skip.py               # D.3 follow-up
tests/test_jsonl_ledger.py                         # E.1
tests/test_master_switches.py                      # E.2
tests/test_switch_gated.py                         # E.3
tests/test_feed_sources_switch_gated.py            # E.3 real-site
```

To run the cumulative regression on host:

```bash
python -m pytest \
  tests/test_jsonl_ledger.py tests/test_switch_gated.py \
  tests/test_master_switches.py tests/test_llm_anthropic_budget.py \
  tests/test_phase_d_connector_wraps.py tests/test_benchmarks_suite.py \
  tests/test_code_intel_v2_tools.py tests/test_code_intel_stats.py \
  tests/test_code_intel_type_check_tool.py tests/test_coder_code_intel_wiring.py \
  tests/test_iterate_type_aware_diagnosis.py tests/test_coding_session_iterate_tool.py \
  tests/test_travel_tools.py tests/test_connector_budget.py \
  tests/test_connector_budget_overrides.py tests/test_travel_connector_budget.py \
  tests/test_tool_registry_migrations_c1.py \
  tests/test_budgets_command_anthropic.py tests/test_anthropic_call_or_skip.py \
  tests/test_feed_sources_switch_gated.py
```

Expected: **307 passed, 38 skipped, 0 failed.**

## Adding a new subsystem — recipe

For someone landing in the codebase later who wants to ship a new
observational subsystem, the canonical pattern is:

1. **Data model**: dataclass(es) with `to_dict` + `from_dict` or
   compatible.
2. **Store**: `JsonlLedger[MyRecord](name=..., default_path=lambda:
   workspace_root() / "subsystem.jsonl", rehydrate=...)`.
3. **Master switch**: declare in `_defaults()` in `runtime_settings.py`
   AND add `get_/set_` pair (or use `MasterSwitch.bind`).
4. **Public entry point**: top-level function gated with
   `@switch_gated("my_setting_enabled", on_disabled=list,
   default=False)`.
5. **REST surface**: new file `app/control_plane/<name>_api.py`,
   register router in `app/main.py`.
6. **React surface** (optional): `dashboard-react/src/api/<name>.ts`
   typed hooks + `<Name>Card.tsx` or `<Name>Page.tsx`.
7. **Tests**: target the rendering / mutating helpers directly with
   injected dependencies, not through the full module imports
   (see `app/agents/commander/budgets_render.py` for the pattern).

## Phase wrap conclusions

- **Cost ceilings are now operator-actionable.** Before this push, a
  runaway loop on any of {Aviationstack, OSV.dev, GitHub, Anthropic
  vendor-wide} would burn through quota / credit without surfacing
  until the next cost-dashboard glance. Now: hard caps at each layer,
  React + REST + Signal visibility, graceful degradation in every
  failure mode.
- **The reusable utilities are exercised.** `JsonlLedger`,
  `MasterSwitch`, `@switch_gated` are not theoretical — each has at
  least one real-site migration with regression tests. Adopting any
  of them in a new subsystem now costs 3–5 lines instead of 50–150.
- **Master-switch hygiene is operator-visible.** Every new bool flips
  through a React card (or appears in `RecentSubsystemsCard`).
  Operators can `grep -rn 'master_switch'` and find the full namespace.
- **Default-OFF discipline.** Every observational primitive ships
  dormant; the operator opts in once they've watched the shape of
  traffic.

## See also

- `crewai-team/CLAUDE.md` — top-level project map.
- `crewai-team/docs/CHANGE_REQUESTS.md` — A.3 zone classification fits
  here.
- `crewai-team/docs/CODING_SESSIONS.md` §15 — A.1 iterate tool lives
  here.
- `crewai-team/docs/CONNECTOR_BUDGET.md` — pre-existing primitive that
  Phases B.3 / B.4 / D.1 / D.2 extend (if absent, this doc serves as
  a fallback reference).
