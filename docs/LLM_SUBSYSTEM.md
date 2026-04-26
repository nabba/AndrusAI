# LLM Subsystem

The complete reference for how this system chooses, calls, vets, and
learns from large-language-model usage. Written so a new contributor
can read it once and understand every layer end-to-end.

---

## 1. Why this design exists

Earlier versions hand-curated a 24-model dict and a static
`ROLE_DEFAULTS[mode][role]` table. Whenever a frontier model launched
(Claude Opus 4.7, DeepSeek V4, Kimi K2.6), someone had to edit Python
and ship a release before the system would *even consider* using it.
That defeated the point of having a model-aware agent stack at all.

The current system replaces that with **five interlocking feedback
loops**:

1. **Discovery** — every 24 h the catalog refreshes from live sources
   (Artificial Analysis, OpenRouter, Ollama). New models appear
   automatically.
2. **Promotion** — discovered models that Pareto-dominate an incumbent
   under cost-aware scoring become first-choice picks (free-tier auto;
   paid-tier via governance approval).
3. **External rankings** — third-party leaderboards (Artificial
   Analysis intelligence, coding, math indexes) blend into the
   selector's quality signal at a configurable weight.
4. **Telemetry** — every LLM call records latency, success, error,
   token usage; the per-task-type benchmarks shift the scoring weights
   over time.
5. **Vetting feedback** — when vetting fails on a low-tier model's
   output, the next selection for that role gets a tier bump or a
   different model entirely.

Plus three operator overrides for when humans know better:
**hand-pin** (force a specific model for a (role, mode) pair),
**promote** (mark a discovered model as a first-choice candidate), and
**runtime mode** (constrain the entire candidate pool — Free / Budget
/ Balanced / Quality / Insane / Anthropic).

---

## 2. Architecture (one screen)

```
                       ┌──────────────────────────────┐
                       │     create_specialist_llm    │   ◄── single
                       │   (app/llm_factory.py:299)   │       gateway
                       └──────────────┬───────────────┘
                                      │
                  ┌───────────────────┼───────────────────┐
                  │                   │                   │
          select_model()       get_default_for_role()    cascade fallback
          (llm_selector.py)    (llm_catalog.py)          (local → API → Claude)
                  │                   │
                  └────────┬──────────┘
                           ▼
            ┌─────────── resolve_role_default ────────────┐
            │  app/llm_catalog.py:533                      │
            │                                              │
            │  Layer 1  Hand-pin  ─►  return directly      │
            │  Layer 2  Promotion ─►  filter candidate set │
            │  Layer 3  Pool      ─►  score & pick best    │
            └──────────────┬───────────────────────────────┘
                           │
        reads policy:                          reads data:
        ─────────────                          ──────────
        _MODE_TIER_WHITELIST (per mode)        CATALOG (367 models)
        _MODE_PROVIDER_WHITELIST (anthropic)   strengths map (9 task types)
        _ROLE_TIER_FLOOR (commander=premium)   external ranks blend
        _ROLE_LOCAL_PREFERRED (planner etc.)   live benchmark scores
        _ROLES_NEEDING_TOOLS                   tool_use_reliability
        _MODE_WEIGHT (cost penalty)            cost_input/output_per_m

                           │
                           ▼
                  one of 367 catalog keys ──► instantiate LLM
```

Every layer fails soft. Every layer is overrideable. No layer is
allowed to be skipped.

---

## 3. The single-gateway invariant

**Every LLM call in the codebase goes through
`create_specialist_llm`.** This is enforced by convention, not the
type system, but verified by grep:

| Bypass vector | Allowed? |
|---|---|
| Direct `LLM(...)` outside `llm_factory.py` | ❌ — only one terminal call exists, at `llm_factory.py:119` |
| `ChatOpenAI` / `ChatAnthropic` / `OpenAI()` / `Anthropic()` direct instantiation | ❌ — zero matches anywhere |
| `Agent(llm="hardcoded-string")` (CrewAI accepts a model_id string) | ❌ — zero matches |
| `select_model()` called outside the factory | ❌ — only callsite is `llm_factory.py:347` |
| `force_tier=...` to constrain candidate pool | ✅ — by design; passes through resolver |

This invariant is what makes the dashboard's "view current pick per
role" report **honest** — it isn't a parallel computation that drifts
from runtime reality.

