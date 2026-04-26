# AndrusAI Tech Radar

> **Status**: Production-deployed.
> **Purpose**: continuously scan the public web for new LLM models, agent
> frameworks, research papers, and tools relevant to the system, then route
> findings to the right consumer — model catalog, evolution prompts, Signal
> notifications, dashboard, and the human operator.

This document is the canonical reference for the tech-radar subsystem: where
it runs, what it produces, what consumes it, how it fails, and how to
operate it.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Component Map](#2-component-map)
3. [Lifecycle](#3-lifecycle-one-scan-end-to-end)
4. [Search Backend Cascade](#4-search-backend-cascade)
5. [Discovery Storage Layers](#5-discovery-storage-layers)
6. [Model Catalog Integration](#6-model-catalog-integration)
7. [Notification & Display Surfaces](#7-notification--display-surfaces)
8. [Idle Scheduling](#8-idle-scheduling)
9. [Configuration Surfaces](#9-configuration-surfaces)
10. [Failure Modes](#10-failure-modes)
11. [Operating Procedures](#11-operating-procedures)
12. [Test Coverage](#12-test-coverage)
13. [Design History](#13-design-history)

---

## 1. Overview

### What it does

Once per HEAVY-weight idle cycle, tech radar:

1. Issues seven topical web searches across **models / frameworks / research
   / tools** (`_SEARCH_QUERIES` in `app/crews/tech_radar_crew.py`).
2. Hands the aggregated results to a research-tier LLM with a
   deduplication prompt — only return what isn't already in
   `scope_tech_radar` memory.
3. For each new discovery: stores it in ChromaDB, pings the user via
   Signal if `relevance == "high"`, and (for models) plants a stub in
   the LLM-catalog discovery pipeline.

### Why it exists

The agent system runs against a moving target — new models ship every
week, agent frameworks evolve, and self-improving-agent research keeps
inventing primitives we want to absorb. Without tech radar the system
would only ever know about the static catalog it shipped with.

### Where the outputs end up

| Output | Destination | Consumer |
|---|---|---|
| Free-form discovery text | ChromaDB `scope_tech_radar` collection | Evolution prompt, dashboard tab, commander `tech radar` command |
| Model-shaped discovery | `control_plane.discovered_models` (PostgreSQL) as a stub row with `source='tech_radar'` | OpenRouter scan enriches → benchmarking → governance promotion → runtime catalog |
| High-relevance discovery | Signal message to the operator | Human |
| Search-backend health | `search_status` field on the dashboard endpoint | React tech-radar tab banner |

---

## 2. Component Map

```
                ┌──────────────────────────────────────────────┐
                │  app/idle_scheduler.py                       │
                │  HEAVY job ("tech-radar")                    │
                └───────────────────────┬──────────────────────┘
                                        │ ~once per HEAVY-rotation slot
                                        ▼
        ┌─────────────────────────────────────────────────────────┐
        │  app/crews/tech_radar_crew.py :: run_tech_scan()        │
        │  • 7 topical queries via app/tools/web_search.py        │
        │  • LLM analysis + dedupe vs scope_tech_radar memory     │
        │  • Per-discovery: store + (models) plant stub +         │
        │    (high) Signal notify                                 │
        └───────┬────────────────┬────────────────┬───────────────┘
                │                │                │
                │ store_scoped   │ _store_stub    │ _notify_user_discoveries
                ▼                ▼                ▼
       ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐
       │  ChromaDB    │  │ PostgreSQL     │  │ Signal CLI      │
       │  scope_tech_ │  │ discovered_    │  │ → operator      │
       │  radar       │  │ models         │  │   number        │
       └──────┬───────┘  └────────┬───────┘  └─────────────────┘
              │                   │
              │                   │ (status='discovered',
              │                   │  cost=0, ctx=0,
              │                   │  source='tech_radar')
              │                   ▼
              │          ┌────────────────────────────┐
              │          │  Next idle cycle           │
              │          │  scan_openrouter()         │
              │          │  → _store_discovered with  │
              │          │    real cost/context       │
              │          │  → ON CONFLICT enriches    │
              │          │    stub (source preserved) │
              │          │  → benchmarking +          │
              │          │    governance promotion    │
              │          └────────────────────────────┘
              │
              ├──── /api/cp/tech-radar ────► React TechRadarTab
              │     (dashboard_api.py)         (LlmsPage.tsx)
              │
              ├──── status/tech_radar ─────► Firestore →
              │     (firebase/publish.py)     legacy dashboard
              │
              └──── get_recent_discoveries ──► evolution.py prompt context
                                              commander/commands.py "tech radar"
```

### Key files

| Path | Role |
|---|---|
| `app/crews/tech_radar_crew.py` | Scan loop, LLM analysis, dispatch to all sinks. |
| `app/tools/web_search.py` | Three-tier search cascade (Brave → SearXNG → DDG) + health surface. |
| `app/llm_discovery.py` | `_store_stub()`, anti-poison `_get_known_model_ids()`, `_store_discovered()` ON CONFLICT enrichment. |
| `app/idle_scheduler.py` | Registers `tech-radar` as a HEAVY-weight job. |
| `app/control_plane/dashboard_api.py` | `/api/cp/tech-radar` REST endpoint, includes `search_status`. |
| `app/firebase/publish.py` | `report_tech_radar()` writes Firestore `status/tech_radar`. |
| `app/agents/commander/commands.py` | `tech radar` text command for Signal. |
| `app/evolution.py` | Pulls 5 recent discoveries into the hypothesis-generation prompt. |
| `dashboard-react/src/components/LlmsPage.tsx` | `TechRadarTab` UI + backend-health banner. |
| `dashboard-react/src/api/queries.ts` | `TechRadarReport` / `SearchBackendStatus` types + `useTechRadarQuery`. |
| `scripts/migrate_tech_radar_proposals.py` | One-shot drainer for the legacy fileless-proposal backlog. |
| `tests/test_web_search_fallback.py` | Cascade unit tests. |
| `tests/test_llm_discovery.py` | Stub-vs-enrichment integration test. |

---

## 3. Lifecycle (one scan, end-to-end)

```
 idle scheduler picks HEAVY slot
        │
        ▼
 run_tech_scan()    [tech_radar_crew.py:40]
   │
   ├── crew_started("self_improvement", "Tech Radar scan", eta=120s)
   │
   ├── retrieve_operational(SCOPE_TECH_RADAR, ...)  ← dedupe corpus (n=20)
   │
   ├── for each (category, query) in _SEARCH_QUERIES:        # 7 queries
   │       result = web_search.run(query)                    # cascade
   │       → search_results[category].append(result)
   │
   ├── if no search_results: crew_completed("No search results"); return
   │
   ├── prompt LLM (research role) with formatted searches
   │   request JSON: [{title, category, relevance, summary, action,
   │                   openrouter_id?}]
   │
   ├── parse JSON; for each discovery (max 10):
   │       store_scoped(scope_tech_radar, "[cat] title: summary. Action: act")
   │       if relevance == "high": high_relevance.append(disc)
   │       if category == "models": _plant_model_stub(disc)
   │
   ├── if high_relevance: _notify_user_discoveries(...)      # Signal
   │
   └── crew_completed(..., tokens, cost, model)
```

### Latency / cost

- Wall time: ~30-90s per scan (dominated by 7 sequential web searches +
  one LLM call).
- LLM cost: ~$0.001-0.003 (research-tier — typically DeepSeek V3.2 at
  $0.42/Mo; one prompt of ~8KB input + ~2KB output).
- Search cost: 7 Brave API calls if Brave has quota, otherwise free
  (SearXNG/DDG).

---

## 4. Search Backend Cascade

`app/tools/web_search.py` exposes a single function `search_brave(query,
count)` whose name is preserved for backwards compatibility — every
caller across the codebase (~20 call sites in agents, atlas, evolution,
research adapters, fiction inspiration) gets the cascade transparently.

```
                 search_brave(query)
                        │
                        ▼
            ┌───────────────────────┐
            │  Tier 1: Brave        │
            │  brave-api 402 quota? │
            └───────────┬───────────┘
                        │
            ┌───────────┴────────────┐
            │ 200 OK + results       │ 402 / network error
            ▼                        ▼
       return results           record "brave:quota"
       last_backend = "brave"   set _brave_quota_blocked_until
                                = now + 24h     ← sticky backoff
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │  Tier 2: SearXNG      │
                            │  http://searxng:8080  │
                            └───────────┬───────────┘
                                        │
                            ┌───────────┴────────────┐
                            │ results                │ no results
                            ▼                        ▼
                       return results          record "searxng:no_results"
                       last_backend="searxng"        │
                                                     ▼
                                         ┌───────────────────────┐
                                         │  Tier 3: DuckDuckGo   │
                                         │  HTML scrape via bs4  │
                                         └───────────┬───────────┘
                                                     │
                                         ┌───────────┴────────────┐
                                         │ results                │ none
                                         ▼                        ▼
                                    return results            return []
                                    last_backend="ddg"        last_backend=None
                                                              full failure chain
                                                              logged + surfaced
```

### Tier semantics

- **Brave** — paid `https://api.search.brave.com/res/v1/web/search`. Best
  quality. On HTTP 402 (quota exhausted) the module marks itself blocked
  for 24h via `_brave_quota_blocked_until` so subsequent calls don't
  hammer the API. Per-process state — workers don't share, but each only
  pays one extra 402 per backoff window.
- **SearXNG** — self-hosted aggregator (Google + Bing + DDG) running in
  the firecrawl docker-compose stack. Reached by the gateway via the
  `searxng` network alias on `crewai-team_external` (declared in
  `docker-compose.firecrawl.yml`). Configurable via `SEARXNG_URL` env
  var, default `http://searxng:8080/search`.
- **DuckDuckGo HTML** — last resort. POSTs to `html.duckduckgo.com/html/`
  and parses with `bs4`. Strips DDG's `uddg=` redirector to recover the
  real URL.

### Health surface

`get_search_status()` returns:

```python
{
    "last_backend_used": "brave" | "searxng" | "ddg" | None,
    "last_failure_chain": ["brave:quota", "searxng:no_results", ...],
    "brave_quota_blocked_until": <epoch> | None,
}
```

Surfaced via:

- `/api/cp/tech-radar` REST response (added Apr 2026).
- React `TechRadarTab` banner — yellow when Brave is in backoff, blue
  when fallback served the request, red when all tiers failed.

---

## 5. Discovery Storage Layers

### 5.1 ChromaDB `scope_tech_radar`

Every discovery is written here regardless of category, in this exact
text format (see `tech_radar_crew.py:122`):

```
"[<category>] <title>: <summary>. Action: <action>"
```

Metadata: `{category, relevance, title}`. `importance="high"` for
high-relevance items, otherwise `"normal"`.

This is the source of truth for human-facing surfaces. The dashboard
endpoint, Firestore publisher, evolution prompt, and `tech radar`
commander command all read from here via
`retrieve_operational("scope_tech_radar", "technology discovery", n=...)`.

The string format is parsed back to structured form by a regex
(`r'\[(\w+)\]\s*(.+?):\s*(.+?)(?:\.\s*Action:\s*(.+))?$'`) at every
read site — `dashboard_api.py:625`, `firebase/publish.py:979`.

### 5.2 PostgreSQL `control_plane.discovered_models` (model discoveries)

When the LLM returns `category="models"` and a plausible
`openrouter_id` (e.g. `"moonshotai/kimi-k2-0905"`), the radar plants a
**stub row**:

```sql
model_id        = "openrouter/moonshotai/kimi-k2-0905"
provider        = "openrouter"
display_name    = <title from radar>
context_window  = 0
cost_input_per_m  = 0
cost_output_per_m = 0
multimodal      = FALSE
tool_calling    = TRUE
source          = "tech_radar"
status          = "discovered"
raw_metadata    = {"tech_radar_discovery": {...}}
```

Insertion uses **`ON CONFLICT (model_id) DO NOTHING`** — see
`_store_stub()` in `app/llm_discovery.py:253`. This is critical: it
means tech_radar can never overwrite a row already enriched by the
OpenRouter scanner.

The slug is **regex-validated** (`^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$`)
before insertion to filter out LLM-hallucinated free-form names like
`"GPT-5.4 Pro"`. See `_plant_model_stub()` in
`tech_radar_crew.py:163`.

---

## 6. Model Catalog Integration

Tech radar's stub rows must not break the existing OpenRouter discovery
pipeline. The integration relies on three coordinated pieces:

### 6.1 Anti-poison filter — `_get_known_model_ids()`

```python
SELECT model_id FROM control_plane.discovered_models
WHERE cost_output_per_m > 0 OR context_window > 0
```

Stub rows (cost=0 AND context=0) are **excluded** from this set. The
`run_discovery_cycle()` filter `mid not in known_ids` therefore treats
stubbed models as still-undiscovered — so when the OpenRouter scanner
finds the real model in its API response, it goes through normalization
and lands in `new_models[]`, scheduled for benchmarking.

Without this filter, a tech_radar stub would permanently shadow the real
model and prevent it from ever being benchmarked or promoted.

### 6.2 Enrichment-aware `_store_discovered()`

ON CONFLICT now updates every data field except `source` and `status`:

```sql
ON CONFLICT (model_id) DO UPDATE SET
    provider          = EXCLUDED.provider,
    display_name      = EXCLUDED.display_name,
    context_window    = EXCLUDED.context_window,
    cost_input_per_m  = EXCLUDED.cost_input_per_m,
    cost_output_per_m = EXCLUDED.cost_output_per_m,
    multimodal        = EXCLUDED.multimodal,
    tool_calling      = EXCLUDED.tool_calling,
    raw_metadata      = EXCLUDED.raw_metadata,
    updated_at        = NOW()
    -- source NOT updated  → first discoverer keeps attribution
    -- status NOT updated  → preserves benchmarking progress
```

So when OpenRouter scan re-discovers a tech_radar stub:
- Pricing/context/capabilities get filled in.
- `source = 'tech_radar'` stays — credit preserved.
- `status = 'discovered'` stays — model now eligible for benchmarking.

### 6.3 The full chain after enrichment

```
tech_radar stub        OpenRouter scan        benchmarking          governance
(cost=0, src=          finds same model      (run_discovery_      (propose_promotion)
 tech_radar)     ──→   (cost>0)        ──→   cycle:1303)    ──→   ──→ approval queue
                       ON CONFLICT enrich                          ──→ promote_model()
                       new_models.append                                ──→ runtime CATALOG
```

Empirically: across the 21 fileless model proposals migrated by
`scripts/migrate_tech_radar_proposals.py`, two slugs (`moonshotai/kimi-k2-0905`,
`nvidia/nemotron-nano-9b-v2`) already existed via OpenRouter scan with
real pricing — `_store_stub` correctly DO-NOTHING'd them, preserving
the OpenRouter attribution.

---

## 7. Notification & Display Surfaces

### 7.1 Signal (high-relevance only)

`_notify_user_discoveries()` in `tech_radar_crew.py:201` sends up to
**three** highest-relevance items per scan:

```
🔬 Tech Radar — new discoveries:

• [models] Kimi K2
  Trillion-parameter MoE …
  → Test for high-complexity agent reasoning…

• …
```

Delivered fire-and-forget via `signal_client.send()` on the running
event loop; falls back to a logger info if no loop is active. The user
can react 👍/👎 or text `approve <id>` (legacy code path — model
proposals no longer use this since the migration to the
`discovered_models` flow).

### 7.2 Dashboard REST — `/api/cp/tech-radar`

`app/control_plane/dashboard_api.py:610`. Returns:

```json
{
  "discoveries": [
    {"category": "models", "title": "Kimi K2", "summary": "…", "action": "…"},
    …
  ],
  "updated_at": "2026-04-26T10:00:00Z",
  "error": null,
  "search_status": {
    "last_backend_used": "searxng",
    "last_failure_chain": ["brave:quota"],
    "brave_quota_blocked_until": 1777283736
  }
}
```

Polled by the React `useTechRadarQuery()` hook every 60s
(`POLL.oneMin`).

### 7.3 React `TechRadarTab`

`dashboard-react/src/components/LlmsPage.tsx:724`. Renders:

- Categorized cards (models / frameworks / research / tools / unknown)
  with color-coded headers from `RADAR_COLORS`.
- Title, summary, and action per discovery.
- A backend-health banner derived from `search_status`:
  - **Yellow** — `brave_quota_blocked_until > now()`: "Brave quota
    exhausted — using searxng until retry at <ts>".
  - **Blue** — `last_backend_used` ≠ Brave but no quota issue: "Search
    served by searxng (Brave skipped)".
  - **Red** — `last_backend_used == null`: "All search backends failed:
    brave:quota → searxng:no_results → ddg:no_results".

### 7.4 Firestore `status/tech_radar`

Mirror of the same data published by `report_tech_radar()` in
`app/firebase/publish.py:966`, written every 5 minutes from the
heartbeat loop in `app/main.py:316`. Consumed by the legacy Firebase
dashboard (`dashboard/public/index.html:1349`).

### 7.5 Evolution prompt context

`app/evolution.py:267` pulls the 5 most-recent discoveries (truncated
to 150 chars each) and inlines them under "Recent Tech Discoveries" in
the hypothesis-generation prompt. Lets evolutionary mutations draw on
fresh research signal.

### 7.6 Commander text command

`app/agents/commander/commands.py:333`. The user can text `tech radar`
(or `tech` / `radar` / `discoveries`) to Signal; replies with the 10
most-recent discovery summaries.

---

## 8. Idle Scheduling

Registered at `app/idle_scheduler.py:1083` as a HEAVY-weight job:

```python
jobs.append(("tech-radar", _tech_radar, JobWeight.HEAVY))
```

The idle loop has a three-phase cycle (`_run_idle_loop`,
`idle_scheduler.py:219`):

1. Phase 1 — all LIGHT jobs in parallel (3-worker pool).
2. Phase 2 — exactly one MEDIUM job (round-robin).
3. Phase 3 — exactly one HEAVY job, **only if `time.monotonic() -
   _last_task_end > 120`** (i.e. ≥2 min idle).

There are 14 HEAVY jobs in total, rotated round-robin. So tech-radar
gets a turn roughly every 14 idle-eligible cycles — typical cadence is
**1-3 scans per day** depending on user-task load.

### Failure cooldown

If `run_tech_scan` raises, the scheduler increments `_job_failure_counts`
and sets a `_job_skip_until` timestamp persisted in
`/app/workspace/memory/idle_job_state` (sqlite3 dbm). Cooldowns survive
container restarts.

---

## 9. Configuration Surfaces

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `BRAVE_API_KEY` | `.env` / container env | required | Tier-1 search; without this the cascade goes straight to SearXNG. |
| `SEARXNG_URL` | gateway env (optional) | `http://searxng:8080/search` | Tier-2 endpoint. |
| Tech-radar HEAVY rotation slot | `idle_scheduler.py:1083` | 1 of 14 | Frequency control — make MEDIUM to run every cycle, remove to disable. |
| `_SEARCH_QUERIES` | `tech_radar_crew.py:29` | 7 fixed queries | What the radar looks for. |
| `IDLE_DELAY_SECONDS` | `idle_scheduler.py` | 5s | Min idle before any background job runs. |
| HEAVY-job 2-min idle gate | `idle_scheduler.py:299` | 120s | Min idle before HEAVY phase fires. |
| Brave quota backoff | `web_search.py:32` | 24h | Time to skip Brave after a 402. |
| Discoveries per scan | `tech_radar_crew.py:109` | top 10 | Cap on stored items. |
| Signal notification cap | `tech_radar_crew.py:175` | top 3 | Max items per Signal message. |
| Dashboard discovery cap | `firebase/publish.py:990` | 15 | Items pushed to Firestore. |

### Docker plumbing

- Gateway image: `app/`, `dashboard/build/`, `scripts/`, `tests/` baked in
  via Dockerfile COPY. Workspace volumes:
  `./workspace`, `./wiki`. ChromaDB persistence:
  `./workspace/memory:/chroma/chroma`.
- SearXNG accessibility: declared in `docker-compose.firecrawl.yml:31`,
  joined to both `firecrawl-backend` and `crewai-team_external` with a
  `searxng` alias so `http://searxng:8080` resolves from the gateway.

---

## 10. Failure Modes

| Symptom | Likely cause | Verification | Fix |
|---|---|---|---|
| Dashboard shows zero discoveries forever | All search backends failed OR scheduler never reached tech-radar slot | `curl /api/cp/tech-radar` → check `search_status`. Check `docker logs` for `'tech-radar' completed`. | Top up Brave / restart firecrawl-searxng-1 / lower idle gate. |
| "Search error: unable to reach search API." in logs (legacy) | Pre-cascade web_search.py | Inspect web_search.py — should have `_search_brave_raw` etc. | Rebuild gateway image. |
| Banner reads "Brave quota exhausted" continuously | Brave free-tier $25/mo cap hit | `curl https://api.search.brave.com/...` returns 402 | Top up at brave.com/search/api/ — backoff auto-clears at next month or 24h, whichever sooner. |
| Stub rows pile up with cost=0 forever | Tech-radar hallucinated a slug not on OpenRouter | `SELECT * FROM discovered_models WHERE source='tech_radar' AND cost_output_per_m=0` | Manual `DELETE` (acceptable garbage; doesn't poison anything). |
| Same model gets benchmarked twice | Race between tech_radar stub and concurrent OpenRouter scan | `_get_known_model_ids` filter is per-cycle | Acceptable — benchmarking is idempotent. |
| Signal message never arrives | No event loop / signal_client misconfigured | grep logs for `Tech radar notification (no event loop)` | Restart gateway. |
| `_brave_quota_blocked_until` survives Brave quota reset | Backoff is 24h, monthly reset on day-1 | `python -c "from app.tools.web_search import _brave_quota_blocked_until; print(_brave_quota_blocked_until)"` | Restart gateway OR wait — first 402 of new month resets it cleanly. |
| Dashboard shows stale discoveries | React Query cache or Firestore lag | Check `updated_at` on the API response | Hard-refresh; React Query refetch is 60s. |

---

## 11. Operating Procedures

### Run a scan on demand

```bash
docker exec crewai-team-gateway-1 python -c \
  "from app.crews.tech_radar_crew import run_tech_scan; print(run_tech_scan())"
```

### Inspect the discovery memory

```bash
docker exec crewai-team-gateway-1 python -c "
from app.crews.tech_radar_crew import get_recent_discoveries
for d in get_recent_discoveries(20):
    print(d[:160])
"
```

### Check search-backend health

```bash
docker exec crewai-team-gateway-1 python -c "
from app.tools.web_search import get_search_status
print(get_search_status())
"
```

### Query model stubs

```bash
docker exec crewai-team-gateway-1 python -c "
from app.control_plane.db import execute
rows = execute(
  \"SELECT model_id, source, status, cost_output_per_m FROM control_plane.discovered_models WHERE source='tech_radar' ORDER BY discovered_at DESC LIMIT 20\",
  fetch=True,
)
for r in rows or []: print(r)
"
```

### Force-clear Brave quota backoff (for testing)

```bash
docker exec crewai-team-gateway-1 python -c "
from app.tools import web_search
web_search._brave_quota_blocked_until = 0.0
print('cleared')
"
```

### Drain pre-existing fileless proposals (idempotent)

```bash
docker exec crewai-team-gateway-1 python scripts/migrate_tech_radar_proposals.py --dry
docker exec crewai-team-gateway-1 python scripts/migrate_tech_radar_proposals.py
```

### Validate persistence across rebuilds

```bash
cd /Users/andrus/BotArmy/crewai-team
docker compose build gateway
docker compose -f docker-compose.firecrawl.yml up -d searxng
docker compose up -d gateway
sleep 12
docker exec crewai-team-gateway-1 python -c "
from app.llm_discovery import _store_stub
from app.tools.web_search import get_search_status
print('stub helper OK; search status:', get_search_status())
"
```

---

## 12. Test Coverage

### `tests/test_web_search_fallback.py`

5 mocked-HTTP unit tests covering every cascade path:

1. `test_brave_success_short_circuits_chain` — Brave 200 ⇒ SearXNG/DDG
   never called.
2. `test_brave_402_falls_through_to_searxng` — 402 quota error ⇒
   backoff timestamp set, SearXNG serves the result, DDG skipped.
3. `test_brave_backoff_skips_brave_on_subsequent_calls` — once
   `_brave_quota_blocked_until` is in the future, the next call doesn't
   touch Brave.
4. `test_all_backends_fail_returns_empty_list_with_chain` — every tier
   returns nothing ⇒ `search_brave` returns `[]`,
   `last_failure_chain` has 3 entries.
5. `test_web_search_tool_wrapper_returns_no_results_string_when_empty`
   — preserves the CrewAI `@tool` string contract.

### `tests/test_llm_discovery.py`

`test_stub_is_not_known_until_enriched` (added Apr 2026) covers the
end-to-end anti-poisoning flow:

```
plant stub  →  assert not in _get_known_model_ids
            →  _store_discovered with real cost (source='openrouter_api')
            →  assert in _get_known_model_ids
            →  assert source == 'tech_radar' (attribution preserved)
            →  assert cost/context updated
```

Also covers `test_get_known_model_ids` and the existing
`test_store_and_retrieve` round-trip.

---

## 13. Design History

### Pre-fix state (≤ Apr 19 2026)

Tech radar auto-created **skill proposals with no `files/`** for every
model discovery. Approval was a no-op because `approve_proposal()`
copies files from `proposal_dir/files/` and there were none.
Empirically: 75 proposals in months, 0 ever approved.

The `action` field on each discovery was free-form LLM-suggested text
that no consumer ever parsed back out.

### What broke

1. The "skill" proposal creation produced fileless cruft that clogged
   the proposals system and never reached the LLM cascade.
2. The Brave Search API plan hit its $25/mo quota and `web_search`
   returned `"Search error: unable to reach search API."` for every
   call — filtered out by `tech_radar_crew.py`'s
   `"error" not in result.lower()` check, so the LLM was never invoked
   and ChromaDB stayed empty.
3. Naïve attempts to plug discoveries into `discovered_models` would
   have poisoned `_get_known_model_ids()` because the un-enriched stub
   would shadow the real OpenRouter row.

### Fix sequence (Apr 19-26 2026)

1. **Removed fileless proposal creation** — tech_radar_crew.py:133-147
   block deleted (commit `5e785d8`, later folded into mainline).
2. **Anti-poison `_get_known_model_ids` filter** — `WHERE
   cost_output_per_m > 0 OR context_window > 0`.
3. **`_store_stub()` helper** + ON CONFLICT DO NOTHING.
4. **`_store_discovered()` ON CONFLICT enrichment** — preserves
   `source` (attribution) and `status` (benchmarking progress).
5. **Migration script** — drained 21 pending fileless proposals,
   resolved 2 to OpenRouter slugs (already enriched), rejected all
   with a migration note.
6. **Three-tier search cascade** — Brave → SearXNG → DDG with a 24h
   backoff on 402 quota errors, replacing the Brave-only single point
   of failure.
7. **`search_status` surface** — REST + React banner so the operator
   can see at a glance whether the cascade is degraded.

### What this earned

- Tech radar produces real discoveries even when Brave is at quota.
- Model discoveries flow into the same benchmarking + governance
  pipeline as OpenRouter-discovered models.
- The operator no longer has to dig through `docker logs` to know why
  the radar might be quiet.
- Stub rows are permanent records of "tech_radar saw this first" with
  attribution preserved through downstream enrichment and promotion.
