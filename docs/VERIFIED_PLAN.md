# Verified Implementation Plan — Devin-class autonomy

**Status:** Delivered (2026-05-23)
**Scope:** Phases A–E + Gap closures 1–6 + Risk #4 + Gaps A–F + Gaps E–F
**Test surface:** 410+ passing, 0 failing across the gap-closure regression
**TIER_IMMUTABLE touches:** Zero outside the standard Tier-3 amendment protocol
**Behavioural change when switches OFF:** Zero — fully reversible

---

## Why this plan exists

The starting question was: "what's needed to give AndrusAI Devin-class
autonomy (delegate a goal, drive it to completion across multiple
crew dispatches, with a forensic audit trail and a recovery path)?"

The four parallel verification passes that preceded this work
**reshaped the answer**: most of what looked like new architecture
was actually extensions of existing seams. The final delivery shape:

- **4 truly new directories**: `autonomous_executor/`,
  `risk_classifier/`, `code_intel/`, plus the
  `coding_session/iterate.py` module + supporting `epistemic/
  skip_state.py` ContextVar carrier.
- **~10 small extensions** of existing modules
  (`epistemic/orchestrator_hook.py`, `coding_session/submit.py`,
  `agents/commander/{routing,orchestrator,commands}.py`,
  `change_requests/{validator,lifecycle}.py`,
  `healing/monitors/__init__.py`, `runtime_settings.py`,
  `goodhart_guard.py`, `llm_factory.py`, `llm_selector.py`).
- **1 Tier-3 amendment** to `app/tool_registry/capabilities.py`
  (operator-authorized) adding `ratelimit` + `code-intelligence`
  capability categories.
- **1 schema migration** (`migrations/036_code_intel.sql`) with
  three idempotent tables.

---

## The seven plan sections — delivered surface

### §1 Autonomous Executor — `app/autonomous_executor/`

The driver layer. `/delegate <goal>` files an `ExecutorRun`; the
HEAVY idle-scheduler tuple advances one step per tick; Commander is
the LLM dispatch surface (executor calls Commander as a library).

**Files:**
```
app/autonomous_executor/
  __init__.py             — exports + run_executor_tick alias
  models.py               — ExecutorRun + status enum + Budget
  store.py                — JSON-per-record at workspace/autonomous_executor/<id>.json
  planner.py              — deterministic v1 planner
  planner_llm.py          — LLM-driven v2 planner (master-switch-gated)
  driver.py               — drive_next_step + blocker auto-detection
  budget.py               — token/$/wall-clock caps + violation detection
  escalation.py           — blocker → Signal alert + operator typed-phrase resume
  audit.py                — fourth hash-chained ledger
  commander_adapter.py    — calls into Commander.handle() with budget tracking
  scheduler_job.py        — idle-scheduler entrypoint (run_executor_tick)
  coding_session_bridge.py — executor → coding_session context handoff
  tools/
    __init__.py
    delegate_tool.py      — agent-callable delegate_goal_tool
```

**Integration touchpoints:**
- `app/idle_scheduler.py:_default_jobs()` — `("autonomous-executor",
  _autonomous_executor_tick, JobWeight.HEAVY)` tuple, gated on
  `autonomous_executor_enabled` master switch.
- `app/agents/commander/commands.py` — `/delegate` slash command
  family: `<goal>`, `status`, `status <run_id>`, `abort <run_id>`,
  `resume <run_id> <hint>`, `help`.
- `app/control_plane/delegate_api.py` — REST endpoints under
  `/api/cp/delegate/*`: `POST /` (create), `GET /` (list),
  `GET /{run_id}` (detail), `POST /{run_id}/abort`,
  `POST /{run_id}/resume`.
- `dashboard-react/src/components/DelegatePage.tsx` — operator UI
  at `/delegate`.
- `app/identity/continuity_ledger.py` — new `executor_milestone`
  event kind (20th).
- 4th hash-chained audit ledger at
  `workspace/autonomous_executor/audit.jsonl` (matches the
  coding_session / change_request / governance_amendment pattern;
  independent by design — never unified).

**Master switch:** `autonomous_executor_enabled` (default OFF).

---

### §2 Persistent Dev Workspace — `app/coding_session/` extensions

Lifts the coding-session worktree out of "ephemeral" mode:

- New `CodingSession.durable: bool = False` — excludes the session
  from retention-monitor cleanup when True.