---

## 4. The 6-mode unified runtime axis

Single user-facing knob. Set via dashboard, Signal command, or env
var. Lives in `app/llm_mode.py` as a runtime-mutable singleton; reads
via `get_mode()`.

| Mode | Tier whitelist | Provider whitelist | Cost ceiling (output) | Cost weight | Use when |
|---|---|---|---|---|---|
| **free** | local + free | any | $0 | 0.50 | offline / zero-cost |
| **budget** | local + free + budget | any | ~$1.5/M | 0.35 | cost-minimised |
| **balanced** *(default)* | every tier | any | ~$6/M | 0.15 | normal operation |
| **quality** | every tier, premium-leaning | any | ~$30/M | 0.05 | best within reason |
| **insane** | premium only, no local | any | ∞ | 0.00 | money no object |
| **anthropic** | mid + premium | Anthropic only | ∞ | 0.15 | vendor lock |

Source of truth: `RUNTIME_MODES` tuple + `_MODE_*` policy dicts in
`app/llm_catalog.py`. Legacy aliases (`hybrid`, `local`, `cloud`) are
auto-normalised by `_normalize_mode()` so old configs keep working.

The mode controls **two** things at once:
1. Which catalog tiers are even eligible (`_MODE_TIER_WHITELIST`).
2. How aggressively the resolver penalises cost in scoring
   (`_MODE_WEIGHT`).

That collapse is the whole point of the unification — previously a
separate `cost_mode` axis (`budget`/`balanced`/`quality`) duplicated
half this signal in a confusing way.

---

## 5. The catalog

`app/llm_catalog.py` exposes a single `CATALOG: dict[str, dict]` that
the resolver scores against. Structure of each entry:

```python
"claude-opus-4.7": {
    "tier": "premium",                 # local | free | budget | mid | premium
    "provider": "anthropic",           # ollama | openrouter | anthropic
    "model_id": "anthropic/claude-opus-4-7",
    "context": 1_000_000,
    "multimodal": True,
    "supports_tools": True,
    "cost_input_per_m": 5.00,
    "cost_output_per_m": 25.00,
    "tool_use_reliability": 0.97,
    "strengths": {
        "coding":       0.95,
        "debugging":    0.93,
        "architecture": 0.94,
        "research":     0.92,
        "writing":      0.93,
        "reasoning":    0.95,
        "multimodal":   1.0,
        "vetting":      0.94,
        "general":      0.94,
    },
    "description": "Claude Opus 4.7 — Anthropic frontier reasoning.",
},
```

### 5.1 Bootstrap (survival minimum)

`_BOOTSTRAP_CATALOG` is a 3-entry hard-coded dict that exists only so
the system boots when every external API is down and no snapshot is on
disk:

| Key | Purpose |
|---|---|
| `claude-sonnet-4.6` | Premium Anthropic fallback |
| `deepseek-v3.2` | Budget OpenRouter fallback |
| `qwen3:30b-a3b` | Local Ollama fallback |

These are mutated in place when the builder refreshes their derived
fields, but never removed. If the resolver's filter set is empty, it
returns `claude-sonnet-4.6` — the universal bootstrap fallback.

### 5.2 Auto-population (the builder)

`app/llm_catalog_builder.py` runs every 24 h via the idle scheduler
job `llm-refresh-catalog`. It:

1. Fetches `https://artificialanalysis.ai/api/v2/data/llms/models`
   (intelligence index, coding index, math index, gpqa, livecodebench,
   ifbench, pricing, median tps).
2. Fetches `https://openrouter.ai/api/v1/models` (cost, context,
   modality, `supported_parameters`).
3. Probes the local Ollama daemon at `/api/tags` for installed models.
4. Cross-references by canonical key (the same `_resolve_catalog_key`
   helper that `llm_external_ranks.py` uses).
5. Calls `derive_strengths(aa_row, is_multimodal, tier)` to map the
   AA evaluation columns onto our 9 canonical task types.
6. Persists to `workspace/cache/llm_catalog_snapshot.json` (24-h TTL).
7. Mutates the live `CATALOG` dict in place — module-level imports of
   `from app.llm_catalog import CATALOG` keep working without restart.

A typical refresh produces ~360 entries. Manual trigger: send Signal
command `refresh catalog` or POST to `/api/cp/llms/discovery/run`.

