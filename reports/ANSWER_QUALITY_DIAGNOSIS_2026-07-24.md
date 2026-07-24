# AndrusAI Answer-Quality Diagnosis — 2026-07-24

**Trigger:** "please make me a report on estona forest health and deforestation data over the years…" → user received:
> "Sorry — I wasn't able to put together an answer for that. The specialist step(s) handling it didn't finish in time or failed: research (Timed out); writing (Timed out)."

**Verdict in one sentence:** The system **built the report — on both attempts — and threw it away both times**; the serving layer (parallel dispatch + 120s cap + no late delivery) is the primary failure, and beneath it sit ~15 further defects that would degrade the answer even when delivered.

---

## 1. Incident forensics (evidence from audit.log, gateway logs, control_plane.crew_tasks)

### Attempt 1 — 2026-07-23
| Time (UTC) | Event |
|---|---|
| 10:27:34 | request_received (trace 95119b3d162d) |
| 10:28:07 | Commander routed to **deep_research + writing, dispatched in parallel** |
| 10:30:07 | `run_parallel: timeout 120s reached` — both crews still running |
| 10:30:11 | Apology sent to user (229 chars) |
| 10:34:03 | deep_research **finished ok** — "# Estonia Forest Health and Deforestation: Evidence-Audited Report…" → orphaned, discarded |
| 10:34:33 | writing **finished ok** — "# Estonian Forest Health & Forestry Industry: A Critical Assessment…" → orphaned, discarded |

### Attempt 2 — 2026-07-24
| Time (UTC) | Event |
|---|---|
| 07:15:58 | request_received (trace b9583896fd32) |
| 07:16:36 | Routed to **research + writing, dispatched in parallel** (38 s routing/queue overhead) |
| 07:18:36 | `run_parallel: timeout 120s reached` |
| 07:18:37 | Apology sent (224 chars) |
| 07:22:21 | writing **finished ok=True after 345 s** — full report "Estonia's Forests Under Pressure: A Critical Analytical Report…" ($0.32, 249k tokens) → discarded |
| 07:27:29 | research **finished ok=True after 652 s** — "# 🌲 Estonia Forest Health & Deforestation: Comprehensive Research… 51.5% of land, ~2,334,177 ha (2023 SMI)…" ($0.84, 893k tokens) → discarded |

Across two attempts: **4 complete deliverables produced, ~$1.35 spent, 0 delivered.** The orphaned-result log line fired all 4 times: `orphaned crew '…' finished AFTER its caller already gave up and returned a response`.

---

## 2. Root causes, ranked

### A. Delivery killers — why you got an apology instead of a report

**A1. 120 s head-of-line cap on every multi-crew dispatch.**
`app/crews/parallel_runner.py:30` — `_PARALLEL_DEFAULT_TIMEOUT = int(os.environ.get("PARALLEL_CREW_TIMEOUT", "120"))`, unset in the container env, so 120 s is live. The comment claims "covers even a slow hard task" — measured runtimes were 345 s and 652 s. The user-facing ETA table (`app/conversation_store.py:414`) even sets `research: 120` — the ETA *is* the give-up deadline, not the runtime.

**A2. Completed-late results are structurally discarded.**
`parallel_runner.py:141-155` — the only action on an orphaned future is `logger.warning(...)`. Nothing routes the finished result to Signal, the outbound queue, or anywhere. `parallel_runner.py:157-166` replaces missing labels with `ParallelResult(success=False, error="Timed out")`, and `orchestrator.py:650-667` turns all-failed into the apology. There is **no redelivery mechanism anywhere in the codebase**.

**A3. The router splits report requests into parallel *independent* crews — which is exactly what forces them onto the capped path.**
`orchestrator.py:1069-1070` — routing LLM returns a flat `{"crews":[…]}` list, no `depends_on`, no ordering, no hand-off channel. The routing prompt (`routing.py:886-901`) literally uses research+writing as its multi-crew example ("independent parts"). Dispatch: single crew → uncapped path (`orchestrator.py:3295`); 2+ crews → `run_parallel` with the 120 s cap (`orchestrator.py:4019, 4080`).

