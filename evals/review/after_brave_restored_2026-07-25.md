# Eval review sheet

- report base_url: `http://gateway:8765`  sender: `eval-harness-20260725d`
- run valid: **True**  credit errors: 0
- harness `delivery_rate`: **1.0** — TRANSPORT ONLY, not a quality figure

Score each question against its contract. Three outcomes:

| outcome | meaning |
|---|---|
| `pass` | satisfies the contract |
| `fail` | does not — **including** content presented as an answer that isn't grounded when grounding is required |
| `blocked_infrastructure` | honestly names an external cause AND withholds invented content; excluded from the quality denominator |

> An ungrounded answer that **discloses** being ungrounded is `fail`, not `blocked_infrastructure`. Disclosure is not a substitute for evidence.

---

## gs_report_forest

> please make me a report on estona forest health and deforestation data over the years. research forestry industry business and practices and evaluate those from critical point in view

`6177 chars` · `520.2s` · harness=delivered

### Contract

**Intent:** A critical, evidence-backed report on Estonian forest health and deforestation trends over time, plus an evaluation of forestry-industry practices.

- shape: `prose_report`
- substance: words≥700, sections_or_headings≥3
- citations: ≥3 (must resolve to run evidence)

**Must address:**
- [ ] forest area or cover, with a trend over more than one point in time
- [ ] deforestation, felling or harvest volumes
- [ ] forestry industry structure or business practices
- [ ] an explicit critical evaluation, not description only

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer

**Degradation:** If web evidence was unavailable, `blocked_infrastructure` requires naming the cause AND withholding the report. A delivered-but-ungrounded report is `fail`.

**Ambiguity (resolved):** 'estona' is a misspelling of Estonia; a correct answer resolves it silently. Resolved 2026-07-25: the misspelling must not be treated as a clarification trigger.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → deep_research → critic**
- gate: presumed_clear (deep_research completed and no block phrasing found; NOT a positive confirmation — no column records the gate verdict; source: result_text)
- ticket: status=done difficulty=8 result_chars=500
  - `commander` completed 21.6s tokens=0 cost=$0.0000
  - `deep_research` completed 317.5s tokens=1072 cost=$0.0000
  - `critic` failed 69.2s tokens=0 cost=$0.0000 ERROR=Task execution failed: litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible ungrounded-by-disclosure

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
I don't currently have a verified retrieval set to cite for this specific report, so the following is drawn from general knowledge of publicly available Estonian and international forestry sources rat
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_report_industry

> write me a critical report on the Estonian dairy industry's business practices over the last decade, with sources

`1067 chars` · `174.7s` · harness=delivered

### Contract

**Intent:** A critical report on Estonian dairy-industry business practices across roughly the last decade, with sources the user can check.

- shape: `prose_report`
- substance: words≥600, sections_or_headings≥3
- citations: ≥3 (must resolve to run evidence)

**Must address:**
- [ ] sector structure (producers, processors, cooperatives) or its consolidation
- [ ] a time span of roughly a decade, not a single snapshot
- [ ] concrete business practices (milk pricing, contracts, export dependence, market power)
- [ ] a critical stance, not a promotional summary

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer

**Degradation:** Same as gs_report_forest. 'with sources' is explicit in the prompt, so an uncited report is `fail` regardless of prose quality.