### 5.3 Strengths map (9 canonical task types)

```
coding, debugging, architecture, research, writing,
reasoning, multimodal, vetting, general
```

Roles map to task types via `_ROLE_TO_TASK` in `llm_catalog.py`:

| Role | Task type | Notes |
|---|---|---|
| commander | general | Routing reliability, light tokens |
| critic | reasoning | Adversarial review |
| vetting | vetting | Output-quality gate |
| synthesis | writing | Multi-source merge |
| introspector | reasoning | Meta-cognitive |
| self_improve | research | Background reflection |
| planner | architecture | Topic decomposition |
| evo_critic | reasoning | LLM-Judge for evolution variants |
| coding | coding | Crews + standalone agents |
| research | research | Crews + standalone agents |
| writing | writing | Crews + standalone agents |
| media | multimodal | Image / PDF / audio |

Custom hint can override via `task_hint=` keyword to `select_model()`.

---

## 6. The 3-layer resolver

`resolve_role_default(role, mode)` in `app/llm_catalog.py` (≈line
533). The authority cake, strongest first:

### Layer 1 — Hand-pin (hard override)

Active row in `control_plane.role_assignments` with
`priority ≥ HAND_PIN_PRIORITY` (=1000) for `(role, mode)`.

If found and its `model` exists in the live `CATALOG`, the resolver
returns it **directly without scoring**. This is the dashboard's
📌 pin button + the Signal `pin <role> <mode> <model>` command.

`unpin_role()` retires the pin; resolver takes over again.

### Layer 2 — Promotion filter

Models in `control_plane.model_promotions` are "first-choice"
candidates. If any promoted model survives the hard filters
(tier whitelist, provider whitelist, multimodal need, tool need),
candidates **collapse to the promoted set** before scoring.

Promotion sources:
- Free-tier discovery: auto (`_promote_model` in `llm_discovery.py`).
- Paid-tier discovery: governance approval required (writes a
  `governance_requests` row of type `model_promotion`; user clicks
  Approve in the dashboard or Signal).
- Manual: dashboard button or Signal `promote <model>`.

### Layer 3 — Pool scoring

The default path. After hard filters:

```
quality = 0.60 · benchmark_score          (live telemetry)
        + 0.35 · catalog_strengths        (derived from AA / bootstrap)
        + 0.05 · tool_use_reliability     (only if role needs tools)

cost_penalty = mode_weight · (cost_per_m / max_cost_in_candidates)

score = quality − cost_penalty
```

When live benchmarks aren't yet populated for a model, the formula
falls back to `0.80·strengths + 0.20·tool_use_reliability` and skips
the cost normalisation against unknowns. The point is: a 5% quality
bump isn't worth a 20× cost increase under `budget` mode, but is
under `quality`.

`max(candidates, key=score)` returns the winner.

### 6.1 Effective tier floor reconciliation

`_effective_tier_floor(mode, role_tier_floor)`. The role-level floor
gets capped by the mode's max allowed tier so the resolver honours the
user's explicit mode choice rather than silently escalating:

| Role | role_tier_floor | mode | effective_tier_floor |
|---|---|---|---|
| commander | premium | balanced | premium |
| commander | premium | free | free *(capped)* |
| commander | premium | insane | premium |
| coding | budget | free | free *(capped)* |
| coding | budget | quality | budget |

Without this reconciliation, `free + commander` would silently
escalate to Claude premium because `tier_floor=premium` had no
satisfying entries in `{local, free}`.

### 6.2 Local preference

A small set of roles (`_ROLE_LOCAL_PREFERRED` =
{`introspector`, `self_improve`, `planner`, `evo_critic`}) prefers
local-tier picks when the mode allows it (`_MODE_PREFER_LOCAL` =
{`free`, `budget`, `balanced`}). For these roles in those modes, the
resolver narrows candidates to local-only first, then scores. Quality
/ insane / anthropic explicitly opt out.

---

## 7. The selector

`app/llm_selector.py:select_model(role, task_hint, force_tier)` is the
intermediate layer between the factory and the resolver. It applies
**non-mode** constraints — environment overrides, task-type detection,
difficulty bumps, runtime ContextVar — that the resolver doesn't know
about.

### 7.1 Selection sequence

