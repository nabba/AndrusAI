# OpenRouter Fusion (multi-model deliberation)

Native OpenRouter **Fusion** — server-side Mixture-of-Agents (launched 2026-06-12).
One completion fans out to a *panel* of diverse models in parallel, a *judge*
compares their answers (consensus / contradictions / unique insights / blind
spots) and a synthesised final answer comes back. ~4–5× the cost of a single
call for a 3-model panel. **Default OFF; nothing fuses until you enable it AND
add roles.**

## Architecture

Driven entirely through the existing factory — no new LLM client, no
client-side fan-out (OpenRouter owns the parallelism).

- **Hook:** `ChatCompletionHandle.create()` in `app/llm_factory.py` already
  forwards `**kwargs` (incl. `extra_body`) to `litellm.completion`, and
  `_apply_openrouter_provider_exclusion` only touches `extra_body["provider"]`.
  Fusion rides the same channel: `extra_body={"plugins":[{"id":"fusion",
  "analysis_models":[…],"model":<judge>}]}`. Fail-open — any error degrades to a
  normal single-model call. Ollama (local) targets never reach the hook.
- **Panel resolver (`app/fusion/panel.py`) — the "LLM chooser":** a *class* is a
  vendor family (`google` / `qwen` / `moonshotai` / `deepseek`). For each class
  we filter the live `CATALOG` to that vendor's non-retired, non-blocked
  OpenRouter entries (`model_id` prefix `openrouter/<vendor>/`), prefer a
  variant-hint slug (`flash`), and rank tier → general-strength → cheaper.
  **No hardcoded model ids** — when a new Gemini Flash ships it wins
  automatically. Alias map accepts `gemini`/`kimi` etc.
- **Two integration paths:**
  | | raw (`chat_completion_for_role`) | agent (CrewAI `create_*_llm`) |
  |---|---|---|
  | where | `ChatCompletionHandle.create` | `_try_api` build site |
  | mode | **deterministic** (`tool_choice="required"`) | **offered-not-forced** (no `tool_choice`) |
  | tools | pure-generation calls | composes with the agent's own tools |
  | metered | yes (per-day cap) | no (cached LLM, reused) |
  | switch | `fusion_enabled` + scope | `fusion_enabled` + scope + `fusion_agent_path_enabled` |

  Forcing fusion on a tool-calling agent would block its tools, so the agent
  path is offered-only and gated behind its own switch. The agent LLM is cached
  by *model* (not role), so `agent_extra_body(role)` bakes the plugin at build
  time (role known there) and forks the cache key (`|fusion`).

## Switches (`runtime_settings`, via `/cp/settings`)

| key | default | meaning |
|---|---|---|
| `fusion_enabled` | `false` | master switch |
| `fusion_scope_roles` | `[]` | roles whose calls fuse — **empty = nothing fuses** |
| `fusion_panel_classes` | `["google","qwen","moonshotai","deepseek"]` | vendor families |
| `fusion_variant_hints` | `{"google":"flash","qwen":"max","moonshotai":"kimi"}` | slug preference per class |
| `fusion_panel_pins` | `{}` | per-class explicit model-id override |
| `fusion_judge_id` | `""` | judge model id; `""` = OpenRouter default (Claude Opus class) |
| `fusion_max_panel` | `4` | panel cap (1–8) |
| `fusion_daily_cap_usd` | `10.0` | per-day fusion spend cap (under the monthly ceiling; auto-off when the brake engages) |
| `fusion_agent_path_enabled` | `false` | also fuse CrewAI agent calls (offered-not-forced, unmetered) |

## Operator surfaces

- **`/cp/settings` → Fusion card:** toggle, scope multiselect, resolved-panel
  preview (current champion per class), judge, caps, agent-path opt-in.
- **Dashboard main page:** a `🔀 Fusion ON …` chip when enabled (shows the
  panel + judge, or "no roles selected (idle)").
- **`/cp/fusion` page:** live state summary + the judge's recorded deliberations.
- **REST:** `GET /api/cp/fusion/state`, `GET /api/cp/fusion/deliberations?limit=N`.