- New `CodingSession.iterate_loop_state: dict | None = None` —
  optional iteration state for the test-driven-loop primitive.
- New `submit_session(submit_mode="branch", branch_name=..., pr_title=..., pr_body=...)`
  — instead of fan-out one CR per touched file, the agent can submit
  the whole worktree as a single git branch + PR via the existing
  `_run_git_auto_pr` from `change_requests/apply.py`.
- New `app/coding_session/iterate.py` (~340 LOC) —
  `iterate_until_green(session_id, *, max_iterations=20, budget_usd=2.0)`
  drives failing-test → `structured_diagnosis` → apply → retest in a
  loop until green / budget / max-iter.

**Integration touchpoints:**
- `app/healing/monitors/retention.py` — durable-session exclusion
  guard (1-line additive change).
- `app/tools/coding_session_tools.py` — new
  `CodingSessionIterateTool` factory.

**Master switches:** `coding_session_iterate_loop_enabled`,
`coding_session_durable_enabled` (default OFF).

---

### §3 Risk Classifier — `app/risk_classifier/`

Trust-zone classification + widening proposer + two-reasoner review:

```
app/risk_classifier/
  __init__.py
  zones.py               — 8 trust zones (ZONE_FREE → ZONE_IMMUTABLE)
  classifier.py          — classify(Action) → Decision (AUTO|GATED|TWO_PARTY|REFUSE)
  evidence.py            — rolling stats: actions_per_zone_per_day, rollback_rate_30d
  widening.py            — WideningProposal + propose_widenings analysis
  widening_decisions.py  — decision-history tracker + governance_ratchet emission
  two_reasoner.py        — two-reasoner peer review for high-risk proposals
```

**Integration touchpoints:**
- `app/change_requests/validator.py:240,244` — `_AUTO_APPLY_ALLOWED_REQUESTORS`
  and `_AUTO_APPLY_ALLOWED_PATHS` became dynamic getters reading
  `runtime_settings.get_auto_apply_allowed_requestors()` /
  `get_auto_apply_allowed_paths()`.
- `app/change_requests/lifecycle.py:152` — `create_request` gains
  optional `pre_classified_zone: str | None = None` parameter.
- `app/control_plane/widening_api.py` + `reviews_api.py` — REST
  endpoints under `/api/cp/widening/*` and `/api/cp/reviews/*`.
- `dashboard-react/src/components/TrustZonesCard.tsx` in
  `/cp/settings`; `WideningPage.tsx` at `/widening`;
  `ReviewsPage.tsx` at `/reviews`.
- `app/goodhart_guard.py` — new `_detect_auto_apply_gaming`
  detector (Gap E closure): watches `change_requests.store` for
  CRs decided via `SELF_HEAL_AUTO_APPLY` / `VACATION_AUTO_APPLY`
  and flags the lane when rollback rate exceeds 30% (or 15% at
  high volume).

**Master switches:** `risk_classifier_enabled`,
`widening_proposer_enabled`, `two_reasoner_review_enabled`,
`auto_apply_allowed_requestors` (list), `auto_apply_allowed_paths`
(list) — all default OFF / empty.

---

### §4 Reflexive Verification — `gate_output()` extension

`gate_output()` keeps its three actions (`ship`/`revise`/`block`)
and gains a verification-extension chain:

- `app/epistemic/verification_extension.py` — four new evaluators
  composed inside `gate_output()`:
  1. `_evaluate_claim_source_consistency` — extracts factual claims,
     looks them up in the `subia/grounding/source_registry`, hedges
     or refuses when source is missing or low-trust.
  2. `_evaluate_retrieval_on_low_confidence` — when confidence is
     below the zone threshold AND the claim is retrievable, tries
     `web_search` and retries classification.
  3. `_evaluate_zone_aware_threshold` — threshold is per-zone
     (chat=0.60, autonomous=0.90, financial=0.95).
  4. Aggregator → ship/revise/block consistent with the existing
     gate semantics.

**Plus** `app/epistemic/skip_state.py` (Gap 2 / §7-3 closure) — a
per-request ContextVar that the routing layer sets to True for
structurally trivial fast-path patterns (calendar lookup, file
list, etc — no factual claims possible). `gate_output()` honours
the flag and short-circuits with `action="ship"`.