1. **Env override** — `ROLE_MODEL_RESEARCH=kimi-k2.5` (case-insensitive).
   Skips everything else; verified to be in `CATALOG`.
2. **Default from resolver** — `get_default_for_role(role, get_mode())`
   reads the live runtime mode (so dashboard switches take effect on
   the next call, no restart).
3. **Task-type detection** — `canonical_task_type(role, task_hint)`
   keyword-scans the hint (`"debug stack trace"` → debugging,
   `"image"` → multimodal) and may swap the pick.
4. **Special rules** — e.g. multimodal hint forces a multimodal model
   regardless of strengths score.
5. **Benchmark adjustment** — `get_scores(task_type)` may shift the
   pick if the resolver chose something that's been failing in
   production.
6. **Availability check** — local: ping Ollama; API: verify key set.
7. **Return** — the catalog key.

### 7.2 Difficulty bumps

`difficulty_to_tier(difficulty, mode)` and
`_resolve_difficulty_tier_floor(role, difficulty)` provide a 1-10
difficulty axis that orthogonally bumps tier floors. Difficulty is set
by the orchestrator at task entry and propagated via the
`_active_difficulty` ContextVar so deeply-nested sub-agents inherit it
without explicit threading.

```
difficulty 1-3  →  budget tier floor (local OK if mode allows)
difficulty 4-7  →  default catalog logic
difficulty 8-10 →  premium tier floor

# Plus role-specific overrides:
research at d=8  →  premium (tighter than coding at d=8)
research at d=7  →  mid
coding at d=9    →  premium
writing at d=9   →  premium
```

This catches the case where a sub-agent (e.g. CrewAI's
`delegate_work_to_coworker` spawning a "Web Research Specialist"
inside a coordinator) wouldn't otherwise know how hard the parent
task is.

### 7.3 Resource-aware local picks

`_select_local_resource_aware()` checks Ollama VRAM usage + system
RAM + model `size_gb` from the catalog and prefers models already
loaded. RAM headroom of 16 GB is reserved for the OS + embeddings.

---

## 8. The factory (single gateway)

`app/llm_factory.py:create_specialist_llm(max_tokens, role, task_hint,
force_tier, phase)` is the only function that constructs `LLM`
instances.

### 8.1 Mode dispatch

```python
mode = get_mode()                # live runtime mode

if mode != "balanced":
    # Restrictive modes (free / budget / quality / insane / anthropic)
    # constrain the pool, then run the regular selector inside it.
    chosen = _pool_constrained_select(role, task_hint, mode, force_tier)
    return _build_from_entry(*chosen, ...) or claude_fallback(...)

# Balanced (default): unconstrained selector + full cascade fallback.
model = select_model(role, task_hint, force_tier)
entry = get_model(model)

if tier == "local" and settings.local_llm_enabled:
    llm = try_local(...)
    if llm: return _maybe_race_wrap(llm, ...)   # Stage 4.3 race-with-API
    if settings.api_tier_enabled:
        api_model = get_default_for_role(role, mode)
        try_api(api_model)                       # cascade up
    return claude_fallback(...)                  # universal bottom

if tier in (free, budget, mid):  try_api(...) or claude_fallback(...)
if provider == "anthropic":      try_anthropic(...)
```

### 8.2 LLM object cache

