# Consolidation rhythm (Phase 3 of elegance plan)

Status: shipped 2026-05-19, default ON, deterministic (no LLM).

## Why this exists

Phase 1 gave the system eyes on code health; Phase 2 turned those signals
into refactor proposals. Both are *event-driven* — they react when drift
crosses a threshold. Phase 3 adds the **rhythmic, calendar-driven layer**
that watches the codebase's *trajectory* and surfaces a quarterly /
annual verdict the operator can read in one glance.

Without this, the loop reads as a series of disconnected alerts. With it,
the alerts compose into a year-over-year story: did the codebase shed?
did refactor proposals land? did the parallel-capability count drop?

## What ships

| File | Cadence | Output |
|---|---|---|
| [app/identity/elegance_reflection.py](crewai-team/app/identity/elegance_reflection.py) | Annual (350-day min) | `wiki/self/elegance_reflections/<year>.md` |
| [app/self_improvement/code_consolidation.py](crewai-team/app/self_improvement/code_consolidation.py) | Quarterly (85-day min) | `wiki/self/code_consolidation/<year>_q<n>.md` |

Both are wired as LIGHT idle jobs in [app/identity/scheduler.py](crewai-team/app/identity/scheduler.py).
Cadence-gating happens inside each pass via mtime comparison against the
target file, so a daily idle tick is a ~50 ms no-op until the cadence is
actually due.

## Deliberate design: deterministic, not LLM-driven

Both passes compose summaries from **objective code metrics** (composite
trajectory, cycle counts, capability owners, file sizes, ledger event
counts). No LLM call. The annual essay sibling (`annual_reflection.py`)
uses an LLM because it reflects on *values* — a domain where prose
matters and phenomenal-language linting is mandatory. The elegance
domain is *metrics* — narrative prose adds noise.

Three benefits:

1. **Cheap.** A pass costs JSON reads + a few hundred microseconds of
   composition. Free to run indefinitely.
2. **Reproducible.** Given identical input artefacts, the output is
   identical. Tests assert exact substrings, not LLM-output shapes.
3. **No phenomenal-language risk.** There's no narrator, so no
   `PhenomenalLanguageLinter` retry loop needed.

## Annual elegance reflection

Reads:
- `workspace/code_quality/elegance_history.json` — per-file composite samples
- `workspace/code_quality/architectural_baseline.json` — current SCC + capability + centrality snapshot
- `workspace/proposal_bridge/refactor_proposer/*.json` — refactor proposals filed in the year + status
- Continuity-ledger `architectural_debt_drift` events in the year

Composes six sections:

1. **Composite trajectory** — annual mean/median/min/max of QualityScore composite
2. **Architectural shape** — actionable cycles, systemic SCCs, parallel capabilities, top centrality
3. **Drift events recorded** — counts by source actor
4. **Refactor proposals filed** — counts by status (staged / applied / rejected / expired)
5. **Codebase shape** — current modules / packages / LOC
6. **Net-zero growth verdict** — `shedding` / `stable` / `growing`

### Verdict semantics

The verdict is the one-line dashboard for whether the elegance plan is
working. Three buckets:

- **`shedding`** — avg composite ≥ 0.90 AND applied ≥ rejected AND ≤5
  actionable cycles. The codebase is being trimmed; refactor proposals
  are landing.
- **`stable`** — avg composite ≥ 0.85 AND at least one drift event
  recorded. The loop is firing but the codebase isn't yet net-shedding.
- **`growing`** — neither. The refactor loop hasn't gained traction.

The verdict appears in the frontmatter so anyone grepping
`wiki/self/elegance_reflections/*.md` can read the year-over-year story
without opening files.

## Quarterly code-consolidation digest

Reads the system_inventory snapshot + architectural_baseline. Produces
three lists:

1. **Shed candidates** — modules meeting ALL of:
   - `loc < 200`
   - `reverse_degree ≤ 1` (zero or one importer)
   - `has_tests = False`
   - Not in the foundational-hub allowlist (`config.py`, `main.py`,
     `paths.py`, `runtime_settings.py`, `__init__.py`)