**Ambiguity (resolved):** None material.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → research**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=5 result_chars=500
  - `commander` completed 36.6s tokens=1122 cost=$0.0003
  - `research` completed 28.6s tokens=8206 cost=$0.0012
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
```json
{
  "title": "Critical Report: Estonian Dairy Industry Business Practices (2014-2024)",
  "subjects": [
    {"id": "estonia_dairy_market", "name": "Estonian Dairy Industry", "market": "Agricul
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_research_deep

> do deep research comparing solar vs wind subsidy policy in the Nordics and recommend which is more cost-effective, with primary sources

`2984 chars` · `655.3s` · harness=delivered

### Contract

**Intent:** A comparative analysis of solar vs wind subsidy policy in the Nordics ending in a reasoned cost-effectiveness recommendation, backed by primary sources.

- shape: `prose_analysis_with_recommendation`
- substance: words≥600
- citations: ≥3 (must resolve to run evidence)

**Must address:**
- [ ] solar AND wind, comparatively rather than sequentially
- [ ] at least two named Nordic countries
- [ ] at least one concrete subsidy mechanism (feed-in tariff, CfD, auction, tax credit)
- [ ] an explicit recommendation with its reasoning

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer
- claiming it has no live access to sources — the system HAS web search; this false capability claim was observed on 2026-07-25 and is `fail`, not `blocked_infrastructure`

**Degradation:** A genuine search outage is `blocked_infrastructure` only if the reply names it. A capability disclaimer that is factually untrue is `fail`.

**Ambiguity (resolved):** 'primary sources' is aspirational for a web-search system; resolved 2026-07-25 — authoritative bodies (IEA, Eurostat, national energy agencies) satisfy it; blogs and aggregators do not.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → deep_research → critic**
- gate: presumed_clear (deep_research completed and no block phrasing found; NOT a positive confirmation — no column records the gate verdict; source: result_text)
- ticket: status=done difficulty=8 result_chars=500
  - `commander` completed 20.6s tokens=4962 cost=$0.0413
  - `deep_research` completed 375.2s tokens=6014 cost=$0.0413
  - `critic` failed 127.3s tokens=0 cost=$0.0000 ERROR=Task execution failed: litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible refusal

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
I cannot complete this request as given. You asked for deep research with primary sources comparing solar vs wind subsidy policy in the Nordics, but I do not have live access to primary source databas
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_research_light

> what is Estonia's current population?

`194 chars` · `230.3s` · harness=delivered

### Contract

**Intent:** The current population of Estonia, as a direct answer with an attributable source.

- shape: `short_fact`
- substance: words≥15
- citations: ≥1

**Must address:**
- [ ] a population figure between 1.2 and 1.5 million
- [ ] an as-of date or reference period
- [ ] attribution to a statistics authority (Statistics Estonia / Statistikaamet / Eurostat)

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- a multi-section report — brevity is correct here

**Degradation:** No acceptable degradation: this is answerable from one lookup.

**Ambiguity (resolved):** The true value changes over time, so the contract asserts a plausible RANGE plus attribution rather than a hardcoded number.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → research**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=2 result_chars=194
  - `commander` completed 24.7s tokens=0 cost=$0.0000
  - `research` completed 94.2s tokens=38274 cost=$0.0054
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

```text
As of January 1, 2026, the official population of Estonia was 1,360,745, according to Statistics Estonia (Statistikaamet).

Source: https://stat.ee/en/find-statistics/statistics-theme/population
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_writing_only

> write a short poem about a Finnish summer evening by a lake

`438 chars` · `30.3s` · harness=delivered

### Contract

**Intent:** A short original poem evoking a Finnish summer evening by a lake.

- shape: `poem`
- substance: lines≥6
- citations: not required

**Must address:**
- [ ] the setting is recognisable (lake, evening or late light, summer)
- [ ] verse form — line breaks, not a prose paragraph

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- commentary ABOUT how to write the poem instead of the poem
- a budget or abort notice in place of the poem

**Degradation:** None. No research dependency, so infrastructure is never a valid excuse here.

**Ambiguity (resolved):** 'short' resolved 2026-07-25 as 6-24 lines; longer is not a failure unless it stops reading as short.

### Provenance

- join: `best_effort_window_match`
- crews: **commander**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=2 result_chars=438
  - `commander` completed 13.4s tokens=4372 cost=$0.0279
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
**Kesäilta järvellä**

The sun forgets to set —
it only leans on the pines,
spilling copper across water
that has not moved for hours.

A loon's cry stitches the far shore
to the near one.
Smoke from
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_coding

> write a Python function that computes the Fibonacci sequence up to n terms, with a docstring and a couple of test cases

`761 chars` · `35.3s` · harness=delivered

### Contract

**Intent:** A working Python Fibonacci function with a docstring and a couple of test cases.

- shape: `code`
- substance: words≥40
- citations: not required

**Must address:**
- [ ] a Python function definition
- [ ] a docstring on that function
- [ ] at least two test cases or assertions

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')

**Degradation:** None.

**Ambiguity (resolved):** None. This is the one question with a fully objective check: the code must parse, execute, and yield the correct first-n sequence. Phase 2 executes it.

### Provenance