LLM instances are cached by `(builder-tag, model_id, max_tokens,
base_url, sampling_key)`. They're stateless wrappers over `(model_id,
api_key, params)` so sharing across requests is safe. Saves
~50-100 ms per specialist call.

### 8.3 Last-pick tracking (per-thread)

`is_using_local()`, `is_using_api_tier()`, `get_last_model()`,
`get_last_tier()` read a `threading.local` populated by the cascade —
used for telemetry attribution and the dashboard's "currently using"
indicator.

---

## 9. Vetting

`app/vetting.py`. **Not** every LLM call gets vetted; that would
double the cost. Vetting is risk-based across 4 tiers:

| Risk | Action | When |
|---|---|---|
| `none` | skip | Direct user answers, easy + premium model |
| `schema` | format / sanity check, no LLM call | Structured outputs from any tier |
| `cheap` | yes/no via budget model | Mid-tier output on routine task |
| `full` | full Claude Sonnet review | All local Ollama output, all code |

Risk derives from `crew_type + difficulty_score + model_tier`. The
vetting LLM itself is selected via the same resolver path with
`role="vetting"`, so it inherits the runtime mode (e.g. in
`anthropic` mode the vetting model is always Anthropic).

Vetting is bounded — `llm.call()` has a hard timeout to prevent the
2026-04-25 case where gpt-5.5 hung for 17 minutes inside a vetting
call and stalled the parent task lifecycle.

Failed vetting feeds back into the next selection: the role's tier
floor lifts, or the model gets a benchmark penalty, depending on the
failure shape.

---

## 10. Discovery & promotion

### 10.1 Discovery

`app/llm_discovery.py`. Runs on the idle scheduler. Consumes the
external-rank fetchers + the catalog snapshot to identify candidates
that **Pareto-dominate the incumbent**:

> Candidate dominates incumbent if
>   `quality_candidate ≥ quality_incumbent`
> AND
>   `cost_candidate ≤ cost_incumbent · (1 − cost_penalty[mode])`
>
> with at least one strict inequality.

`cost_penalty` is `0.35` in budget, `0.10` in balanced, `0.00` in
quality (so quality requires strict cost reduction; budget tolerates
some cost increase for big quality gains).

A `_discover_judges()` rotation picks one top-intelligence model from
each of three different provider families to act as the LLM-as-Judge
for cross-evaluation, preventing self-reinforcing bias.

### 10.2 Promotion

When a candidate dominates:

- **Free tier** (cost = 0): auto-promoted via
  `_promote_model(model, role, by="discovery")`. Inserts a row in
  `control_plane.model_promotions`.
- **Paid tier**: writes a `governance_requests` row of type
  `model_promotion`. The dashboard's Governance page or the Signal
  `approve <id>` command consumes it. On approval,
  `consume_approved_promotions()` calls `llm_promotions.promote()` and
  triggers a catalog rehydrate.

`_promote_model` correctly merges roles via:

```sql
ARRAY(SELECT DISTINCT unnest(COALESCE(promoted_roles, '{}') || %s::text[]))
```

(Earlier bug: simple assignment clobbered the previous role list when
the same model was approved twice for different roles.)

### 10.3 Hand-pin overlay

`pin_role(role, mode, model)` writes priority=1000 to
`role_assignments`. Returns directly from the resolver's layer 1 —
short-circuiting score and promotion entirely.

`unpin_role(role, mode)` retires every priority≥1000 row for that
pair. Doesn't touch lower-priority auto-promotion artefacts.

The dashboard surfaces 📌 inline on each role card and exposes a
"Pin to role" dialog from each model card in the catalog grid.

---

## 11. External rankings

`app/llm_external_ranks.py`. Three sources blended into a single
quality signal:

| Source | What it provides | Weight |
|---|---|---|
| **Artificial Analysis** | Intelligence index (~57-80 frontier), coding index, math index, gpqa, livecodebench, ifbench, throughput | dominant |
| **OpenRouter** | Cost (input/output per M), context size, supported parameters, throughput | cost ground truth |
| **HuggingFace leaderboard** | Open-model rankings | optional, dependency on `pandas` |

Blend formula in `_blend()`:

```
final_score = (1 − external_weight) · internal_score
            + external_weight       · external_score