## Deploy

The work is currently **uncommitted** on a shared branch, so deploy the working
tree directly (no commit needed). Two artifacts — gateway (Python) and the
React dashboard — rebuild separately. **Run on the Mac host.**

```bash
cd ~/BotArmy/crewai-team

# 1. Gateway (fusion module + factory hook + REST + settings) — working tree as-is
./scripts/deploy_gateway.sh --no-pull         # docker compose up -d --build gateway (+ watchdog)

# 2. React dashboard (Fusion card + chip + /cp/fusion page)
npm --prefix dashboard-react run build         # tsc -b && vite build → dashboard/serve-root/cp
#   then hard-refresh the dashboard (or restart the dashboard LaunchAgent)
```

## Verify live (1–2 PAID calls)

After the gateway rebuild includes `app/fusion/`:

```bash
docker exec -i gateway python -m app.fusion.selftest            # passthrough: plugins + tool_choice → wire
docker exec -i gateway python -m app.fusion.selftest --factory  # +1 call: the real factory hook end-to-end
```

It prints PASS/FAIL **and the response shape** — confirm the judge's deliberation
location and, if it lands somewhere `app/fusion/observe.py:_extract` doesn't yet
probe, add that key. If the plugin form 400s, switch `apply.py` to the
`model:"openrouter/fusion"` alias form.

## Activate

1. Deploy (above) → run the selftest → confirm PASS.
2. `/cp/settings` → Fusion → toggle **On**. (Still idle — no roles yet.)
3. Tick one or more **roles** (e.g. `research`, `synthesis`, `writing`). The
   Dashboard chip lights up; `/cp/fusion` starts logging deliberations.
4. Optional: enable **agent-path** fusion (advanced) for CrewAI agent calls.

## Rollback

- **Soft (instant, no rebuild):** `/cp/settings` → Fusion → **Off**. The master
  switch gates everything; back to single-model behaviour immediately.
- **Code:** `git restore` the modified files + remove `app/fusion/`,
  `dashboard_routes_fusion.py`, `FusionCard.tsx`, `FusionPage.tsx`, then redeploy.
  (Additive + default-OFF, so leaving it deployed-but-off is also a no-op.)

## Status (2026-06-16) — DEPLOYED + live-verified

Deployed to the running gateway (working-tree build via
`deploy_gateway.sh --no-pull`) + React bundle synced. Live checks on the
production gateway:

- `GET /api/cp/fusion/state` → the resolver picked real champions per class:
  `gemini-3.5-flash` · `qwen3.6-max-preview` · `kimi-k2-thinking` ·
  `deepseek-r1-0528`. `enabled:false` (inert until you opt in).
- **Fusion genuinely engages** (plugin + `tool_choice="required"`): a 2-model
  panel cost **6613 tokens / $0.023** vs **96 tokens / $0.0012** single-model on
  the same prompt — ~20×. (The headline "4–5×" is a 3-model panel on a long
  answer; short prompts run higher because the judge re-ingests every panel
  output.)
- **Cost is metered on the ACTUAL `response_cost`** in
  `observe.record_response`, not a naive `panel_size+1` estimate (which
  under-counted ~6× and would have made the $/day cap that much too loose).
- **The judge's structured deliberation is NOT exposed by the API** — the
  completion returns only the final answer + usage, and `GET /api/v1/generation`
  404s. So `/cp/fusion` shows panel / answer / usage / cost, not the
  consensus-vs-contradictions breakdown. `observe._extract` still probes for it
  and will pick it up if OpenRouter starts surfacing it.
- The bare `model:"openrouter/fusion"` alias 502'd (Stealth provider) in a raw
  probe that skipped provider-exclusion; the **plugin form is the deployed path**
  and works.

38/38 `tests/test_fusion.py`, React `tsc -b` clean, gateway `/health` 200
post-deploy. The work is **uncommitted** on shared branch `feat/deploy-poller`
(working-tree deploy); container/CI-only tests (route mount, dispatcher pinning,
route-invariants) are not host-runnable here.