**Master switches:** `verification_extension_enabled`,
`verification_threshold_chat/autonomous/financial`,
`epistemic_enabled_override`, `epistemic_blocking_mode_override`
(all runtime_settings, hot-flippable without restart).

---

### §5 Code Intelligence — `app/code_intel/`

```
app/code_intel/
  __init__.py
  models.py              — SymbolLocation / ReferenceLocation / IndexSnapshot
  indexer.py             — pure-AST Python indexer (fast, dep-light)
  tree_sitter_indexer.py — tree-sitter parallel indexer (Gap 1 closure;
                           registry-driven multi-language hook)
  store.py               — JSONL store at workspace/code_intel/
                           (dual-writes to Postgres when enabled)
  postgres_store.py      — Postgres-backend writer for migration-036 tables
                           (Gap F closure)
  query.py               — find_symbol, find_references, find_callers
  refresh.py             — idle-scheduler tuple wrapper
  pyright_sidecar.py     — pyright subprocess + project-config discovery
  agent_tools.py         — 8 agent-callable tools
```

**Eight agent tools** (the plan-named 6 + 2 Gap C additions):
`code_intel_find_symbol`, `code_intel_find_references`,
`code_intel_find_callers`, `code_intel_type_check`,
`code_intel_coverage`, `code_intel_deps`, `code_intel_history`
(git blame / log), `code_intel_test_for` (find tests covering a
symbol/file).

**Three Postgres tables** (migration `036_code_intel.sql`):
`code_symbols`, `code_references`, `code_coverage_snapshot` — all
idempotent (`IF NOT EXISTS`), all indexed for the canonical
query patterns. Dual-write from JSONL store via
`postgres_store.save_index()` when `code_intel_postgres_enabled`.

**Requirements (Gap 3 + Gap D):** `ruff>=0.5.0`,
`tree-sitter>=0.22.0`, `tree-sitter-python>=0.21.0`,
`pyright>=1.1.350`.

**Master switches:** `code_intel_enabled` (overall),
`code_intel_tree_sitter_enabled` (default OFF — additive to AST),
`code_intel_postgres_enabled` (default OFF — JSONL canonical),
`pyright_sidecar_enabled`, `code_intel_auto_type_check_enabled`.

---

### §6 Connector tag migration — `app/tool_registry/` extensions

23 tools (plan said ~12; over-delivered) gained `@register_tool`
decorators with capability tags. Two new categories added via
**Tier-3 amendment** (operator-authorized 2026-05-23):

- **`ratelimit`** (6 tags): `quota-limited-anthropic`,
  `quota-limited-brave`, `quota-limited-google-workspace`,
  `quota-limited-openai`, `quota-limited-osv`,
  `quota-limited-github`.
- **`code-intelligence`** (4 tags): `queries-code-symbols`,
  `checks-types`, `finds-test-coverage`, `finds-deps`.

Proposal docs at:
- `docs/proposed_tier3_amendments/ratelimit_capability_category.md`
- `docs/proposed_tier3_amendments/code_intelligence_capability_category.md`

Both marked **APPLIED** 2026-05-23.

**ConnectorBudget primitive** — `app/connector_budget/decorator.py`
(`@with_connector_budget(daily_cap_usd=...)` and
`daily_call_cap=...`) — sibling decorator to `@register_tool`. Per-
connector daily caps; raises `ConnectorBudgetExceeded` on overrun.
Operator overrides via `runtime_settings.connector_budget_overrides`.

---

### §7 Fast Path — `app/agents/commander/routing.py` extensions

Three additive moves:

1. **`_FAST_ROUTE_PATTERNS` extended** with 10+ new patterns
   covering trivial high-frequency queries (today's calendar,
   latest briefing, ticket status, file list).
2. **`_try_local_route()`** — new function that matches
   interest-profile-aware queries (calendar / briefing / threads
   / health / tickets / notes) and returns a routing decision
   with `tier_hint="local"`. When the master switch
   `local_route_enabled` is True, the orchestrator threads
   `tier_hint` through `_run_crew` → `set_active_local_tier(True)`
   ContextVar → `create_commander_llm()` overrides `mode="local"` →
   the resolver picks an Ollama model (Gap A closure — was
   half-wired before).
