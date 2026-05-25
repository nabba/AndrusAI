# LLM Cost Model — Operator Guide

The LLM factory enforces cost discipline through **seven** distinct
mechanisms, each operating at a different layer of the call stack.
This document explains what each does, how they interact, and which
lever to pull for which operational goal.

## TL;DR

| Layer | Mechanism | Granularity | What it does |
|---|---|---|---|
| 1 | `cost_mode` (free/budget/balanced/quality/insane) | Per-call selection | Hard-filters which tiers/providers the selector may consider |
| 2 | `budget_usd` per role | Per-call selection | Soft Pareto demotion to a cheaper model within `quality_gap=0.10` |
| 3 | `RoleCostProfile.expected_hourly_usd` adaptive | Per-call selection | Auto-tightens `budget_usd` for roles running over their hourly pace |
| 4 | `cost_input/output_per_m` catalog data | Per-call estimation | Numeric inputs to the Pareto demotion + `pre_check` estimates |
| 5 | `anthropic_daily_cap_usd` | Per-call refusal | Hard cap on rolling-24h Anthropic spend; raises `AnthropicDailyCapExceeded` |
| 6 | `openrouter_daily_cap_usd` | Per-call refusal | Hard cap on rolling-24h OR spend; raises `OpenRouterDailyCapExceeded` |
| 7 | `total_cost_monthly_cap_usd` (idle-pause brake) | Per-construction + per-call skip | Hard ceiling on monthly multi-provider spend; brakes paid providers at 95% |

Going from **fine-grained to coarse**: 5+6 (per-provider per-call) > 4 (cost data) > 3 (adaptive back-pressure) > 2 (Pareto soft demote) > 1 (mode hard whitelist) > 7 (monthly brake).

Plus a meta-layer that doesn't gate calls but **proposes adjustments**:

| Mechanism | Trigger | What it does |
|---|---|---|
| `llm_cost_advisor` | Weekly idle job | Files CRs via `proposal_bridge` proposing cap raises/lowers based on 7-day spend trends + per-role baseline adjustments |

## The canonical data source

All seven mechanisms read cost data from **one** place:

- **`token_usage` table in SQLite** (`workspace/llm_benchmarks.db`) — written by `app.llm_benchmarks.record_tokens` on every observed LLM call. Schema: `(model, prompt_tokens, completion_tokens, total_tokens, cost_usd, ts, project_id, agent_role)`.

The canonical reader is `app/llm_cost_ledger.py` — every cost-aware module delegates to it. Provider classification (which model id maps to which provider) is `app/llm_provider_classify.py`.

> **Historical note**: prior to 2026-05-25 the per-provider budget modules read JSONL from a non-existent `app.audit_log` module. The imports failed silently (fail-OPEN), spend was always 0.0, and the per-day caps never fired. The session-9 fix routes all readers through the canonical SQLite ledger; the gates now actually engage on real data.

## Where each mechanism fires

```
┌─────────────────────────────────────────────────────────────────┐
│ create_*_llm()                                                   │
│ ├─ select_model(role, task_hint, budget_usd=...)                 │
│ │  ├─ Step 2: get_default_for_role  → cost_mode filter (1)       │
│ │  ├─ Step 2a: capability gate                                   │
│ │  ├─ Step 4: benchmark override + Pareto demote (2)+(4)         │
│ │  ├─ Step 4c: budget_usd enforcement (2)                        │
│ │  └─ Step 5.7: mode-pool fallback (1)                           │
│ │                                                                 │
│ │  _resolved_budget_usd(role):                                   │
│ │    base = RoleCostProfile.budget_usd                            │
│ │    factor = adaptive_budget_factor(role)  ← (3)                 │
│ │    return base × factor                                         │
│ │                                                                 │
│ └─ _walk_chain(candidates)                                       │
│    └─ _construct_from_entry(name, entry)                         │
│       └─ _check_candidate_basics():                              │
│          ├─ shape validation                                      │
│          ├─ health-cache skip                                     │
│          ├─ idle_pause_due_to_budget brake (7)                    │
│          │  — paid providers only                                 │
│          ├─ openrouter pre_check (6) — OR provider                │
│          │  — at construction; per-call too via BudgetAware      │
│          └─ API-key check                                         │
│                                                                   │
│ Per-call (only on the constructed LLM):                          │
│ ├─ CreditAwareAnthropicCompletion.call:                          │
│ │  ├─ idle_pause_due_to_budget brake (7) — re-checked at call     │
│ │  └─ pre_check (5) — Anthropic per-call cap                      │
│ │                                                                 │
│ ├─ AnthropicClientHandle._InstrumentedMessages.create:           │
│ │  └─ pre_check (5) — Anthropic per-call cap                      │
│ │                                                                 │
│ └─ BudgetAwareCompletion.call (OR-routed LLMs):                  │
│    └─ pre_check (6) — OpenRouter per-call cap                     │
└─────────────────────────────────────────────────────────────────┘
```