**A4. Deep-research promotion is neutralized by the multi-crew branch.**
Attempt 1 proves it: the request *was* promoted to `deep_research` (2400 s internal budget, `runtime_settings.py:851`) — but because `writing` rode along, dispatch entered the multi-crew branch and the outer 120 s wall still applied. Promotion only renames the crew (`deep_path.py:171-203`); it does not change the dispatch topology.

**A5. The purpose-built long-report pipelines are unreachable from plain language.**
- `/delegate research|paper` (the §84 idea→verified-citation→LaTeX pipeline — the closest thing to "answer like Claude would") is gated on the literal slash prefix (`commands.py:76, 2909`). A plain "make me a report on X" can never reach it.
- `company_dossier` fast-route requires keywords `dossier` / `due diligence` / `investment-grade` (`routing.py:175-184`).
- deep_research auto-promotion scorer (`deep_path.py:107-165`) gave this phrasing only ~2-3 points vs threshold 4 on attempt 2 — "report on X over the years… evaluate critically" earns no depth/review-shape points.

### B. Answer-quality killers — why even a delivered answer would be mediocre

**B1. The writer works blind.** Parallel crews each get only their router-authored task string + last 3 conversation turns (`orchestrator.py:3290-3292, 4023-4036`). There is no channel for research findings to reach the writing crew. The writing crew's template (`writing_crew.py:11-22`) has **no grounding, citation, or anti-fabrication discipline** — it writes the "critical analytical report" from model weights.

**B2. Research is content-starved by tool caps.** `web_search` returns top-5 snippets with a **hard budget of 6 calls per task** (`web_search.py:58, 280`); a lexical-overlap filter silently drops results (`search_validation.py:67-70`); `web_fetch` caps page text at 12,000 chars (`web_fetch.py:180`); firecrawl scrape/search cap at 8,000 / 5×2,000 chars (`firecrawl_tools.py:82, 251`). A "comprehensive critical report" is assembled from a few KB of snippets. (Firecrawl/searxng containers showed zero traffic during the 11-minute research run — the crew leaned on web_search/research_orchestrator.)

**B3. Everything is tuned for phone-length answers, against report intent.**
- Research template: *"Aim for 200-400 words, not 2000"*, *"Do NOT write a 'Unified Report'"* (`research_crew.py:68-72`).
- Routing prompt: *"Keep answers SHORT… NOT a report"* (`routing.py:904-909`).
- Research synthesis feeds only `combined_input[:6000]` at `max_tokens=4096` and again demands 200-400 words (`research_crew.py:539-549`).
- Multi-crew merge truncates to `raw_combined[:6000]`, `max_tokens=4096` (`orchestrator.py:632-637`).
- Writer LLM `max_tokens=4096` (`writer.py:62`).

**B4. Vetting is a 6,000-char plausibility check, not verification.** `_verify_full` sees `response[:6000]` only (`vetting.py:418`); cannot fetch sources; a timeout = PASS unvetted; a "corrected" replacement may legally shrink the report to 50%+1 chars (`vetting.py:472`). The real `citation_verifier` exists (`app/research/citation_verifier.py`) but is wired only into the `/delegate` research spine, not `ResearchCrew`/`WritingCrew`.

### C. Latency amplifiers — why the crews took 5–11 minutes

**C1. Nested `run_parallel` semaphore self-starvation (dominant).** One global 2-slot semaphore (`ollama_max_concurrent_crews=2`, `config.py:218`) and one 6-worker pool (`config.py:85`) are shared by outer crews AND inner sub-agents. The outer dispatch (research + writing) holds both slots for the crews' full runtimes; research's subtopic fan-out (`research_crew.py:466`) then re-enters the same semaphore (`parallel_runner.py:83`) and queues behind the *writing* crew. This explains research 652 s ≫ writing 345 s. Two fan-out crews dispatched together can approach true deadlock.