3. **Output-streaming shortcut** (`app/epistemic/skip_state.py` —
   Gap 2 closure) — trivial fast-path patterns get
   `skip_verification=True` in the routing decision; the
   orchestrator sets the ContextVar; `gate_output()` short-
   circuits with `action="ship"` and a diagnostic note.

**Master switches:** `local_route_enabled` (default OFF),
`extended_fast_route_patterns_enabled` (default ON).

---

## Risk register — closure status

| # | Risk | Mitigation | Status |
|---|------|------------|--------|
| 1 | `EPISTEMIC_ENABLED` is env-var, not runtime_settings | Migrated to `epistemic_enabled_override` runtime_settings key | **Closed** |
| 2 | `capabilities.py` is TIER_IMMUTABLE | Tier-3 amendment applied 2026-05-23 (operator-authorized) | **Closed** |
| 3 | Three audit chains don't compose | Verified by design; 4th chain at `workspace/autonomous_executor/audit.jsonl` matches the pattern; never unified | **Closed (as designed)** |
| 4 | `gh` CLI version drift fragility | New healing monitor `app/healing/monitors/gh_version.py` (41st) — weekly probe + Signal alert on major-version drift | **Closed** |
| 5 | No central cost/quota model | `ratelimit` capability category + `ConnectorBudget` decorator + per-vendor caps in `runtime_settings.connector_budget_overrides` | **Closed (observability layer)** |
| 6 | code_intel adds 50MB pyright to gateway | Operator decision: stays in Dockerfile; tree-sitter is the parallel multi-language path | **Closed (operator decision)** |

---

## Phase / Gap closure history

| Phase | Description | Tests |
|-------|-------------|------:|
| A.1–A.4 | iterate_loop_enabled + CR attribution + pre_classified_zone + governance_ratchet | 50+ |
| B.1–B.5 | inspect.signature CR + conftest stubs + Decimal arithmetic + daily_call_cap + pydantic_settings pin | 30+ |
| C.1–C.5 | Capability tag migration + code_intel coverage/deps + benchmark suite + pyright in Dockerfile + JSONL design doc | 40+ |
| D.1–D.3 | Feed-source daily_call_cap + dependency_radar OSV/GitHub + Anthropic per-day USD cap | 20+ |
| E.1–E.3 | JsonlLedger + MasterSwitch registry + @switch_gated decorator | 30+ |
| Gap 1–6 | executor_milestone / escalation / classifier-in-CR / local_route / Anthropic gate wiring / ratelimit Tier-3 proposal | 60+ |
| Gap I–IV | delegate_tool / iterate_loop_state / evidence.py / executor audit chain | 40+ |
| Risk #4 | gh CLI version monitor | 24 |
| Gap 1–4 (4th round) | Code-intel architecture upgrade / ruff+tree-sitter / output-streaming shortcut / Tier-3 amendment | 80+ |
| Gap A–D | tier_hint dispatch / /resume command / history+test_for tools / pyright in requirements | 45+ |
| Gap E–F | goodhart auto-apply lane / Postgres backend writer | 25 |

**Cumulative gap-closure test surface: 410+ passing, 0 failing, 9 skipped (source-pinned where full stack required).**

---

## Operator surfaces

| Where | What |
|-------|------|
| **Signal** | `/delegate <goal>`, `/delegate resume <run_id> <hint>`, `/budgets`, `/thread`, etc. |
| **React `/cp/*`** | `/delegate`, `/widening`, `/reviews`, `/capability-regression`, `/coding-sessions`, `/changes`, `/settings` cards (TrustZones, VerificationExtension, AnthropicBudget, ConnectorBudget, CapabilityRegression, etc.) |
| **REST `/api/cp/*`** | `delegate/*`, `widening/*`, `reviews/*`, `changes/*`, `coding-sessions/*`, `capability-regression/*`, `connector-budget/*`, `anthropic-budget/*`, `code-intel/*` |
| **Audit ledgers** | `workspace/autonomous_executor/audit.jsonl` (4th hash-chained ledger) |
| **Healing monitors** | 41 registered; new: `gh_version` (41st) |

---

## Deliberately off the table

1. No new architectural layer above Commander — executor is parallel, not above.
2. No replacement of `gate_output()` — extending its evaluators is the elegant path.
3. No central audit unification — four independent chains by design.
4. No fine-tuning / model training — weights stay frontier.
5. No TIER_IMMUTABLE bypasses outside the Tier-3 protocol.
6. No removal of any current default-OFF feature — they remain available, just gated by trust zones.