- join: `best_effort_window_match`
- crews: **commander**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=2 result_chars=500
  - `commander` completed 14.7s tokens=4709 cost=$0.0357
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
```python
def fibonacci(n):
    """Return a list with the first n terms of the Fibonacci sequence.

    The sequence starts 0, 1, 1, 2, 3, 5, ...

    Args:
        n: Number of terms to generate (mus
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_multi_and

> compare the economic and environmental trade-offs of Estonia's oil shale industry versus renewable energy, and evaluate which path serves the country better long-term

`2090 chars` · `409.5s` · harness=delivered

### Contract

**Intent:** A comparison of Estonia's oil shale industry against renewable energy on economic and environmental grounds, ending in a long-term judgement.

- shape: `prose_report`
- substance: words≥600
- citations: ≥3 (must resolve to run evidence)

**Must address:**
- [ ] oil shale AND renewables, compared rather than listed
- [ ] an economic dimension (cost, GDP share, employment)
- [ ] an environmental dimension (emissions, land, water)
- [ ] an explicit long-term judgement with reasoning

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer

**Degradation:** Observed 2026-07-25: an honest 'the retrieved evidence cannot answer this, here is what was retrieved instead' IS `blocked_infrastructure` — it named the cause and withheld invented content. It is not a pass.

**Ambiguity (resolved):** No literal 'report' in the prompt; resolved — the synthesis verbs make report-depth the correct reading.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → deep_research → critic**
- gate: presumed_clear (deep_research completed and no block phrasing found; NOT a positive confirmation — no column records the gate verdict; source: result_text)
- ticket: status=done difficulty=7 result_chars=500
  - `commander` completed 19.5s tokens=4838 cost=$0.0392
  - `deep_research` completed 294.2s tokens=5896 cost=$0.0392
  - `critic` failed 52.4s tokens=0 cost=$0.0000 ERROR=Task execution failed: litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible honest non-answer

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
**Finding: this question cannot be answered from the retrieved evidence.**

- None of the retrieved sources provide the quantitative material the question requires: oil shale's GDP share, Ida-Virumaa
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_short_chat

> hey, how's it going?

`174 chars` · `71.3s` · harness=delivered

### Contract

**Intent:** A brief, warm conversational reply.

- shape: `conversational`
- substance: words≥3
- citations: not required

**Must address:**
- [ ] a direct response to the greeting

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- any research or report machinery output
- more than ~80 words — verbosity here is itself a failure

**Degradation:** None.

**Ambiguity (resolved):** A light status or local-context mention is acceptable and was judged good on 2026-07-25; an internal telemetry dump is not.

### Provenance

- join: `best_effort_window_match`
- crews: **commander**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=1 result_chars=174
  - `commander` completed 10.4s tokens=4127 cost=$0.0225
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

```text
Hey! All good here — systems running normally, memory stores online, nothing on fire. It's a warm Saturday evening in Tallinn (sunset around 21:51). What can I help you with?
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_calendar

> what's on my calendar tomorrow?

`402 chars` · `192.1s` · harness=delivered

### Contract

**Intent:** Tomorrow's calendar events, or an honest statement that there are none / that the calendar is unreachable.

- shape: `short_list_or_fact`
- substance: words≥5
- citations: not required

**Must address:**
- [ ] either concrete events with times, or an explicit 'nothing scheduled', or an explicit 'calendar unavailable'

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- invented events

**Degradation:** 'Calendar unavailable' is `blocked_infrastructure`. 'Nothing scheduled tomorrow' is a full `pass` — an empty calendar is a correct answer.

**Ambiguity (resolved):** Ground truth depends on the operator's real calendar, so the contract cannot assert content. Scoring checks shape, plausibility and honest-unavailability handling only. Flagged as the weakest question in the set for automated scoring.

### Provenance