## When to use which lever

### "I want zero LLM spend right now"
Set `cost_mode=free`. Hard-filters to `{local, free}` tiers — only Ollama and free-tier OpenRouter models survive `_filter_candidates`. The selector physically cannot pick a paid model.

### "I want a monthly ceiling — pause everything paid above it"
Set `total_cost_monthly_cap_usd` in `/cp/settings`. The `total_cost_ceiling` healing monitor watches monthly spend; at 95% it engages `idle_pause_due_to_budget`. The chain walker then refuses Anthropic and OpenRouter at construction, falling through to Ollama. Hysteresis: clears at 70%.

### "Anthropic specifically — refuse new calls at $X/day"
Set `anthropic_daily_cap_usd` in `/cp/settings`. Per-call refusal via `pre_check` inside `CreditAwareAnthropicCompletion.call` and `AnthropicClientHandle._InstrumentedMessages.create`. When the cap fires AND OpenRouter Claude is configured, CreditAware silently fails over to OR — the call succeeds, the operator gets a 1/day-deduped Signal alert.

### "OpenRouter specifically — refuse new calls at $X/day"
Set `openrouter_daily_cap_usd` in `/cp/settings`. Per-call refusal inside `BudgetAwareCompletion.call` (every OR LLM is wrapped) plus a construction-time check in `_construct_from_entry`.

### "Each role should have a typical spend ceiling per call"
The `_ROLE_PROFILES` table in `app/llm_role_spend.py` defines per-role `RoleCostProfile(budget_usd, expected_hourly_usd)`:
- `commander`: $0.05 / $0.20/h
- `vetting`: $0.05 / $0.10/h
- `cheap-vetting`: $0.005 / $0.05/h
- `research` / `coding`: $0.50 / $2.00/h
- `self_improve`: $0.50 / $1.00/h
- `writing` / `creative`: $0.25 / $1.00/h
- `media`: $0.25 / $0.50/h
- fallback: $0.20 / $0.50/h

`budget_usd` flows into `select_model(budget_usd=...)` → Step 4c Pareto demote. `expected_hourly_usd` is the adaptive baseline (layer 3).

### "Tighten a role automatically when it's burning fast"
Layer 3 (adaptive back-pressure) does this without operator intervention. `adaptive_budget_factor(role)` reads the role's last 1h spend, compares to `expected_hourly_usd`, and returns a multiplier:
- < 1.5× expected: factor 1.0 (no tightening)
- 1.5–2.5×: factor 0.8 (mild)
- 2.5–4×: factor 0.5 (significant)
- > 4×: factor 0.25 (aggressive)

The factor multiplies the role's `budget_usd` for the next call's selector pass. The selector then demotes to cheaper alternatives. Only tightens — under-pace roles get the base budget, not a free pass.