**C2. Pre-dispatch overhead.** 38 s from request_received to dispatch (routing LLM at max_tokens=1024 + history/Mem0 loaders at 5 s timeouts each + wiki/temporal/spatial context). Each crew then pays a ~6 s 12-loader RAG ctx_load (`orchestrator.py:1337-1395`).

**C3. Loop multipliers.** Researcher `max_iter=20` × advisory `max_execution_time=300` (`researcher.py:198-204` — advisory only, hence 652 s); difficulty≥8 adds a debate round (3 extra LLM calls); `research_orchestrator` allows **1,500 s** in a single tool call (`research_orchestrator.py:606`); sub-agent retries with backoff; on the single-crew path a failed quality gate triggers up to 3 full crew re-runs (reflexion, `orchestrator.py:2328-2453`).

### D. Model-selection layer defects

**D1. A stale promoted model breaks writing-role selection on every call.** `llm_rehydrate.py:107-132` re-injects `control_plane.discovered_models` rows into the catalog each boot; a pre-consolidation row yields key `claude-haiku-4-5` with `provider="anthropic"`, which the factory can no longer construct (`llm_factory.py:561` → `unknown_provider`). Because promoted models win the role-default filter (`llm_catalog.py:689-699`), **every writing call selects an unbuildable model, fails construction, and silently lands on bootstrap fallback claude-sonnet-4.6**. `workspace/llm_catalog_overrides.json` still carries the bad remap `"claude-4.5-haiku": "anthropic/claude-haiku-4-5"`. Selection, cost prediction, and telemetry contracts are all wrong (fallback happened to be a *better* model here — pure luck).

**D2. Difficulty-7 research is hard-capped at mid tier.** `_ROLE_DIFFICULTY_TIER_FLOOR` (`llm_selector.py:182-188`): research d=7 → `force_tier="mid"` → glm-5.2; premium requires d≥8. The force_tier short-circuit (`llm_selector.py:702-736`) bypasses mode weighting, benchmarks, and budget logic entirely — even `quality` mode can't lift it. Your hardest research work is pinned to a $2.48/M mid-tier model one difficulty point short of premium.

**D3. Credit-exhaustion storms silently gut quality.** 2026-07-21/22: ~13,500 OpenRouter **HTTP 402 "Insufficient credits"** errors; the rate-throttle failover retried every one with **`ollama/llama3.1:8b` (max_tokens=2048)** — a model the codebase itself documents as unable to handle tool calls (`llm_selector.py:209-211`) — and local Ollama's circuit breaker was simultaneously flapping. ~32k failovers to this target exist in the log history. Not active on the report day, but it is a recurring catastrophic-quality mode with no auto-top-up/alert loop that actually prevents it.

**D4. Message-ordering 400s lock the best models out of the native-tools path.** `crewai.flow call_llm_native_tools` builds message arrays with a system message *after* assistant messages → Anthropic-side `invalid_request_error` (seen on `openrouter/anthropic/claude-fable-5`, 12×, plus 41 prefill-shape 400s). Calls to the strongest models fail and cascade downward.

**D5. Rising max_tokens truncation.** 221 truncation-guard events on 07-24 (64 on 07-22) — mostly idle `training_collector` at max_tokens=256, but including sonnet/glm user-path completions. Truncated completions = clipped answers.

**D6. Cascade noise.** `google/gemma-4-31b-it` returned ~400 HTTP-500s in 3 days; `openrouter/auto-beta` / `pareto-code` 502s — retry burn on models that should be health-cached out.

### E. Substrate noise (indirect quality drag)