2. **Parallel-capability clusters** — capabilities with ≥3 owners
3. **Persisting small cycles** — actionable SCCs from the architectural baseline

Cap of 20 shed candidates, 10 parallels, 10 cycles per digest — the
operator gets a focused list, not a wall of text.

### Why it's not a CR

The user's original plan said "shed candidates markdown CR". After
implementation review, the cleanest semantic is:

- The digest itself goes to `wiki/` (observational, no CR — it's a
  reflection page, not a code change).
- The digest CONTAINS a list of candidates the operator can then act on.
- ACTIONABLE proposals come from `refactor_proposer` (Phase 2), which is
  the CR-emitting half of the loop.

This separates *situational awareness* (Phase 3) from *proposed action*
(Phase 2). Both compose: the operator reads the quarterly digest to
decide which Phase 2 proposals to prioritise.

### Live first-run findings against the real codebase

The first quarterly digest immediately surfaced 20 shed candidates —
nearly all of them are 18-LOC "shim modules" under `app/consciousness/`
and `app/self_awareness/`. These look like migration shims left after a
refactor — exactly the kind of artefact the consolidation rhythm exists
to highlight. The operator's quarterly review now has a concrete punch
list.

## Continuity-ledger emission

Both passes emit `code_consolidation` events to the identity continuity
ledger (the 23rd event kind). `summarise_drift.by_kind` is a dynamic
Counter, so future annual reflections will automatically pick up:
- How many quarters had a digest written
- The annual reflection's own emission

The ledger trail becomes the system's long-term self-knowledge of *its
own elegance trajectory*.

## Master switches

| Switch | Default | Notes |
|---|---|---|
| `elegance_reflection_enabled` | ON | Annual deterministic essay |
| `code_consolidation_enabled` | ON | Quarterly deterministic digest |

Both flippable via `/cp/settings`. Env fallbacks
`ELEGANCE_REFLECTION_ENABLED` / `CODE_CONSOLIDATION_ENABLED` honored
when runtime_settings is unavailable.

## Composition with prior phases

```
Phase 1 monitors            Phase 2 producer             Phase 3 rhythm
─────────────────────       ─────────────────────────    ──────────────────────────
elegance_drift          →   detect_complexity_hotspots → elegance_reflection
                            detect_import_cycles         (annual essay summarising
architectural_drift     →   detect_parallel_caps         applied/rejected counts)
                                                  ↓
                                            proposal_bridge.stage
                                                  ↓
                                            operator CR gate            code_consolidation
                                                  ↓                     (quarterly digest
                                            applied / rejected ─────→   listing un-acted
                                                                        candidates)
```

Each phase's output feeds the next. The rhythm in Phase 3 closes the
loop: the operator reads the quarterly digest, prioritises Phase 2 CRs,
which feeds Phase 1's measurements, which feed the next quarter's
digest.

## Tests

[tests/identity/test_elegance_reflection.py](crewai-team/tests/identity/test_elegance_reflection.py) — 11 tests
covering composite trajectory filtering, architectural shape extraction,
proposal-status counting, all three verdict states, end-to-end write,
disabled short-circuit, recent-skip, and stale-mtime due-detection.

[tests/self_improvement/test_code_consolidation.py](crewai-team/tests/self_improvement/test_code_consolidation.py) — 13 tests
covering quarter assignment, shed-candidate filtering (all criteria),
shed-cap, parallel-cluster threshold, systemic-cycle exclusion,
end-to-end write, disabled short-circuit, recent-skip, stale-mtime
due-detection.

24 new tests pass; full Phase 1 + 2 + 3 slice = **100 tests pass**.

## What deliberately doesn't ship in Phase 3

- **PEP / framework idiom radar** — extension of library_radar that
  watches PEPs + changelogs for idiom updates. A natural future ship;
  not blocking for this phase.
- **Lessons-learned universal consultation** — already exists for
  threads (Q8.2); extending to all CR creation paths is a separate
  fold-in.
- **Cross-monitor pattern detector** — reads OUTPUTS of all monitors
  and detects N-monitor overlapping causes. Useful but the design space
  for "overlapping cause" is large; deferred.

Each of these is additive against the patterns shipped here.