### "I want to see total spend right now"
- React Settings card → "Anthropic per-day" pane and "OpenRouter per-day" pane → live snapshot
- React Cost dashboard → cost-by-crew/agent/internal panels (source of truth: SQLite `token_usage`)
- API: `app.llm_anthropic_budget.state_snapshot()` and `app.llm_openrouter_budget.state_snapshot()` (pass `use_cache=False` for fresh reads)
- API: `app.llm_role_spend.all_roles_summary(hours=24)` for per-role breakdown

## The cost advisor (meta-layer)

The advisor watches 7-day spend trends and **proposes** cap adjustments via `proposal_bridge` — operators approve via the standard CR workflow. Never auto-applies.

Decision rules (all thresholds operator-tunable via `runtime_settings`):

**Per-provider caps:**
| Condition | Action | Magnitude |
|---|---|---|
| Cap hit on ≥3 of 7 days (`cost_advisor_raise_n_days`) | **Raise** | × `cost_advisor_raise_factor` (default 1.25) |
| Cap > 0 AND <25% utilised on ≥6 of 7 days | **Lower** | × `cost_advisor_lower_factor` (default 0.5) |
| No cap AND mean spend > `cost_advisor_set_min_daily_usd` (1.0) | **Set** | `cost_advisor_set_factor` (2.0) × max-day-spend |

**Per-role baselines:**
| Condition | Action | Magnitude |
|---|---|---|
| 24h spend > 4× expected baseline | **Raise `expected_hourly_usd`** | 2× |
| 24h spend < 0.1× expected baseline | **Lower `expected_hourly_usd`** | 0.5× |

Internal cadence: at most one analyser pass per 24h (state file at `workspace/llm_cost_advisor/last_run.txt`). LIGHT-pass invocations within the window short-circuit to empty.

Rejection-backoff: `proposal_bridge`'s terminal-state guard means a REJECTED proposal stays terminal — re-staging the same signature is a silent no-op. No advisor-level backoff needed.

## How the mechanisms compose

The layers compose conservatively — the **most restrictive** wins:

1. **Mode (1) is the outermost gate.** A `free`-mode operator can't be overridden by any other cost knob.
2. **Monthly brake (7) overrides everything except mode.** Once `idle_pause_due_to_budget` engages, paid providers are skipped regardless of any per-role budget or per-day cap state.
3. **Per-day caps (5, 6) override per-role budgets.** A `commander` role with `budget_usd=$0.05` (which would happily run Sonnet at ~$0.01/call all day) still hits the per-day cap once accumulated spend approaches the ceiling.
4. **Adaptive back-pressure (3) tightens per-call but never blocks.** A role running 6× expected gets `budget_usd × 0.25`, but the call still happens — at a cheaper model picked by the selector's demote.
5. **Pareto demote (2) is the soft-preference layer.** Within the candidate pool that survived 1, 5, 6, 7, the per-role `budget_usd` biases selection toward cheaper alternatives when their benchmark scores stay within `quality_gap=0.10` of the default.
6. **Catalog cost data (4) feeds 2 + the pre_check estimates.** It's not a gate itself — it's the numeric data the gates compute from.

## Failover behavior — what happens when a cap fires

### Anthropic daily cap fires (5)
1. `pre_check` raises `AnthropicDailyCapExceeded` inside `CreditAware.call` or `Handle.messages.create`
2. **CreditAware path**: if `OPENROUTER_API_KEY` is set, fails over to OR Claude. Operator gets a 1/day-deduped Signal alert.
3. **Handle path** (the 22 migrated raw-SDK sites): exception propagates. Sites use existing `except Exception:` to return graceful sentinels.
4. **Router path specifically**: if no fallback configured, `AnthropicDailyCapExceeded` propagates to the orchestrator's typed catch arm (`isinstance(exc, CapExceededError)`) → loud Signal alert + provider-named user reply.

### OpenRouter daily cap fires (6)
1. `pre_check` raises `OpenRouterDailyCapExceeded` (subclass of `CapExceededError`)
2. **BudgetAwareCompletion path**: exception propagates (no automatic OR-to-OR fallback — there is no further provider)
3. **Construction-time path**: caught and converted to `ConstructionFailed("budget_paused", …)`; chain walker falls through to local Ollama
4. **Router path**: same orchestrator catch arm as the Anthropic case via the `CapExceededError` base class