- **memory KB replay is failing continuously**: `source_ledger.replay_kb: batch upsert failed kb=memory` (1,100+ rows in the recent tail, still firing at 13:09 today) — the RAG memory the crews load context from is degraded/incomplete.
- **neo4j unreachable windows** (368 connection errors around 01:33) — belief/graph context absent during those periods.
- **Creative-crew budget/attribution anomaly**: a `creative` run at 07:15:10 recorded 1.68M tokens / $0.475 in 0.2 s and aborted "hit its $0.10 budget before producing output (input ~233 tokens)" — cross-run tracker attribution looks broken (distinct from the June input-aware fix).
- **Observability gaps**: `trace_id` empty on all crew_dispatch/phase rows; no per-tool call logging; the OTel exporter can't reach its collector.

---

## 3. Why Claude-in-chat gives a better answer (the honest comparison)

For this exact request, a single strong model with a web tool does: search → read → think → write one long grounded document, iterating until done, with no 120 s wall, no 6-search budget, no 6,000-char merge clamp, no parallel writer working blind, and it *delivers whatever it produced*. AndrusAI actually has an equivalent pipeline (`/delegate paper`: literature → verify citations → compose → LaTeX) — it is simply unreachable from a natural-language ask, and the default chat path is tuned for 200-400-word phone replies.

---

## 4. Recommended fixes, in leverage order

1. **Deliver late results.** In the orphan callback (`parallel_runner.py:141-155`), on `ok=True` push the result through the normal postprocess/send_durable path as a follow-up message ("here's the report that finished late"). This single change converts both incidents into successes and is the cheapest systemic fix. Add an honest progress message when dispatching research-class work ("this will take ~10 min").
2. **Stop parallelizing dependent steps.** Teach routing/dispatch a pipeline shape (research → writing consumes research output), or route report-intent to a *single* crew (deep_research) so it uses the uncapped single-crew path. A `depends_on` field on the routing schema + sequential execution of dependents is the minimal version.
3. **Make the timeout intent-aware.** 120 s is fine for chat-shaped multi-crew asks; report/research-class dispatches need the crew's real budget (e.g. per-crew ETA × margin, or 900–1800 s), with the progress ping from #1.
4. **Fix the nested-semaphore starvation.** Don't hold the outer semaphore slot while sub-agents run (release around inner `run_parallel`), or give inner fan-out its own semaphore/pool tier.
5. **Purge the stale promoted model + remap.** Delete/demote the `anthropic/claude-haiku-4-5` row in `control_plane.discovered_models`, remove the `model_id_remaps` entry in `workspace/llm_catalog_overrides.json`, and make `rehydrate_catalog` validate provider ∈ {openrouter, ollama} (drop + alert otherwise).
6. **Lift research d=7 to premium** (or dispatch report-class research at d≥8): one line in `_ROLE_DIFFICULTY_TIER_FLOOR`.
7. **Make report intent change the output contract.** When the router detects report intent: pass task-type-aware templates (drop "200-400 words" / "NOT a report"), raise writer/synthesis max_tokens, replace the `[:6000]` merge clamp with map-reduce over full texts, and skip the shrink-permitting vetting rewrite in favor of the existing `citation_verifier`.
8. **Route plain-language report asks to the paper/deep-research pipeline.** Lower the deep-research promotion threshold for "report/analyze/evaluate over the years" phrasings, and/or let the router emit `crew="deep_research"` directly with `writing` chained after it — reusing `/delegate research verify compose` machinery.
9. **Ops hygiene:** 402 storm → alert + hard-pause paid cascade instead of 13k failovers to a tool-incapable 8B model; fix the native-tools message-ordering bug (system-after-assistant) so fable-5/sonnet native-tool calls stop 400ing; health-cache gemma-4-31b out of the cascade; fix the memory-KB `replay_kb` upsert failures; give crews real trace_ids.

---

*Produced 2026-07-24 by a three-track code+log audit (routing/dispatch, crew internals, LLM selection) with live verification against gateway logs, `workspace/audit.log`, `workspace/logs/errors.jsonl`, and `control_plane.crew_tasks`.*