- join: `best_effort_window_match`
- crews: **commander → pim**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=3 result_chars=402
  - `commander` completed 0.9s tokens=0 cost=$0.0000
  - `pim` completed 47.7s tokens=78002 cost=$0.0111
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
You have several travel-related events on your calendar for tomorrow, July 27, 2026:
- **Flight LO786**: Tallinn (TLL) to Warsaw (WAW), departing at 11:20 AM (currently scheduled with a 24-minute dela
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_dossier

> give me an investment-grade company dossier on Bolt (the Estonian ride-hailing company)

`3094 chars` · `500.4s` · harness=delivered

### Contract

**Intent:** An investment-grade company dossier on Bolt: what it is, how it makes money, its market, and its risks.

- shape: `structured_dossier`
- substance: words≥700, sections_or_headings≥4
- citations: ≥3 (must resolve to run evidence)

**Must address:**
- [ ] company identification (Estonian, ride-hailing/mobility, founding)
- [ ] business model or revenue lines
- [ ] market position or named competitors
- [ ] financials, funding or valuation
- [ ] risks

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer
- a build-failure message in place of the dossier (observed 2026-07-25: 'Dossier build failed: OSError…')

**Degradation:** A pipeline crash is `fail`, not `blocked_infrastructure` — a crash is our defect, not an external one.

**Ambiguity (resolved):** 'investment-grade' resolved 2026-07-25 as: cited, structured, and covering the five must_address areas. It does not imply regulated-quality diligence.

### Provenance

- join: `best_effort_window_match`
- crews: **pim → commander → critic**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=7 result_chars=500
  - `pim` completed 47.7s tokens=78002 cost=$0.0111
  - `commander` completed 0.9s tokens=0 cost=$0.0000
  - `critic` failed 51.7s tokens=0 cost=$0.0000 ERROR=Task execution failed: litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible traceback; possible crash message

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
Dossier build failed: OSError: [Errno 36] File name too long: '/app/workspace/output/dossier_subia_context_loop_compressed_scene_2_items_0_74_self_assessment_loop_count_70_last_updated_2026_07_0_40_se
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_report_no_evaluate

> make me a report on how Estonian forests have changed over the years

`79 chars` · `75.8s` · harness=delivered

### Contract

**Intent:** A report on how Estonian forests have changed over time — descriptive, with no evaluation demanded.

- shape: `prose_report`
- substance: words≥500, sections_or_headings≥2
- citations: ≥2 (must resolve to run evidence)

**Must address:**
- [ ] forest area, cover or growing stock
- [ ] change across at least two time points or an explicit trend

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer

**Degradation:** Same as gs_report_forest.

**Ambiguity (resolved):** No synthesis verb, so a critical evaluation is NOT required — a purely descriptive report passes. Resolved 2026-07-25 to stop this being scored against the forest question's bar.

### Provenance

- join: `best_effort_window_match`
- crews: **critic → commander → research → research**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=5 result_chars=79
  - `critic` failed 51.7s tokens=0 cost=$0.0000 ERROR=Task execution failed: litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ
  - `commander` completed 18.3s tokens=0 cost=$0.0000
  - `research` failed 8.9s tokens=0 cost=$0.0000 ERROR=1 validation error for TaskOutput
raw
  Input should be a valid string [type=string_type, input_value=[ChatCompletionMessageToo...626e', type='function')], input_type=list]
    For further information
  - `research` completed 30.8s tokens=39070 cost=$0.0044
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible raw tool-call syntax

### Reply

```text
call:web_search{query:Estonian forest cover changes historical data statistics}
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---

## gs_ambiguous_short_report

> report on Tallinn's housing prices

`1903 chars` · `26.2s` · harness=delivered

### Contract

**Intent:** A short report on Tallinn housing prices: current level and recent direction.

- shape: `prose_report`
- substance: words≥250
- citations: ≥2 (must resolve to run evidence)

**Must address:**
- [ ] a current price level (e.g. EUR per square metre) or an explicit index level
- [ ] a trend direction over a stated recent period

**Must not:**
- raw tool-call syntax (e.g. 'call:web_search{...}')
- ReAct scratchpad ('Thought:', 'Action:', 'Observation:')
- raw internal JSON as the whole reply
- a traceback or exception string (OSError, Traceback, 'build failed:')
- multi-agent phase transcript ('[researcher]', '[writer]')
- SubIA/context-block scaffolding ('--- SubIA Context ---')
- prose written from model knowledge while disclaiming the lack of retrieval (e.g. 'drawn from general knowledge rather than retrieved sources') — this is `fail`, not `blocked_infrastructure`, because content is still being presented as the answer

**Degradation:** Same as the other report-class questions.

**Ambiguity (resolved):** THE contested question in this set. The prompt is three words and 'report' could mean a sentence or an essay. **Resolved 2026-07-25: a report ask, however terse, requires more than a bare figure — 250 words and two citations — but not full report depth.** Recorded here so it is not silently re-litigated per run. Revisit deliberately if it proves the wrong bar.

### Provenance

- join: `best_effort_window_match`
- crews: **research → research → commander → research**
- gate: **no gate ran** — no deep_research crew in window — the plain research crew has no evidence gate
- ticket: status=done difficulty=5 result_chars=500
  - `research` failed 8.9s tokens=0 cost=$0.0000 ERROR=1 validation error for TaskOutput
raw
  Input should be a valid string [type=string_type, input_value=[ChatCompletionMessageToo...626e', type='function')], input_type=list]
    For further information
  - `research` completed 30.8s tokens=39070 cost=$0.0044
  - `commander` completed 11.4s tokens=0 cost=$0.0000
  - `research` completed 5.8s tokens=11590 cost=$0.0012
- ⚠️ per-crew cost/token figures are UNRELIABLE: nested crews record the shared request tracker's totals at stop, so a child row can echo its parent's figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)

### ⚠️ Syntactic hints (not a verdict): possible ReAct scratchpad

### Reply

_⚠️ Only a 200-char preview is stored — this report predates full-reply capture, so groundedness cannot be judged._

```text
```
Thought: The user wants a detailed research report on Tallinn's housing prices as of July 2026. I need to find:
1. Average apartment price per square meter in euros.
2. Price trends from Q1-Q3 202
```

### Verdict

- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**
- clause it turns on: **______**

---