```

`external_weight` defaults to `0.3` (settings.external_ranks_weight).

AA's intelligence index is rescaled by `/70` because the frontier
tops at ~57-80 (not 100). Models without an AA measurement get a
`0.85×` confidence penalty so the resolver doesn't over-weight a
guess.

---

## 12. Telemetry & feedback loops

`app/llm_benchmarks.py` records per-call metrics keyed by `(model,
task_type)`:

- latency (p50/p95)
- success rate (0/1 from `_benchmark_recorded` ContextVar)
- error type (timeout / 429 / 4xx / 5xx)
- token usage (in / out)
- cost ($, derived)

The selector reads `get_combined_scores(task_type)` on the hot path —
telemetry-driven scores override catalog strengths when present
(`0.60·bench + 0.35·strengths + 0.05·tool` in the resolver formula).

A `_record_token_usage` guard via `_benchmark_recorded` ContextVar
prevents double-recording when wrappers nest (e.g. cascade +
race-wrap + retry).

Re-benchmarking happens periodically via
`app/llm_discovery.py::TestIncumbentRotation` style logic — incumbents
are re-tested, new candidates are tested, scores update.

---

## 13. Span tracking (task-flow drawer)

`app/crews/span_events.py` subscribes to CrewAI's event bus
(`AgentExecutionStartedEvent`, `ToolUsageStartedEvent`,
`LLMCallStartedEvent`, plus their finish/error counterparts) and
persists every fine-grained event to
`control_plane.crew_task_spans`.

Correlation: a ContextVar `_current_crew_task_id` is set by
`crews/lifecycle.py` before `crew.kickoff()` runs; subscribers read it
on every event. CrewAI's own `event_id`/`parent_event_id` fields
reconstruct the agent → tool → llm-call hierarchy for free.

Per-row overhead: ~1 ms INSERT (start) + ~1 ms UPDATE (finish).
Typical crew run = ~45 events ≈ 90 ms across a 30-60 s run.

The dashboard's Tasks tab opens a drawer on row click with two views
(Tree / Timeline) that poll `/api/cp/tasks/{id}/timeline` at 2 s
while the task state is `running`, then stop. Retention: 7-day sweep
via the idle scheduler `spans-retention` job.

A 10-minute watchdog (`close_stale_spans`) marks any span stuck in
`running` longer than that as `failed` — covers the case where CrewAI
crashes mid-tool and never fires the matching `*_Finished` event.

---

## 14. Database schema

All tables in the `control_plane` schema. Migrations under
`migrations/`.

### 14.1 `role_assignments` (016, 019)

The hand-pin + auto-promotion overlay.

| Column | Type | Notes |
|---|---|---|
| role | TEXT NOT NULL | e.g. `commander`, `coding` |
| mode | TEXT NOT NULL | One of the 6 runtime modes (was `cost_mode` pre-019) |
| model | TEXT NOT NULL | Catalog key — must exist in live `CATALOG` |
| priority | INT NOT NULL DEFAULT 100 | ≥1000 = hand-pin |
| source | TEXT NOT NULL | `manual`/`auto_promotion`/`governance`/`rebenchmark` |
| reason | TEXT | Free-form |
| assigned_by | TEXT | `user`/`user:dashboard`/`system`/etc. |
| active | BOOLEAN NOT NULL DEFAULT TRUE | |
| created_at | TIMESTAMPTZ | |
| retired_at | TIMESTAMPTZ | |

Primary key: `(role, mode, model)`.

### 14.2 `model_promotions` (018)

Sticker-list of "first-choice" models. The resolver's layer-2 filter
reads this.

| Column | Type | Notes |
|---|---|---|
| model | TEXT PRIMARY KEY | Catalog key |
| promoted_by | TEXT | `discovery`/`governance`/`user:dashboard` |
| reason | TEXT | |
| promoted_roles | TEXT[] | Optional role-specific scope |
| created_at | TIMESTAMPTZ | |

### 14.3 `external_ranks` (017)

Cached per-model external metrics. Refreshed daily by
`llm-refresh-external-ranks` idle job.

| Column | Type |
|---|---|
| model | TEXT NOT NULL |
| source | TEXT NOT NULL (`aa`/`openrouter`/`hf`) |
| metric | TEXT NOT NULL (`intelligence`/`coding`/`cost_out`/etc.) |
| value | NUMERIC |
| recorded_at | TIMESTAMPTZ |

### 14.4 `discovered_models` (011)

Audit trail of discovery runs — what got considered, dominated, or
rejected.

### 14.5 `crew_task_spans` (022)

Fine-grained event log inside a crew run.

| Column | Type | Notes |
|---|---|---|
| id | BIGSERIAL PRIMARY KEY | |
| task_id | TEXT NOT NULL | FK → crew_tasks.id, ON DELETE CASCADE |
| parent_span_id | BIGINT | Self-FK for tree |
| span_type | TEXT NOT NULL | `agent`/`tool`/`llm_call` |
| name | TEXT NOT NULL | Role / tool name / model id |
| crewai_event_id | TEXT | For Started→Finished pairing |
| started_at, completed_at, state | … | |
| detail | JSONB DEFAULT `'{}'` | Tool args preview, token usage, etc. |
| error | TEXT | |

---

## 15. File inventory

| File | Role |
|---|---|
| `app/llm_catalog.py` | Catalog dict + `RUNTIME_MODES` + policy dicts + resolver |
| `app/llm_catalog_builder.py` | Auto-population from AA + OpenRouter + Ollama |
| `app/llm_mode.py` | Runtime-mutable mode singleton (`get_mode`/`set_mode`) |
| `app/llm_factory.py` | Single LLM gateway (`create_specialist_llm`, `create_vetting_llm`) |
| `app/llm_selector.py` | Selector with env overrides + difficulty + ContextVars |
| `app/llm_role_assignments.py` | DB overlay (hand-pins + auto-promotions) |
| `app/llm_promotions.py` | Promotion CRUD |
| `app/llm_discovery.py` | Pareto-dominance discovery + governance gating |
| `app/llm_external_ranks.py` | AA / OpenRouter / HF blending |
| `app/llm_benchmarks.py` | Per-call telemetry + score aggregation |
| `app/llm_rehydrate.py` | Rebuild CATALOG from snapshot at boot |
| `app/llm_sampling.py` | Phase-tuned sampling params (creative-mode) |
| `app/llm_context.py` | Token-budget management |
| `app/vetting.py` | 4-tier risk-based output verification |
| `app/crews/span_events.py` | CrewAI event-bus → crew_task_spans bridge |
| `app/control_plane/crew_task_spans.py` | Span persistence + retention |
| `app/control_plane/dashboard_api.py` | `/api/cp/llms/*` + `/api/cp/tasks/{id}/timeline` |
| `migrations/011_llm_discovery_schema.sql` | discovered_models |
| `migrations/016_llm_role_assignments.sql` | overlay table (col was `cost_mode`) |
| `migrations/017_llm_external_ranks.sql` | rank cache |
| `migrations/018_model_promotions.sql` | promotion list |
| `migrations/019_unified_runtime_mode.sql` | rename cost_mode → mode |
| `migrations/022_crew_task_spans.sql` | task-flow spans |

---

## 16. Operations

### 16.1 Switching mode

```bash
# Dashboard: LLMs tab → Runtime Mode card → click any of the 6 buttons.

# Signal:
mode quality

# API (requires GATEWAY_SECRET):
curl -X POST -H "Authorization: Bearer $GATEWAY_SECRET" \
     -H "Content-Type: application/json" \
     http://localhost:8765/config/llm_mode \
     -d '{"mode":"insane"}'
```

### 16.2 Pinning a model

```bash
# Dashboard: LLMs tab → click "📌 pin to role" on any model card.

# Signal:
pin commander balanced claude-opus-4.7

# API:
curl -X POST -H "Authorization: Bearer $GATEWAY_SECRET" \
     -H "Content-Type: application/json" \
     http://localhost:8765/api/cp/llms/pin \
     -d '{"role":"commander","mode":"balanced","model":"claude-opus-4.7","reason":"prefer Opus reasoning"}'
```

### 16.3 Refreshing the catalog

```bash
# Idle scheduler runs llm-refresh-catalog every 24 h. Manual trigger:
# Signal:
refresh catalog

# API:
curl -X POST -H "Authorization: Bearer $GATEWAY_SECRET" \
     http://localhost:8765/api/cp/llms/discovery/run
```

### 16.4 Inspecting current resolver state

```bash
# Signal:
status

# API (returns 367 models + role assignments + active mode):
curl -H "Authorization: Bearer $GATEWAY_SECRET" \
     http://localhost:8765/api/cp/llms/catalog
```

### 16.5 Watching a task flow live

Dashboard → Tasks tab → click any row. Drawer opens; toggle 🌳 Tree
/ ⏱️ Timeline. Polls every 2 s while the task is running.

---

## 17. Failure modes & recovery

| Failure | What happens | Why it's safe |
|---|---|---|
| All three fetchers (AA, OpenRouter, Ollama) down | Builder skips refresh, snapshot stays stale | 24-h TTL gives long grace; live `CATALOG` keeps last good state |
| Snapshot file missing or corrupt | Falls back to `_BOOTSTRAP_CATALOG` (3 entries) | System still boots, commander/vetting/critic resolve to Sonnet |
| Postgres unreachable | `role_assignments` queries return `None`; resolver layer 1 silently skips | Layer 3 pool scoring still works |
| CrewAI version doesn't expose event types | `span_events.install_listeners()` logs warning, sets installed flag, returns | Crews still run; just no spans get persisted |
| Hand-pin points at retired model | `set_assignment()` rejects writes that aren't in live `CATALOG`; old pins surface as stale | Resolver layer 1 verifies `pin in CATALOG` before returning |
| Vetting hangs | Bounded `llm.call()` timeout (~30 s) | Parent task's soft-timeout still fires |
| Span never finishes (CrewAI bus crash) | 10-min watchdog `close_stale_spans` marks them failed | Dashboard doesn't show eternally-running spans |
| Discovery promotes the wrong model | Demote button on dashboard or `demote <model>` Signal | Catalog rehydrates immediately |

---

## 18. Test surfaces

| Test file | Covers |
|---|---|
| `tests/test_llm_catalog.py` | Catalog structure, resolver, role policy, planner/introspector wiring, unified-mode vocabulary |
| `tests/test_llm_catalog_builder.py` | Snapshot building, strength derivation, fetcher fallbacks |
| `tests/test_llm_selector_routing.py` | difficulty_to_tier, detect_task_type |
| `tests/test_llm_role_assignments.py` | Pin/unpin, set_assignment, mode aliasing |
| `tests/test_llm_promotions_and_pins.py` | Promotion CRUD, hand-pin layered priority |
| `tests/test_llm_external_ranks.py` | AA / OR / HF fetchers, blend weighting |
| `tests/test_llm_rebenchmark.py` | Incumbent rotation, judge selection |
| `tests/test_llm_discovery.py` | Pareto-dominance, governance gating |
| `tests/test_llm_telemetry.py` | Per-call recording, ContextVar hygiene |
| `tests/test_vetting_feedback.py` | Vetting failure → tier bump |
| `tests/test_crew_task_spans.py` | Span persistence, ContextVar correlation, event-map roundtrip |

Run the whole LLM suite with:

```bash
pytest tests/test_llm_*.py tests/test_vetting_feedback.py tests/test_crew_task_spans.py -v
```

---

## 19. Glossary

| Term | Meaning |
|---|---|
| **Catalog** | The live `dict[str, dict]` of all known models. Mutated in place by the builder. |
| **Bootstrap** | The 3-entry survival catalog. Always present even with zero connectivity. |
| **Resolver** | `resolve_role_default(role, mode)` — the score-based pick function. |
| **Selector** | `select_model(role, task_hint)` — adds env overrides, difficulty, ContextVars on top of the resolver. |
| **Factory** | `create_specialist_llm(...)` — single instantiation gateway. |
| **Hand-pin** | A `priority≥1000` row in `role_assignments`. Returned directly from the resolver, no scoring. |
| **Promotion** | A row in `model_promotions`. Filters the candidate set down to "first-choice" models when any survive. |
| **Mode** | The unified runtime axis: `free`/`budget`/`balanced`/`quality`/`insane`/`anthropic`. |
| **Tier** | `local`/`free`/`budget`/`mid`/`premium`. Mode controls which tiers are allowed. |
| **Strengths map** | Per-model dict from the 9 canonical task types to a 0-1 score. Derived from AA evaluations. |
| **External rank** | A third-party metric (AA intelligence, OpenRouter cost, etc.) blended into the resolver's quality signal. |
| **Vetting** | `app/vetting.py` — 4-tier risk-based output check. Vetting model itself is selected via the resolver with `role="vetting"`. |
| **Span** | A `crew_task_spans` row. One per agent-execution / tool-call / llm-call inside a crew run. |
| **Span watchdog** | `close_stale_spans` — marks spans stuck in `running` past 10 minutes as `failed`. |
| **Incumbent** | The current top-scoring model for a role + mode pair. Discovery proposes replacements. |
| **Pareto dominance** | Quality ≥ AND cost ≤ (with at least one strict inequality, mode-weighted). |

---

## 20. Cross-references

- High-level system architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Control-plane patterns: [`CONTROL_PLANES.md`](CONTROL_PLANES.md)
- Self-improvement pipeline (uses LLM-as-Judge for evolution variants): [`SELF_IMPROVEMENT.md`](SELF_IMPROVEMENT.md)
- Dashboard surfaces (LLMs tab + Tasks tab + drawer): React app at `dashboard-react/src/components/{LlmsPage,TasksPage,TaskFlowDrawer}.tsx`