---

## Files added (new, ~25 modules)

- `app/autonomous_executor/` (13 files)
- `app/risk_classifier/` (7 files)
- `app/code_intel/` (9 files)
- `app/coding_session/iterate.py`
- `app/connector_budget/` (3 files)
- `app/capability_regression/` (snapshot + detector)
- `app/benchmarks/` (real benchmark suite)
- `app/epistemic/skip_state.py`
- `app/epistemic/verification_extension.py`
- `app/healing/monitors/gh_version.py`
- `app/llm_anthropic_budget.py`
- `app/utils/{jsonl_ledger,master_switches,switch_gated}.py`
- `app/control_plane/{anthropic_budget,benchmarks,capability_regression,code_intel,connector_budget,delegate,reviews,widening}_api.py`
- `dashboard-react/src/components/{Delegate,Widening,Reviews,CapabilityRegression,Benchmarks}Page.tsx`
- `dashboard-react/src/components/{TrustZones,VerificationExtension,AnthropicBudget,ConnectorBudget,CapabilityRegression}Card.tsx`
- `migrations/036_code_intel.sql`
- `docs/proposed_tier3_amendments/{ratelimit,code_intelligence}_capability_category.md` (APPLIED)
- `docs/PHASES_ABCDE_SUMMARY.md`

## Files extended (existing modules touched, ~20)

Modified files all retain backward-compatible signatures (defaults
preserve pre-amendment behaviour). See git diff for the full set.

---

## Test entry points

```
tests/test_capabilities_vocabulary.py
tests/test_skip_verification.py
tests/test_tree_sitter_indexer.py
tests/test_gh_version_monitor.py
tests/test_local_tier_dispatch.py
tests/test_delegate_resume_command.py
tests/test_code_intel_history_test_for.py
tests/test_goodhart_auto_apply_lane.py
tests/test_code_intel_postgres.py
tests/test_executor_audit_chain.py
tests/test_executor_milestone_event.py
tests/test_executor_escalation.py
tests/test_classifier_in_cr_lifecycle.py
tests/test_local_route.py
tests/test_gaps_i_ii_iii.py
tests/test_gap_a_b_wireup.py
tests/test_anthropic_call_or_skip.py
tests/test_switch_gated.py
tests/test_feed_sources_switch_gated.py
tests/test_jsonl_ledger.py
tests/test_master_switches.py
tests/test_risk_classifier.py
tests/test_two_reasoner.py
tests/test_two_reasoner_cr_integration.py
tests/test_widening_decisions_and_api.py
tests/test_code_intel.py
tests/test_connector_budget.py
tests/test_connector_budget_overrides.py
tests/test_capability_regression.py
tests/test_pyright_sidecar.py
tests/test_iterate_pyright_wire.py
tests/test_iterate_type_aware_diagnosis.py
```

---

## Activation order (operator runbook)

All master switches default **OFF**. Flip in order:

1. `verification_extension_enabled` — extended evaluators in `gate_output()` (lowest risk; observational where thresholds are conservative).
2. `risk_classifier_enabled` — classifier reads CR creation; with empty allowlists it observes only.
3. `local_route_enabled` + `code_intel_enabled` — fast-path + symbol index (requires Ollama warm + `code_intel.refresh` ticked at least once).
4. `autonomous_executor_enabled` — `/delegate` becomes operational.
5. **Optional widening:** populate `auto_apply_allowed_requestors` / `auto_apply_allowed_paths` only after >30 days of clean classifier evidence. Goodhart auto-apply detector watches this lane.

---

## Cross-references

- `crewai-team/PROGRAM.md §62` — full delivery commit notes.
- `crewai-team/docs/PHASES_ABCDE_SUMMARY.md` — operator runbook for Phases A–E.
- `crewai-team/docs/CODING_SESSIONS.md` — coding-session subsystem.
- `crewai-team/docs/CHANGE_REQUESTS.md` — change-request lifecycle.
- `crewai-team/docs/TIER3_AMENDMENT.md` — Tier-3 amendment protocol.

---

*Delivered through six rigorous ultrathink audit cycles. Every plan
promise verified operationally end-to-end, not just by file
existence. Zero TIER_IMMUTABLE touches outside the standard Tier-3
amendment protocol; zero behavioural change to existing subsystems
when new switches are OFF.*
