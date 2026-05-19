# Phase 4 — elegance plan follow-ups

Status: shipped 2026-05-19, both modules default-discoverable, cross-monitor monitor default ON, idiom radar default OFF.

## What this ships

Three deferred items from the original elegance plan finalised:

| Item | Status | Where |
|---|---|---|
| 1. PEP / framework idiom radar | **NEW — shipped here** | [app/library_radar/idiom_radar.py](crewai-team/app/library_radar/idiom_radar.py) |
| 2. Lessons-learned universal consultation | **Already shipped 2026-05-09** | Lines 272-285 of [app/change_requests/lifecycle.py](crewai-team/app/change_requests/lifecycle.py) |
| 3. Cross-monitor pattern detector | **NEW — shipped here** | [app/healing/monitors/cross_monitor_pattern.py](crewai-team/app/healing/monitors/cross_monitor_pattern.py) |

Item 2 was already implemented as part of Phase F #5 (the May 9 audit-driven hardening pass) — `create_request` already calls `lessons_learned.check_against` against the proposal text and prepends a `⚠️ Matches rejected-pattern lesson` banner to the CR reason. Nothing to add — the universal consultation hook fires for every CR regardless of source.

## Item 1: PEP/idiom radar

Reads the Python PEPs RSS feed via the existing `app/episteme/feed_sources.fetch_python_peps`. For each entry, checks the title + abstract against an idiom-signal keyword list (`match`, `dataclass`, `async`, `walrus`, `f-string`, `structural pattern`, `typing`, `protocol`, `slots`, `type parameter`, `type hint`, `annotation`, `exception group`, `sub-interpreter`). Matching entries become candidates.

For each candidate, stages a markdown proposal via `proposal_bridge.stage` with:
- **source** `library_radar` (reuse — PEP idioms ARE library-class adoption signals)
- **signature** `pep_<number:04d>` (idempotent — same PEP, same signature)
- **target_path** `docs/proposed_pep_idioms/pep_<number>.md`
- **cooldown_days** 14
- **coding_session_spec** with intent + acceptance criteria (survey-only, no specific files)

### Migration hints

Per matched keyword, the proposal body includes a "Suggested migration starting points" section. Hints are deterministic, not LLM-generated, e.g.:

- `match` / `structural pattern` → "Look for `if isinstance(...): elif isinstance(...):` chains in `app/agents/` and `app/crews/`."
- `dataclass` / `slots` → "Look for hand-rolled `__init__` + `__eq__` + `__repr__` classes."
- `type` / `annotation` / `typing` → "Run `code_quality.measure_file_at_path` over modules with lowest type_coverage."

### Discipline

- **Default OFF.** PEP feed is noisy — not every Final PEP is worth migrating. Operator opts in via `runtime_settings.pep_idiom_radar_enabled` (env fallback `PEP_IDIOM_RADAR_ENABLED`).
- **Cap 3 per pass.** Backlog spreads over weeks via the bridge's 14-day cooldown.
- **Weekly poll.** PEPs land at low cadence; daily would burn the feed for nothing.
- **15-min warm-up** so it doesn't fight the gateway boot or library_radar.

## Item 3: Cross-monitor pattern detector

The system has ~40 monitors as of 2026-05; each fires on its own threshold and emits its own continuity-ledger landmark. None of them notice when *several different monitors fire on the same path* within a short window — that's the signature of a deeper architectural problem the per-monitor alerts miss.

`cross_monitor_pattern` (43rd healing monitor) reads recent continuity-ledger events, groups by `detail.path` (most monitors emit one), and alerts when **≥3 distinct event KINDS converge on the same path within a 14-day window**.

### Why the ledger?

The identity continuity ledger is the canonical "monitor landmark" surface. Almost every monitor that emits at all uses `record_event`. Reading the ledger gives us a uniform view of "what monitors said this week" without scraping a dozen per-monitor JSONL files with diverging schemas.

### Dedup discipline

Cluster fingerprint = `(path, sorted(kinds))`. Re-detecting the same fingerprint within a 30-day window stays silent. When the cluster's *composition* changes (a new kind joins, or a kind drops out), the fingerprint changes and the next pass alerts again. Bounded growth via 60-day fingerprint expiry.

### No new event kind

Reuses `architectural_debt_drift` for its own ledger emission — a convergent cluster IS an architectural-debt signal. No new IDENTITY_EVENT_KIND needed; `summarise_drift.by_kind` already picks it up for the annual reflection.

### Discipline

- **Default ON.** Observational only; alerts via Signal + ledger, never proposes CRs, never modifies code.
- **Weekly cadence** inside the daily probe (saves alert spam).
- **Top-5 clusters per alert** so the operator gets a focused punch list, not a wall.

## Composition with prior phases

```
Phase 1 monitors                       Phase 4 meta-detector
──────────────────────                 ────────────────────────────
elegance_drift          ┐
architectural_drift     │   ←──────    cross_monitor_pattern
tz_drift                │              (groups by detail.path;
feedback_loop_drift     │               alerts on convergence
embedding_drift         │               across distinct kinds)
... ~35 other monitors  │
identity ledger         ┘

Phase 4 PEP radar                      Phase 2 producer
────────────────────────               ────────────────────
idiom_radar             ──── stage ───→ proposal_bridge ───→ operator CR gate
(uses source="library_radar")            (same flow as
                                          library_radar +
                                          refactor_proposer)
```

The cross-monitor detector closes a meta-gap: individual monitors are local, this one is global. The PEP radar adds a new upstream signal source for the same Phase-2 producer infrastructure.

## Tests

[tests/test_idiom_radar.py](crewai-team/tests/test_idiom_radar.py) — 14 tests covering PEP number extraction (URL, title, padded zero), keyword detection, candidate detection with feed stub, fetch-failure resilience, per-pass cap, body/spec construction, disabled short-circuit, bridge integration, signature stability.

[tests/healing/test_cross_monitor_pattern.py](crewai-team/tests/healing/test_cross_monitor_pattern.py) — 14 tests covering path extraction (3 fallback keys), pathless-event skipping, cluster grouping, threshold filtering, fingerprint stability, dedup window in/out, run-time disabled/cadence/alert/dedup-second-pass paths, and the pure `detect_convergent_clusters` for diagnostics.

28 new Phase 4 tests pass. Full Phase 1+2+3+4+adjacent slice: **128 tests pass**, 0 fail.

## Master switches

- `pep_idiom_radar_enabled` (default OFF)
- `cross_monitor_pattern_monitor_enabled` (default ON)

Both also honor env-var fallback (`PEP_IDIOM_RADAR_ENABLED`, `HEALING_MONITORS_ENABLED` for the whole driver).

## The elegance loop, now end-to-end

After Phase 4 the loop is complete from observation → proposal → action → reflection → meta-observation:

```
                     elegance_drift, architectural_drift
                                 ↓
                  [Phase 1] continuous code-health observation
                                 ↓
                       refactor_proposer (Phase 2)
                                 ↓
                  proposal_bridge → operator CR gate
                                 ↓
                  applied / rejected → lessons_learned KB
                                 ↓
              [Phase 3] elegance_reflection (annual)
              [Phase 3] code_consolidation (quarterly)
                                 ↓
                  identity continuity ledger
                                 ↓
              [Phase 4] cross_monitor_pattern (meta-detector)
                                 ↓
              [Phase 4] idiom_radar (upstream new-tech signal)
                                 ↓
                              [back to Phase 1]
```

Each loop closes the next. The codebase now measures itself, proposes refactors, reflects on the result, and watches its own monitor pattern for convergent debt.
