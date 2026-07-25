# Eval Harness v2 — replace the scorer, don't extend it

**Date:** 2026-07-25 · **Status:** Phase 0 IN PROGRESS
**Companion:** `reports/GATE_DIAGNOSIS_2026-07-25.md` (the runs this generalises from)

---

## 0. The evidence

The current scorer decides `delivered` by checking a reply against a blocklist of
known failure substrings (`_GAVE_UP_MARKERS` + `_REFUSAL_MARKERS` in
`evals/run_eval.py`). Tested against the six failure shapes actually observed:

| verdict | failure shape |
|---|---|
| **DELIVERED** | creative crew no-answer — *the message written on 2026-07-25* |
| caught | deep_research no-answer — **by accident**, see below |
| **DELIVERED** | leaked tool call `call:web_search{query:…}` |
| **DELIVERED** | leaked ReAct scratchpad ```` ```Thought: The user wants… ```` |
| **DELIVERED** | crash traceback `OSError: [Errno 36] File name too long` |
| **DELIVERED** | ungrounded report ("drawn from general knowledge") |

**Five of six score as success on current code.** It has now been wrong three
times in production use, always in the same direction:

1. 2026-07-24 — explicit refusals counted as successes (6/12 → 2/12 once fixed).
2. 2026-07-25 — an honest non-answer counted as a success (`gs_multi_and`).
3. 2026-07-25 — seven replies of internal leakage counted as successes (12/12).

---

## 1. Why extending fails structurally

**1.1 The error is one-directional.** A missing marker yields a false *success*.
No mechanism in the design can yield a false failure. The metric is therefore
biased in the worst possible direction for a quality gate: it flatters, silently.

**1.2 A closed list chasing an open set.** Failure modes are unbounded — every
internal artifact, crash type, refusal phrasing, model idiom. Success is a
narrow, checkable property. The blocklist is on the wrong side of that asymmetry.

**1.3 It responds to cosmetic rewording — including our own.** Replacing the
critic's self-blaming refusal with `outcome.NoAnswer.user_message()` changed
refusal *wording*. The deep_research variant is still caught only because the new
sentence happens to contain the substring "evidence gate did not clear"; the
creative variant lost its marker and now scores delivered. **The metric moved
because an error message was reworded.** A metric with that property cannot
baseline a refactor — and `ANSWERING_V2_PLAN.md` Phases 2–4 are a refactor that
rewords everything.

**1.4 It measures transport, not quality.** `delivered` means "bytes returned
that don't match a blocklist". The plan's own success metrics — groundedness,
completeness — are unmeasured. The ungrounded forest report is the proof: fluent,
well-formed, self-disclosed as ungrounded, scored a success.

**1.5 It's a black box, so the number is uninterpretable.** Routing is
non-deterministic: the same report questions went to `deep_research` (gated) at
14:32–16:30 and to plain `research` (ungated) at 17:51 on 2026-07-25. "12/12"
averages over materially different systems.

**1.6 The set was built to test routing, not quality.** Every `notes` field in
`golden_set.jsonl` describes a *dispatch* expectation ("should auto-promote",
"must NOT promote", "should hit the company_dossier fast-route"). That is what it
was for, and it is good at it. Repurposing it as a quality baseline without
adding acceptance criteria is the root of everything above.

---

## 2. Three inversions

| from | to |
|---|---|
| blocklist of failures | **acceptance contract** per question |
| black box (POST → string) | **observability-joined** (crew, gate verdict, evidence count, cost) |
| single sample | **variance-aware** (record routing; k>1 for decisions) |

### 2.1 Three outcomes, not two

`pass` / `fail` is what forced honest non-answers to be miscounted. v2 scores:

- **`pass`** — satisfies the contract.
- **`fail`** — does not, *including* content presented as an answer that isn't
  grounded when the contract requires grounding.
- **`blocked_infrastructure`** — the reply honestly names an external cause
  (no web results, provider quota exhausted, tool unavailable) **and** does not
  present ungrounded content as an answer.

`blocked_infrastructure` is excluded from the quality rate and reported
separately. This is the distinction the harness never had, and it is why a
credit outage was read as a quality collapse on 2026-07-24.

**Critically: an ungrounded report that discloses being ungrounded is `fail`,
not `blocked_infrastructure`.** Disclosure is not a substitute for evidence.
That rule exists because `gs_report_forest` on 2026-07-25 produced exactly that
and the prompt change arguably licensed it.

---

## 3. Phases

Each phase has a gate and an explicit falsifier — the thing that, if observed,
means stop rather than proceed.

### Phase 0 — write down what "good" means *(no code, no spend)*

Add a `contract` object to each golden-set entry: `intent`, `answer_shape`,
`min_substance`, `citation`, `must_address`, `must_not`, `degradation`,
`ambiguity`. Resolve the underspecified questions **now**, in the fixture, rather
than in a scorer's head — `gs_ambiguous_short_report` ("report on Tallinn's
housing prices") needs a recorded decision about whether 122 characters can pass.

*Gate:* two independent scorings of the same replies agree.
*Falsifier:* if they disagree, the contracts are not specific enough — tighten
them before writing any scorer.

### Phase 1 — the harness collects; humans judge *(plumbing only)*

- Store **full reply text**. Today it stores `reply[:200]`, which is why
  `--rescore` is documented as "a floor on the failure count, not a ceiling".
- Join each result to `control_plane.crew_tasks` / `tickets` for crew(s) used,
  gate verdict, evidence-set size, cost, tokens. *Verified feasible:*
  `postgres:5432` is reachable from the eval container on `crewai-team_internal`.
- Emit a **review sheet**: one reply per section with its contract and
  provenance, for explicit labelling.
- Demote the existing markers from gate to **diagnostic label**.

*Gate:* the sheet answers, for every historical run, "which crew handled this
and did a gate run".
*Falsifier:* the original diagnosis recorded empty `trace_id` on crew rows. If
the join proves unreliable, say so and fix provenance first — do not paper over
it with heuristic matching on timestamps.

### Phase 2 — deterministic positive scorer *(offline, no spend)*

Derive checks from the contracts. One check subsumes all four leakage failures
without enumerating shapes:

> a report-class reply must be prose of ≥N words containing ≥K citations that
> resolve to the run's evidence set

Tool-call syntax, scratchpad, raw JSON and tracebacks all fail the first clause.
`gs_coding` gets a genuinely objective check: parse the code, run it, compare the
sequence.

*Gate:* reproduces the Phase 0/1 human labels **and** catches all six shapes in
§0 — including the two introduced on 2026-07-25.
*Falsifier:* if it disagrees with human labels on more than one of twelve, the
contracts or the checks are wrong; do not "tune" thresholds until it agrees.

### Phase 3 — rubric judge, validated before trusted *(spend)*

LLM judge for groundedness and completeness, given the reply **and the run's
evidence set**, required to quote the span supporting each sub-score. Prefer a
different model family from the one under test.

*Gate:* measured agreement with Phase 1 human labels **before** any weight is
placed on it.
*Falsifier:* poor agreement means the judge is wrong, not the labels.
*Named risk:* a judge can be fooled by the same thing that fooled the harness —
fluent ungrounded prose. Supplying the evidence set is what makes groundedness
checkable rather than a matter of taste.

### Phase 4 — variance *(spend)*

k=1 for routine regression; **k=3 for decision-grade**. Report per-question
spread; flag when routing differs between runs for the same question.

*Gate:* only after this can the harness answer "did v2 beat v1".

---

## 4. Deliberately out of scope

- Building a general eval framework. This serves one golden set.
- Adding more failure markers. Three strikes.
- Letting the judge be the only gate.
- Scoring on latency alone (it is a constraint, not a quality signal).
- **Using the current harness to justify the `ANSWERING_V2_PLAN` Phase 2–4
  decision.** The present number cannot support a decision that size.

---

## 5. Cost

| phase | effort | live LLM spend |
|---|---|---|
| 0 | ~2h | none |
| 1 | ~3h | none |
| 2 | ~3h | none |
| 3 | ~4h + validation | judge calls only |
| 4 | scheduling | 36 dispatches per decision-grade baseline |

Phases 0–2 carry most of the value and spend nothing.

## 6. Validation asset already on disk

~27 replies across three 2026-07-25 runs have been read and classified by hand,
covering all three historical scorer failures. That is an immediate validation
set for Phase 2.

**Honest limit:** the JSON files store 200-char previews and
`tickets.result_summary` truncates at 500, so this set validates leakage
detection but **not** groundedness judging. Which is precisely why Phase 1 must
store full replies before Phase 3 is attempted.
