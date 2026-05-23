# Briefing Evolution

Dynamically-growing morning briefing. The system proposes new sections,
shows them with a `✨ NEW — 👎 to drop, anything else keeps it` marker,
and learns from operator reactions.

Shipped 2026-05-23.

## Behaviour the operator sees

1. Every morning briefing carries the fixed-shape core (calendar /
   unread / tickets / workstream activity / system status / queued
   notifications / travel) PLUS, at the tail, any **adopted** sections
   plus at most ONE **trial** section with the ✨ NEW marker.
2. Reacting to the briefing on Signal:
   - 👎 → trial section dropped permanently from future briefings.
     After a 90-day cooldown it re-proposes itself.
   - 👍 → trial section adopted immediately. Appears in every briefing
     from now on.
   - Anything else (or no reaction) → keeps the section in trial. After
     3 shows OR 7 days of no 👎 the section auto-adopts.
3. The catalog of candidate sections grows two ways:
   - **Tier 1**: hand-curated modules under
     `app/life_companion/briefing_sections/` — adding a file is the
     only step.
   - **Tier 2**: a weekly Haiku 4.5 proposer surfaces NEW section
     ideas inline via the `briefing-ideas` candidate. Operator picks
     which ones are worth implementing.

## Architecture

```
app/life_companion/
├── briefing_evolution/        — subsystem core
│   ├── __init__.py            — re-exports (lazy import)
│   ├── catalog.py             — scans briefing_sections/, registers candidates
│   ├── trial_state.py         — JSON state machine (proposed → trial → adopted | dropped)
│   ├── selector.py            — picks ≤1 trial per briefing; renders adopted ones
│   ├── feedback_bridge.py     — signal_ts → trial_id map (8d TTL)
│   └── proposer.py            — weekly LLM proposer
└── briefing_sections/         — candidate modules (12 seeded)
    ├── _base                  — protocol: ID / DISPLAY_NAME / DESCRIPTION / gather()
    ├── weather.py             — Open-Meteo (no API key)
    ├── sun_times.py           — sunrise / sunset / day length (Helsinki)
    ├── currency_rates.py      — ECB daily reference rates
    ├── estonian_headlines.py  — existing web_search adapter
    ├── epistemic_claims.py    — recent claims from claim_ledger
    ├── affect_summary.py      — yesterday's mood from affect/trace.jsonl
    ├── action_requests.py     — pending app/action_requests/ items
    ├── paper_picks.py         — high-relevance non-codeable papers
    ├── skills_used.py         — recent skill invocations + dormant nudge
    ├── goal_progress.py       — kernel.self_state.current_goals
    ├── subia_observations.py  — daily one-liners from the 4 Q5 sentience modules
    └── briefing_ideas.py      — surfaces the LLM proposer's output
```

## State machine

```
   proposed ─── first show ──▶ trial ──── 👎 ─────▶ dropped
                                 │                    │
                                 ├── 👍 ──▶ adopted   │ 90d cooldown
                                 │                    ▼
                                 └── ≥3 shows OR ≥7d ▶ adopted
                                                      ▲
                                                      │ re-proposed back to PROPOSED
                                                      ▼
                                                  proposed
```

Persisted at `workspace/life_companion/briefing_evolution/state.json`.
Single-writer (briefing module + reaction handler), threading.Lock
protected, failure-isolated.

## Signal-reaction routing

A morning briefing carrying a trial section is sent via
`send_message_blocking` (not the usual fire-and-forget) so the Signal
timestamp can be captured. `feedback_bridge.register(ts, section_id)`
records the pairing.

`app/main.py` reaction handler adds a 7th block (after the governance
one). On 👎/👍 targeting a known ts:
1. Resolves `signal_ts → section_id` via `feedback_bridge.find_section_for_ts`.
2. Dispatches to `trial_state.mark_dropped` or `mark_adopted`.
3. Records `AgreementResponse.REJECTED` / `ACCEPTED` against the
   `proactive_briefing` entry in `app/agreement_self_model/agreement_ledger.py`.
4. Emits a `briefing_section_decision` event to the identity
   continuity ledger (new event kind, registered in
   `app/identity/continuity_ledger.py`).

## How to add a new candidate

1. Create `app/life_companion/briefing_sections/<id>.py`:
   ```python
   ID = "my-section"
   DISPLAY_NAME = "🔧 My new section"
   DESCRIPTION = "What this surfaces in one sentence."

   def gather() -> list[str]:
       # return list of bullet lines or [] to hide this briefing
       return ["  • first line", "  • second line"]
   ```
2. That's it. The catalog scans the package on next briefing render and
   registers the new candidate as PROPOSED. It enters the FIFO queue.

## Composition

- **`app.agreement_self_model.agreement_ledger`** — every trial show
  records a `proactive_briefing` row; reactions record ACCEPTED /
  REJECTED. The 90-day rolling-rate digest in the existing daily
  briefing picks this up automatically.
- **`app.identity.continuity_ledger`** — adopt/drop events surface in
  the annual reflection's `summarise_drift` Counter via the new
  `briefing_section_decision` kind.
- **`app.life_companion.feature_registry`** — new `briefing_proposer`
  feature so React `/cp/life-companion` can toggle the weekly LLM
  proposer.

## Cost

- Catalog scan: zero (lazy single-shot on first briefing render).
- Per-section `gather()`: varies — weather/sun-times/currency hit free
  external APIs (~50ms each); BotArmy-data candidates are pure file
  reads.
- Weekly LLM proposer: ~$0.001/week (single Haiku 4.5 call, ~3k input
  + ~500 output tokens).

## Anti-Goodhart

The user's "no answer = keep" rule means the briefing trends toward
more sections by default. Three guards:

1. **At-most-one trial per briefing.** The operator can't be drowning
   in new content.
2. **`agreement_ledger` integration.** Every trial counts toward the
   `proactive_briefing` rolling-rate — visible in the existing
   `briefing_section()` digest the operator already reads.
3. **Adopted sections can still be removed** via
   `POST /api/cp/briefing/sections/<id>/drop` (manual operator action).
   Not via Signal — that surface is reserved for trial sections.

## Files touched

New:
- `app/life_companion/briefing_evolution/{__init__,catalog,trial_state,selector,feedback_bridge,proposer}.py`
- `app/life_companion/briefing_sections/{__init__,weather,sun_times,currency_rates,estonian_headlines,epistemic_claims,affect_summary,action_requests,paper_picks,skills_used,goal_progress,subia_observations,briefing_ideas}.py`

Modified:
- `app/life_companion/daily_briefing.py` — composer returns trial id; run() registers ts
- `app/life_companion/__init__.py` — registers the weekly proposer idle job
- `app/life_companion/feature_registry.py` — adds `briefing_proposer` feature
- `app/identity/continuity_ledger.py` — adds `briefing_section_decision` event kind
- `app/main.py` — adds reaction handler block 7 (briefing-evolution)