### Monthly brake fires (7)
1. `idle_pause_due_to_budget` engages at 95% of monthly cap
2. Construction-time check in `_construct_from_entry` for paid providers → `ConstructionFailed("budget_paused", …)`
3. Call-time check in `CreditAware.call` / `.acall` → transparently fails over to OR if configured, else raises
4. Chain walker falls to local Ollama
5. Operator sees Signal alert from `total_cost_ceiling` monitor + 1/day cap-engaged alert from CreditAware

## What the cost model does NOT do

- **No per-provider monthly cap.** Only total across providers (7) and per-day per-provider (5, 6).
- **No automatic cap tightening.** The advisor PROPOSES; operators approve. Never auto-applies.
- **No mid-stream cost gating.** Once a call starts, it completes. The next call's pre-check sees the cumulative cost.
- **No precise per-call estimate from prompt.** The estimate uses `max_tokens` × catalog cost-per-token with a 2000-token input assumption. Real prompts can vary 10x.

## Adding a new cost lever

When adding a new cost-discipline mechanism:

1. Pick the layer it belongs to (selection / construction / per-call) and document why.
2. If it's a hard refusal: subclass `CapExceededError` from `app.llm_anthropic_budget` so `BudgetAwareCompletion` and the orchestrator's catch arm pick it up automatically.
3. If it's a soft preference: feed into `select_model`'s scoring, not into construction.
4. Add a 5-second TTL cache around any per-call spend read (use `app.llm_cost_ledger`'s patterns).
5. Update this document with the new row in the TL;DR table and the composition order.

## Adding a new provider with cost discipline

Symmetry checklist (mirror what Anthropic + OpenRouter have):

1. **Daily cap module** `app/llm_<provider>_budget.py` with `<Provider>DailyCapExceeded(CapExceededError)`, `pre_check`, `today_spent_usd`, `state_snapshot`. Delegate to `llm_cost_ledger.spend_for_provider` (don't reimplement the reader).
2. **Provider classifier** — add the prefix to `app/llm_provider_classify.py`.
3. **Per-call wrapping** — wrap the LLM constructor with `BudgetAwareCompletion(budget_module=..., estimated_cost_fn=...)`.
4. **Runtime settings** — add `<provider>_daily_cap_usd` getter/setter to `runtime_settings.py`.
5. **Idle-pause brake** — add provider to the paid-providers list in `_check_candidate_basics`.
6. **Cost advisor** — `analyze_provider_caps` reads from `llm_cost_ledger.daily_spend_by_provider_for_advisor`; add the provider name to its iteration.

## See also

- [LLM_SUBSYSTEM.md](LLM_SUBSYSTEM.md) — full factory architecture
- [llm_factory.py](../app/llm_factory.py) — `_check_candidate_basics`, `_resolved_budget_usd`
- [llm_cost_ledger.py](../app/llm_cost_ledger.py) — canonical SQLite reader
- [llm_provider_classify.py](../app/llm_provider_classify.py) — single classification source
- [llm_role_spend.py](../app/llm_role_spend.py) — `_ROLE_PROFILES`, `adaptive_budget_factor`
- [llm_anthropic_budget.py](../app/llm_anthropic_budget.py) — Anthropic per-day cap + `CapExceededError` base
- [llm_openrouter_budget.py](../app/llm_openrouter_budget.py) — OpenRouter per-day cap
- [llms/budget_aware.py](../app/llms/budget_aware.py) — per-call wrap for LiteLLM-routed LLMs
- [llm_factory_probe.py](../app/llm_factory_probe.py) — `call_with_observation` envelope
- [llm_cost_advisor/](../app/llm_cost_advisor/) — weekly cap-adjustment proposer
- [healing/monitors/total_cost_ceiling.py](../app/healing/monitors/total_cost_ceiling.py) — monthly brake
