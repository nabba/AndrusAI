# Refusing-Gates Diagnosis — 2026-07-25

**Question asked (handoff step 1):** 4 of 12 golden-set answers were withheld by internal gates. Are the crews producing nothing (crews broken), or are the gates over-firing on adequate work (gates miscalibrated)?

**Answer:** Both, split cleanly by case — **but neither is the dominant cause.** The dominant cause is that the baseline was recorded during an active OpenRouter credit outage, which the harness had no way to detect. Read Finding 0 first; it changes what the other findings mean.

Evidence: `control_plane.crew_tasks` + `control_plane.tickets` (2026-07-24 14:39–15:44 UTC), `workspace/logs/errors.jsonl`, `workspace/healing/loop_stalls/20260724T154312Z.txt`, and code read at `3e1c3797`.

---

## Finding 0 — The baseline is invalid: it was recorded during a 402 credit storm

- **69 × HTTP 402 "Insufficient credits"** between 14:51 and 15:29 UTC, and **all 69 failed over to `ollama/llama3.1:8b`** — the model the codebase itself documents as unable to handle tool calls (`app/llm_selector.py:209-211`).
- 402s by model: `z-ai/glm-4.7` ×52, `anthropic/claude-opus-4.7` ×9, `anthropic/claude-fable-5` ×7, `z-ai/glm-5.2` ×1.
- **The first 402 landed at 14:51 — during question #2.** Question #1 (forest report, 14:39:05–14:47:39) ran with **zero** 402s and **produced a complete report**.
- The storm peaked at 8/min across 15:19–15:26, which is exactly the Fibonacci question's window (15:18:14–15:44). **That is the 24-minute latency pathology** from handoff step 4 — every premium call 402'd, retried, and fell back to a local 8B model. It is no longer unexplained.
- The gateway wedge began at **15:43:12 UTC** (`loop_stalls/20260724T154312Z.txt`), within 3 seconds of `gs_multi_and`'s ticket being created (15:43:15). That wedge is why 6/12 questions returned `RemoteDisconnected`. `crew_tasks` has **no rows at all after 15:44:51**.

**Therefore:** the effective sample is 6 completed questions, 5 of which ran under credit exhaustion. The headline **2/12 (17%) measures a credit-exhausted system running on an 8B local fallback**, not the designed pipeline. The single question that ran with credits available produced real work.

Credits are now restored — **0 × 402 events on 2026-07-25**. (32,529 total 402s exist across the full log history, so this is a recurring mode, not a one-off.)

---

## Finding 1 — The deep-research evidence gate IS miscalibrated (structurally, not by threshold)

Two of the four refusals (dairy, solar/wind) blocked with the **identical** reason:

```
BLOCKED: research-evidence gate escalated to verify.
final synthesis cites no identifier retrieved by this run
```

That is `app/research/deep_path.py:429-433`. Critically, **evidence WAS retrieved** — otherwise the message would have been `"deep research retrieved no evidence sources"` (`deep_path.py:410-411`). The research passes ran; the draft was written; the gate rejected it on citation *form*.

**The gate contradicts itself.** Within the same function:

- The precondition (`deep_path.py:428`) accepts **only literal identifier substrings**:
  ```python
  cited = [identifier for identifier in identifiers if identifier in text]
  if not cited: return "verify", "final synthesis cites no identifier retrieved by this run"
  ```
- The per-block claim check (`deep_path.py:464-472`) explicitly accepts **`[S<n>]` labels** as valid tracing:
  ```python
  source_labels = {int(m.group(1)) for m in re.finditer(r"\[S(\d+)\]", block, re.IGNORECASE)}
  traces_source_label = any(1 <= n <= len(usable_rows) for n in source_labels)
  ```

**And the prompts teach the model to use `[Sn]`.** `_literature_evidence()` (`app/research/run.py:361`) renders every source as `[S1] Title (web) — https://…`, so `[Sn]` is the affordance the model is handed. Direct confirmation from the forest run's own critic note: the draft *"does not include … the source list ([S1]-[S4]) that inlin[e]…"*.

**Truncation removes the only citation form the precondition accepts.** The literal URLs live in the trailing source list. 22 max_tokens truncation events fired in the eval window (incl. `glm-5.2` at `max_tokens=4096` ×2), and the forest critic note says the draft *"terminates mid-sentence in Section 6 ('The EU Rene…')"*. Truncate the tail → lose the source list → lose every literal URL → guaranteed BLOCK, while the `[Sn]` citations that *did* survive in the prose are ignored by the precondition.

**Additional structural hazard:** for `kb`-sourced rows the identifier falls back to `row.get("id")` (`deep_path.py:389`, `419-424`) — an internal chunk id that can never legitimately appear in prose. For a KB-heavy run this precondition is **unpassable by construction**, regardless of answer quality.

---

## Finding 2 — The creative crew IS broken, and a correct poem was produced then thrown away

`control_plane.crew_tasks` shows **two** creative runs for the one poem request:

| # | started | ended | duration | tokens | cost | result |
|---|---|---|---|---|---|---|
| 1 | 15:13:26.846 | 15:16:17.865 | 171 s | 71,095 | $0.198364 | real poem produced |
| 2 | 15:17:22.067 | 15:17:22.385 | **0.32 s** | **71,095** | **$0.198364** | budget-exhaustion notification |

Run #2 reports **byte-identical token and cost figures** to run #1 while making zero LLM calls. Run #1's `result_preview` contains an actual finished poem — `# Lake Light (Finnish Summer Evening)` — inside its phase-1 researcher output.

**Root cause:** `_check_budget` (`app/crews/creative_crew.py:106-114`) compares `get_active_tracker().total_cost_usd` — the **whole-request cumulative** spend — against the creative crew's **own per-run** $0.10 budget. `start_request_tracking` is deliberately nesting-aware (`app/rate_throttle.py:633-648`), so nested crews share the outer request's tracker by design.

Consequences:
1. Run #1 tripped its own "budget" mid-run against its own accumulating spend → `BudgetExceeded` → best-so-far fallback (`creative_crew.py:435-439`) emitted the **raw phase transcript** (`[researcher]… [writer]…`) instead of the converged poem → vetting rejected that (correctly — it isn't an answer) → retry.
2. Run #2's very first `_check_budget` (`creative_crew.py:208`, *before* any LLM call) saw run #1's $0.198 still in the shared tracker → instant abort → the string *"Creative run hit its $0.10 budget before producing output (input ~887 tokens)"* returned as `final_output` (`creative_crew.py:443-447`) rather than raised as a failure.
3. The creative crew's effective budget is silently reduced by **all** prior request spend. Routing alone cost $0.014 on this request.

---

## Finding 3 — Crew failures are laundered through the critic into a self-blaming refusal

The notification string from Finding 2 is passed to the critic as reviewable `crew_output` (`orchestrator.py:3942-3947`). The critic reads it, correctly observes there is no content, and returns `BLOCK`; `_apply_review_result` (`critic_crew.py:60-65`) then renders:

> "I'm withholding the draft because adversarial review found an unresolved critical quality issue: The creative crew failed to produce any content due to a budget exhaustion error; no poem was generated"

The critic is behaving **correctly on the input it was given**. The defects are upstream and in presentation:
- a failed crew's error text should never reach the critic as output-to-review;
- the user-facing message misattributes causation to *review* and buries the actionable cause (a budget bug).

Same shape for solar/wind: *"The crew output contains no actual research… It is a notification of failure"* — the critic was handed the Finding 1 gate-block string and reviewed it as if it were an answer.

Note the poem was rated **difficulty 8**, which is the only reason the critic gate ran at all (threshold 7).

---

## Finding 4 — The forest report, the one clean run, died on an artifact contract it could not satisfy

Forest ran with zero 402s and produced a full report. It failed because "make me a report" was classified **artifact-shape expecting a PDF**. The crew had no working tools to create one — its own output says *"coding_session tools were not accessible during this edit"* — so it emitted `ARTIFACT: workspace/output/estonia_forest_health_report.pdf` and inlined the report text instead.

`verify_artifacts` found no such file and prepended `[ARTIFACT VERIFICATION FAILED]` **while preserving the report body** (`orchestrator.py:2369-2379`). A downstream stage then rewrote the whole thing into an apology about the missing PDF plus a note that the draft was truncated. The user got an error where a (truncated) report existed.

---

## Bonus — Cost attribution is broken globally

Every nested crew row records the **shared request tracker's totals at its stop moment**, not its own contribution. In all three deep_research cases the crew's `cost_usd` exactly equals its parent commander's: `0.032075`, `0.032550`, `0.014475`. Token counts are correspondingly meaningless (5,376 tokens for a 7-minute research run).

Two leak vectors compound it:
- `finalize_request_tracking()` is reached only on the happy path (`orchestrator.py:4189`) and one early return (`4113`). Three early returns (`3186`, `3233`, `3241`) and **any exception in the ~1,000 lines between** leak the tracker into the long-lived `commander` pool thread.
- `parallel_runner._throttled` (`parallel_runner.py:80-84`) calls `set_active_tracker(parent_tracker)` inside `crew-parallel` workers and **never clears it**.

Because `start_request_tracking` returns an existing tracker when one is set, a later request landing on a poisoned pool thread **adopts the stale tracker** — inheriting its spend for every budget check, including Finding 2's.

---

## Relevant to handoff step 3 (concurrency / Postgres)

> **⚠️ CORRECTED 2026-07-25 — the original text of this section was wrong.** It
> read the 15:43 dump's *first-listed* thread as the blocking one and concluded
> there was "a synchronous Postgres write in every LLM call's success callback"
> blocking the event loop. On re-reading the dump properly:
>
> * The asyncio loop thread's entire stack is `asyncio/runners.py:118 run` —
>   **no blocking Python frame at all.** The loop was starved, not blocked.
> * `rate_throttle._store` already runs on a detached daemon thread
>   (`rate_throttle.py:499`), so that write was never on the loop.
> * Only **1–2** training-capture threads appear in either dump, against 74 and
>   79 total threads.
>
> The dump's own header comment ("the asyncio loop thread's frame below names
> the blocking call") is a heuristic that misleads when the loop is starved
> rather than blocked. The two stalls are different events:
>
> | dump | duration | psycopg2-blocked threads | reading |
> |---|---|---|---|
> | `20260724T154312Z` | 6.2 s | 1 | GIL/CPU-starvation blip across 74 threads |
> | `20260724T171644Z` | **36.5 s** | **7** | the real pool wedge |
>
> Only the 17:16 dump is the incident. The single genuine issue in the
> training-capture path is that it spawns **one unbounded daemon thread per LLM
> call**, each taking a pool connection — a contributor to thread count and pool
> pressure, but not the mechanism, and both files involved
> (`rate_throttle.py`, `training_collector.py`) are `TIER_IMMUTABLE`. Left
> alone deliberately.

**The actual mechanism, measured.** `create_specialist_llm` → `select_model` →
`resolve_role_default` → `get_combined_scores` → `get_scores`, which looped over
the whole runtime catalog calling `get_external_score` once per model — then
again per candidate. Each of those is its own Postgres query. Measured against
unmodified `3e1c3797` with the live catalog size of 360 entries:

```
BASELINE queries for one get_scores() over a 360-entry catalog: 721
```

**721 synchronous queries per agent construction**, against the fixed
24-connection `CONTROL_PLANE_POOL_MAX` pool. Raising `thread_pool_size` 6→16
multiplied that across more concurrent constructions, which is exactly how the
gateway ended up with 7 threads blocked in `psycopg2/pool.py` inside this chain
and 3 watchdog force-restarts in 2.5 h.

Fixed 2026-07-25 (see the addendum below): bulk fetch + TTL cache, **721 → 1**.

---

## Recommended order (revised)

1. **402 circuit breaker.** Hard-pause the paid cascade and alert, instead of 69 silent failovers to a tool-incapable 8B model. This is the highest-leverage fix and it is operational, not architectural.
2. **Make the eval harness credit-aware.** Pre-flight credit check that *aborts* rather than records; per-question 402/failover counts in the report. A credit-exhausted run must never again be mistaken for a quality baseline.
3. **Gate precondition** (`deep_path.py:428`): accept `[Sn]` labels, consistent with the same function's own per-block check at `464-472`; refuse internal chunk ids as citable identifiers; raise draft/critique `max_tokens` so the source list survives.
4. **Creative budget:** delta-baseline the tracker at run entry (`current - baseline > budget`), and return a real failure rather than a notification string when a crew produces nothing.
5. **Stop feeding crew-failure text to the critic;** surface the underlying cause in the user-facing message.
6. **Report intent should not imply a PDF artifact** unless the user asked for a file.
7. **Then** re-record the baseline (after step 3 of the original handoff lands) and only then re-decide the v2 topology question.

**Phases 2–4 stay on hold — but the reason has changed.** The dominant cause of the 17% baseline was operational (credit exhaustion with no breaker), not topology and not gates. A lead agent + durable job lane would not have fixed any of Findings 0–4.

---

*Produced 2026-07-25 by read-only forensics against Postgres control-plane tables, the structured error log, stall dumps, and code at `3e1c3797`. No code changed.*

---

## Addendum — all seven fixes SHIPPED + DEPLOYED + LIVE-VERIFIED 2026-07-25

Commit `7205774b` on branch `fix/answer-quality-gate-diagnosis-2026-07-25`.
Deployed via `./scripts/deploy_gateway.sh --no-pull`; gateway recreated 11:11,
`/health` 200, `RestartCount=0`, no OOM, **zero tracebacks at boot**.

| # | Fix | Where |
|---|---|---|
| 1 | Credit circuit breaker — absorbs a blip, suppresses failover past 6 credit errors / 5 min per provider, pages the operator once, self-closes after 10 min quiet | `app/llm_credit_breaker.py` + both `rate_throttle` failover paths (TIER_IMMUTABLE edit, operator-approved) |
| 2 | Evidence-gate precondition accepts `[Sn]` labels (matching its own per-block check) and no longer treats internal KB chunk ids as citable | `app/research/deep_path.py:428` |
| 3 | Creative budget delta-baselined at run entry; per-crew cost/token rows report their own contribution | `app/crews/creative_crew.py` |
| 4 | Crew "no answer" is a typed signal; orchestrator skips vetting/critic and reports the real cause | `app/crews/outcome.py` + orchestrator short circuit |
| 5 | Request cost tracker finalized in a `finally` at the request boundary; `parallel_runner` restores each worker's prior tracker | `orchestrator.handle`, `parallel_runner.py` |
| 6 | "report" removed from the artifact-noun table — prose genre, not a file request | `app/agents/commander/artifact_intent.py` |
| 7 | Eval harness refuses to start during a credit outage, counts 402s per question, aborts mid-run, never reports `valid: true` for a contaminated run | `evals/run_eval.py` |

**Live sanity check inside the running container:** breaker thresholds 6/300s/600s;
`classify_task("make me a report on Estonian forests").is_artifact == False` while
`"make me a pdf report…" == True`; `_source_label_numbers` and `_run_baseline_usd`
present; no-answer signal round-trips.

**Tests:** 48 new tests pass. A broad sweep over the touched areas returns the
**identical 34 failures on unmodified `3e1c3797`** (verified in a baseline
worktree) — zero regressions; 761 passing vs 729 before.

**Not done, deliberately:** the baseline has NOT been re-recorded. Two of the
original handoff's prerequisites still stand — cache the benchmark-score query
in `create_specialist_llm` (and audit `training_collector._store_to_postgres`,
the *other* synchronous-Postgres-on-the-hot-path vector named by the 15:43
stall) before re-running the harness live. The harness will now refuse to
record an invalid baseline, but it will not protect the gateway from the
concurrency wedge.

---

## Addendum 2 — the DB-pool ceiling is fixed (2026-07-25)

The prerequisite for re-recording a baseline, and for ever re-attempting the
reverted concurrency bump.

**Change:** `llm_external_ranks.get_external_scores_bulk()` fetches every
model's external score in one query, behind a 300 s TTL cache invalidated on
`_upsert`; `get_external_score()` now serves from that map, so all existing
callers benefit without changing their call sites; `llm_benchmarks.get_scores()`
takes the map once instead of looping the catalog twice.

**Measured:** 721 → **1** query per `get_scores()` over a 360-entry catalog.
Verified by running the same measurement against unmodified `3e1c3797`.

**Tests:** `tests/test_benchmark_query_fanout_2026_07_25.py` — 8 tests pinning
the *query count*, not just the result, since a correctness-only test passes on
the broken version. Two pre-existing tests in `test_llm_external_ranks.py`
patched `get_external_score` as their seam and were updated to patch the bulk
function; their assertions are unchanged.

**Regressions:** none. The failure set over `-k 'llm or benchmark or
external_rank or catalog or selector or orchestrat or routing or commander or
critic or vetting or creative or deep_research or artifact or parallel or
tracker or credit or outcome'` is **70 on both** this tree and unmodified
`3e1c3797`, verified by diffing the two sorted lists.

**Still open before raising concurrency again:** the pool ceiling is no longer
the binding constraint, but nothing here re-tests the *nested-semaphore
starvation* that the reverted bump was originally trying to fix. Raise
`thread_pool_size` / `ollama_max_concurrent_crews` only with a fresh trace of
what the added threads do — and now that agent construction is ~721× cheaper in
DB terms, that trace should look very different.

---

## Addendum 3 — FIRST VALID BASELINE recorded 2026-07-25

`evals/results/baseline_2026-07-25.json`. Run in an ephemeral container on the
compose network (no host process), `--sender eval-harness-20260725`,
concurrency left at the reverted 6 / 2 so no concurrency change is bundled into
the measurement.

**`valid: true` · 0 credit errors · 0 HTTP errors · 9/12 delivered (75%)**

The gateway survived all twelve questions. On 07-24 it wedged at question 7 and
the last six never ran — that alone is the pool fix (721 → 1 queries per agent
construction) doing its job.

| question | 07-24 (void) | 07-25 (valid) | latency |
|---|---|---|---|
| gs_report_forest | FAILED | **FAILED** | 625s → 481s |
| gs_report_industry | FAILED | **FAILED** | 320s → 858s |
| gs_research_deep | FAILED | delivered | 1144s → 538s |
| gs_research_light | delivered | delivered | 41s → 157s |
| gs_writing_only | FAILED | delivered | 308s → **18s** |
| gs_coding | delivered | delivered | 1452s → **64s** |
| gs_multi_and | HTTP-err | **FAILED** | 1775s → 597s |
| gs_short_chat | HTTP-err | delivered | — → 40s |
| gs_calendar | HTTP-err | delivered | — → 42s |
| gs_dossier | HTTP-err | delivered | — → 163s |
| gs_report_no_evaluate | HTTP-err | delivered | — → 425s |
| gs_ambiguous_short_report | HTTP-err | delivered | — → 264s |

avg latency 472s → 304s; max 1775s → 858s.

**Caveat on the comparison:** the 07-24 column is the void run, kept only for
orientation. Five of its six completed questions ran under credit exhaustion, so
the latency deltas cannot be attributed cleanly to the pool fix — the Fibonacci
1452s → 64s and poem 308s → 18s improvements are consistent with agent
construction having been the bottleneck, but a credit storm was also removed.
Only 07-25 is a reference point.

### The three remaining failures are NOT gate miscalibration

This is the important finding, and it is a different problem from the one fixed.

**gs_report_forest, gs_report_industry — the anti-fabrication check, firing
correctly.** Both cleared the precondition I fixed (they no longer block on
citation *format*) and now stop one check later, at `deep_path.py`'s untraced-
citation check:

```
BLOCKED: … final synthesis contains citation(s) not retrieved by this run:
  https://elfond.ee, https://keskkonnaagentuur.ee, https://www.eea.europa.eu
  https://ec.europa.eu/eurostat, https://news.err.ee, https://piimaliit.ee
```

Every one is a **bare organization homepage**. These are real institutions, not
hallucinated domains — but they were not in the run's evidence set. The model is
padding its bibliography with plausible org homepages it never retrieved, which
is precisely what that check exists to catch. Note the check already accepts
substring matches (`token in identifier`), so a retrieved deep link would have
covered its own domain; these genuinely aren't there.

**Root cause is upstream, in the prompts.** `_build_draft_prompt` says *"attribute
it to its source (author, link, or arXiv id)"* and `_build_critique_prompt` says
*"Preserve real URLs/identifiers, cite sources inline"*. **Neither forbids
introducing a source that is not in the supplied evidence.** The model is doing
what it was asked. Fix: instruct both steps to cite ONLY from the supplied
evidence, preferring `[Sn]` labels, and to name an unretrieved organisation
without a URL.

**gs_multi_and — the critic, also firing correctly**, on a retrieval failure:
*"The research crew received an off-topic evidence bundle containing no data on
oil shale or renewables"*. Evidence *retrieval* returned irrelevant results; the
draft built on it was rightly withheld. Note this went through the `research`
crew + critic, not `deep_research`, so the no-answer short circuit did not cover
it — as documented in `app/crews/outcome.py`.

**The no-answer fix is confirmed working.** All three failures now read *"The
deep_research step didn't produce an answer: …"* with the real cause, instead of
blaming adversarial review for an upstream bug.

### What 75% does and does not mean

It is a *delivery* rate, not a quality score. `gs_ambiguous_short_report`
("report on Tallinn's housing prices") counts as delivered on **122 characters**
— thin for the ask. The golden set still has no groundedness or completeness
rubric, so the plan's success metrics 2 and 3 remain unmeasured.

### Bearing on ANSWERING_V2_PLAN Phases 2–4

The plan's premise was that *topology* caps quality. With a valid measurement in
hand: **9/12 delivered, no wedge, no discarded work, and every remaining failure
is a grounding or retrieval defect that a lead agent would inherit unchanged.**
Two prompt lines and better evidence retrieval address all three. Nothing here
argues for replacing the serving topology; the case for Phases 2–4 is now code
health (5 research paths → 1, Python 3.14 / chromadb-2 unblocking), not answer
quality — and should be scheduled as such.

---

## Addendum 4 — closed-citation-set fix, and two new findings (2026-07-25)

Deployed `06caad82`. Re-ran only the three failing questions
(`evals/results/retry_citation_fix_2026-07-25.json`, sender
`eval-harness-20260725b`). Honest scorecard — **not** 2/3 fixed:

| question | before | after | real status |
|---|---|---|---|
| gs_report_forest | blocked, untraced citations | **delivered, 6500 chars** | **genuinely fixed** |
| gs_report_industry | blocked, untraced citations | blocked, *different* gate | **partly fixed** |
| gs_multi_and | critic block | "delivered", 1780 chars | **NOT fixed** — see below |

**gs_report_forest — fixed, and for the right reason.** The original incident
question, failing since 07-23, now delivers a full report. It opens by scoping
itself honestly: *"Note on scope: the user asked for 'data over the years.' The
retrieved evidence in this run do[es not…]"* — which is exactly the intended
behaviour. Told it may only cite what it was given, the model states the
limitation instead of padding the bibliography.

**gs_report_industry — the citation padding is gone; it now fails one gate
later.** Before: `contains citation(s) not retrieved by this run` (untraced
URLs). After: `anti-fabrication verification — empirical claims with no recorded
measurement or retrieval-traced citation` — a different check (`HINT_VERIFY`,
`run.py:1240`). Latency 858s → 217s, so it fails much earlier. The draft has
stopped inventing links but still asserts numbers it cannot trace. Real progress
in mechanism, still not delivered.

### New finding A — the harness counts an honest non-answer as delivered

`gs_multi_and` scored *delivered* on this content:

> **Finding: this run cannot answer the question — the retrieved evidence
> contains no Estonia energy data.** All retrieved sources are off-topic: AI
> research-synthesis notes on a safety-invariant architecture [S1][S2][S3][S9]
> [S10]; a generic probabilistic energy-forecasting model [S4]; a 250B-parameter
> language model report [S5]; a solar-flare EUV irradiance predictor (solar
> physics, not solar power) [S6]; and two text-diff web tools [S7][S8].

That is a *better failure* than the previous self-blaming critic block — it names
the real cause and cites its evidence correctly. But it is **not an answer to the
question**, and `delivered` counts it as one. This is the same class of defect as
the original scorer bug (which counted refusals as successes): the metric cannot
see a well-formed non-answer. **The 9/12 baseline is therefore an upper bound.**
Fixing it properly needs the groundedness/completeness rubric the golden set
still lacks, not another substring marker — marker-based scoring has now been
wrong twice.

### New finding B — evidence retrieval is returning off-topic, partly
### self-referential content

For "Estonia's oil shale industry versus renewable energy", `collect_deep_evidence`
returned AI-safety architecture notes from the system's **own** knowledge base,
a 250B language-model report, two text-diff web tools, and a **solar-flare EUV
irradiance predictor** — a keyword collision on "solar". Ten sources, zero on
topic.

This is upstream of every gate and is now the clear top defect: the gates and
prompts are working correctly on evidence that should never have been handed to
them. It also explains `gs_report_industry` — a draft cannot trace claims to
evidence that isn't about its subject. Worth noting the run-to-run variance this
implies: `gs_multi_and` was a *control* in this re-run and it moved, so single
runs cannot distinguish a fix from a lucky sample. The forest result is credible
because the blocking check and its cause were identified precisely; it is not
credible on n=1 alone.

**Next, in order:** (1) fix deep-evidence retrieval — relevance filtering and
excluding the system's own internal notes from research KB hits; (2) add the
groundedness/completeness rubric so `delivered` stops overcounting; (3) re-run
the full twelve once both land.

---

## Addendum 5 — the relevance filter was WRONG and has been REVERTED (2026-07-25)

`72e870b4` reverted in `c3e669f9`. It caused a regression and I shipped it on
fixtures of the wrong shape.

**Observed regression:** `gs_report_forest` — which delivered 6500 chars after
the citation fix — came back at 208 chars, blocked by anti-fabrication
verification. The filter had starved the run of evidence.

**Root cause: I filtered against the wrong text.** `collect_deep_evidence`
receives the **router-authored task string**, not the user's question. The
rejection logs show what that means:

```
rejected off-topic hit for 'please make me a report on estona forest health…'
  (shares 1 of 18 question terms (needs 2)): epi_004509
rejected off-topic hit for 'Produce a critical, well-sourced report (roughly
  1500-2000 words, structured wit…'
  (mentions none of the question's entities: asian, association, baltic): epi_004393
rejected off-topic hit for 'Estonian dairy industry market structure…'
  (shares 1 of 125 question terms (needs 2)): epi_004829
```

Two distinct defects:

1. **125 "question terms"** — the task string is a verbose instruction whose
   significant terms are dominated by formatting boilerplate ("produce",
   "critical", "sourced", "roughly", "structured", "words"). A genuinely
   relevant source matches almost none of them, so the overlap test measured
   the wrong thing entirely.
2. **`entities: asian, association, baltic`** — `_entity_terms` scraped every
   incidental capitalised word out of that long string. The real discriminator
   (`Estonian`) was diluted among noise, and legitimate KB evidence (`epi_*`
   rows) was rejected wholesale for not mentioning "Asian".

**Why the tests passed anyway.** Every fixture I wrote used a clean
user-style question (`"compare the economic and environmental trade-offs of
Estonia's oil shale…"`). Production never passes that. The tests were
well-constructed and thorough in both directions — and tested an input shape
the code never sees. Catching the `Estonian`/`forests` morphology bug
pre-deploy gave false confidence that the *rest* of the design was sound.

**What a correct version needs:** derive the topic core from the **user's
original question**, not the router task string — either by threading the user
question through to `collect_deep_evidence`, or by extracting entities and
salient nouns and ignoring instruction boilerplate. Entity matching should use
only high-confidence entities, not every capitalised token. And any future
fixture set must include a real router task string captured from the logs.

**Still live and still good** (verified in the running container after the
revert): the `[Sn]` gate precondition, the closed-citation-set prompts, the
creative budget baseline, the credit breaker, the tracker-leak fix, the
721→1 query fix. The reverted filter was the only regression.

### Operational hazard found while reverting

The revert deploy **silently failed and took the gateway down**. A prior
half-completed recreate had left a stale container holding the name
`crewai-team-gateway-1`, so `docker compose up` failed with a name conflict
*after* stopping the running container:

```
Container f293923c6c3b_crewai-team-gateway-1  Error response from daemon:
  Error when allocating new name: Conflict. The container name
  "/crewai-team-gateway-1" is already in use…
```

`/health` went to `000`. Two contributing factors worth fixing:

* I had piped `deploy_gateway.sh` through `grep -E "Recreated|Started|deploy
  complete|✗"`, which **swallowed the error** — the run looked like it merely
  printed nothing. Never filter a deploy script's output down to expected-success
  patterns; the failure line will not match them.
* `deploy_gateway.sh` does not detect or clear a stale `<hash>_<name>` container
  before `compose up`, and does not fail loudly when the gateway ends up down.
  Worth hardening.

Recovered by removing both stale containers (stateless — data lives in the
postgres services and the `workspace` bind mount) and running
`docker compose up -d --no-deps gateway`. Healthy in 48s, `RestartCount=0`,
zero tracebacks.

---

## Addendum 6 — designing the filter deliberately concluded: don't build it

Asked to design the reverted relevance filter properly rather than defer it. The
design work started by establishing what the code actually receives — the step
skipped the first time — and that investigation invalidated the filter's premise.

**What the input really is.** `DeepResearchCrew.run` → `ResearchCrew._extract_core_topic`
→ `execute_deep_research` → `run.goal` → `collect_deep_evidence(run.goal)`.
`_extract_core_topic` strips *injected context blocks* (KB passages, conversation
history), **not** the router's instruction wrapper. So the goal's shape depends
on whatever the routing LLM writes — sometimes near the user's words, sometimes
"Produce a critical, well-sourced report (roughly 1500-2000 words, structured
with headings) on…". No filter can assume either shape.

**Then two plausible hypotheses died on contact with evidence.** Recording both,
because each felt convincing:

1. *"Instruction-shaped queries return nothing from searxng."* **Wrong.** Probed
   the live instance: instruction-shaped queries return **20 results**, the same
   as topic-shaped ones. Query shape is not why `searxng:no_results` was logged.
2. *"The `search_validation` lexical-overlap filter drops everything."* **Wrong.**
   It requires only **one** term of overlap (`if query_terms and not overlap`), so
   it cannot explain an empty result set — and a long instruction query makes
   overlap *more* likely, not less.

**What IS established.** `web_search` has three tiers — Brave (paid), self-hosted
searxng, DDG. Brave is **quota-exhausted (HTTP 402)**, and on 2026-07-25 all
three failed together at least twice:

```
web_search: all backends failed for 'Conduct deep comparative research on solar
  vs wind subsidy p': ['brave:quota', 'searxng:no_results', 'ddg:no_results']
```

With no web evidence, deep research falls back to KB (our own internal notes) and
arXiv (keyword collisions — a solar-flare EUV paper for "solar"). **That is
exactly the off-topic evidence set observed in `gs_multi_and`.** Both searxng and
`_search_searxng` work when probed now, so that total failure was transient and
is not currently reproducible.

One incidental find: for a goal beginning "Produce a critical report on Estonian
forest health…", the evidence set contained
`https://www.merriam-webster.com/dictionary/produce` — a dictionary definition of
the instruction's leading verb.

### The design conclusion

**The off-topic-evidence problem is retrieval AVAILABILITY, not ranking.** A
relevance filter sits at the wrong end: when the web tier is down, it would
correctly reject the KB/arXiv fallback and turn a bad answer into *no* answer.
That is a more honest failure but not a better one, and it would have hidden the
real cause — as it already did once, by regressing `gs_report_forest` into a
no-evidence block that looked like a gate problem.

So the filter is **not** being rebuilt. The operator action that unblocks web
evidence is **topping up the Brave quota**; the reproducibility gap
(`searxng:no_results` while searxng demonstrably works) needs a logged reason
from `_search_searxng`'s exception path before it can be diagnosed — it currently
swallows the error at `logger.debug`.

### What was fixed instead

`1d5aa105` — **the Brave quota backoff is now persisted.**
`_brave_quota_blocked_until` lived only in a module global, so every gateway
restart reset it, re-probed the exhausted API, earned another 402, and logged
another 24h backoff the next restart would also forget. Three such logs on
2026-07-25 (09:25, 14:32, 16:19) match that day's container recreations. Now
written to `workspace/.brave_quota_block` (a bind mount) and read once per
process, failure-isolated in both directions. 8 tests including a simulated
restart; deploy verified by checking the code inside the running container, not
just `/health`.

---

## Addendum 7 — the 12/12 run was an artifact, and it exposed the real defect

Brave limit raised (it was a self-imposed **$25/month spend cap**, `current_spend
25.0 / usage_limit 25.0`, not a lapsed subscription). Brave verified live through
the app's own path: `search_brave` → 5 rows, `backend used: brave`, empty failure
chain, sources from IEA/OECD. Full 12-question run:
`evals/results/after_brave_restored_2026-07-25.json` — **12/12 "delivered",
`valid: true`, 0 credit errors, 0 HTTP errors.**

**That number is meaningless.** Reading every reply instead of the summary:

| question | what was actually returned |
|---|---|
| `gs_report_no_evaluate` | `call:web_search{query:…}` — **raw tool-call syntax**, 79 chars |
| `gs_ambiguous_short_report` | ```` ```Thought: The user wants… ```` — **ReAct scratchpad** |
| `gs_report_industry` | ```` ```json {"title":…,"subjects":[…]} ```` — **raw internal JSON** |
| `gs_dossier` | `Dossier build failed: OSError: [Errno 36] File name too long` |
| `gs_report_forest` | *"…drawn from general knowledge…"* — **explicitly ungrounded** |
| `gs_multi_and` | honest non-answer |
| `gs_research_deep` | refusal, falsely claiming no live source access |

**Genuine answers: 5 of 12** (`short_chat`, `research_light` with source,
`writing_only`, `calendar`, `coding`). The harness's `delivered` marker matches
none of the leakage shapes, so it scored all seven failures as successes.

### Root cause: the SubIA context block was the crew's task

`crew_tasks` topics read literally `Research: --- SubIA Context --- loop:
compressed scene (2 items…`. `_consume_pre_task_context`
(`orchestrator.py:73-77`) prepends the SubIA block to every crew task, and
`ResearchCrew._extract_core_topic`'s boundary list did not include that marker —
so the crew's notion of its own task *was* the context block. That explains the
leaked tool call, the leaked scratchpad, and the dossier filename built from
`dossier_subia_context_loop_compressed_scene_2_items_0_74_self_assessment…`.

**Present since at least 2026-07-24** — the same shape appears in that day's rows
for the creative and delegated-research crews. It was visible in the very first
`crew_tasks` query of this investigation and its significance was missed;
degraded crew output was read as an answer-quality problem instead.

Fixed in `4c11f769`: `--- End SubIA Context ---` added as a strip boundary, with
a test pinning it against `_build_injection`'s source so the two lists cannot
drift apart again. Deployed and verified inside the running container.

### Two corrections to earlier claims in this report

1. **Routing is non-deterministic, which confounds every cross-run comparison
   here.** The same report questions went to `deep_research` (gated, grounded) at
   14:32–16:30 and to plain `research` (ungated, leaky) at 17:51. The sequence
   "2/12 → 9/12 → 12/12" was never measuring one pipeline. Any future comparison
   must record which crew handled each question.
2. **The closed-citation-set prompt (`06caad82`) may license labeled-ungrounded
   reports.** It tells the model to name unretrieved sources in words and mark
   them as not retrieved; `gs_report_forest` generalised that to writing the
   entire report from general knowledge *and disclosing it*. That is a fair
   reading of the instruction. Needs tightening: disclosure is not a substitute
   for evidence.

### Standing conclusion on the eval harness

Marker-based scoring has now been wrong three times — refusals counted as
successes, honest non-answers counted as successes, and now internal leakage
counted as success. It should be replaced with a positive check (does the reply
answer the question, with citations where required), not extended with more
substrings.

---

## Addendum 8 — Phase 1 provenance immediately found two systematic bugs

`docs/EVAL_HARNESS_V2_PLAN.md` Phase 1 shipped: the harness stores full replies
and per-question UTC timestamps, `evals/provenance.py` joins each question to
`control_plane.tickets`/`crew_tasks`, and `evals/review_sheet.py` renders the
contract + provenance + full reply for human labelling. Run provenance where the
credentials already live — `docker compose run --rm --no-deps ... gateway` injects
the service env, so nothing is duplicated.

Joined 12/12 on the "12/12 delivered" run. The crew column alone explains most of
the failures:

| question | crews | gate | contract verdict |
|---|---|---|---|
| gs_report_forest | commander → **deep_research** → critic | presumed_clear | fail (ungrounded) |
| gs_report_industry | commander → **research** | **none** | fail (raw JSON) |
| gs_research_deep | commander → **deep_research** → critic | presumed_clear | fail (false capability claim) |
| gs_multi_and | commander → **deep_research** → critic | presumed_clear | blocked_infrastructure |
| gs_dossier | **pim** → commander → critic | none | fail (crash) |
| gs_report_no_evaluate | commander → **research** ×2 | **none** | fail (tool-call leak) |
| gs_ambiguous_short_report | commander → **research** ×3 | **none** | fail (scratchpad leak) |

**Three of the four leakage failures are the plain `research` crew, which has no
evidence gate at all.** The gate work in this report only ever protected the
`deep_research` path; non-deterministic routing decides which one a report
question gets.

**`gs_dossier` never reached a dossier crew** — it went to `pim`. The
`company_dossier` fast-route did not fire, which is why an "investment-grade
dossier" request produced a filename crash.

### Bug A — the critic crew is failing 100% of the time, silently

Every question where the critic ran (5 of 5) failed identically:

```
Task execution failed: litellm.BadRequestError: OpenrouterException -
  {"error":{"message":"Provider returned error","code":400,
   "metadata":{"raw":"{\"type\":\"error\",\"error\":{\"type\":\"invalid_requ…
```

This is **D4 from the original 2026-07-24 diagnosis** — the native-tools path
building a message array with a system message after assistant messages, yielding
an Anthropic-side `invalid_request_error`. It was listed as deferred and has been
live ever since.

It fails *silently*: `critic_crew.review()` catches the exception and returns the
original output unchanged ("On failure, return original output — don't block
delivery"). So adversarial review has not run on any high-difficulty answer, and
nothing surfaced that. Every "the critic blocked this" conclusion earlier in this
report refers to the *pre-2026-07-25* runs; in the latest run the critic never
executed at all.

### Bug B — the leaked scaffolding is a TaskOutput validation failure

The two questions that returned raw scaffolding both show:

```
research  failed  1 validation error for TaskOutput
raw
  Input should be a valid string…
```

So the leak is not "the crew was confused about its task" — CrewAI's
`TaskOutput` rejected the crew's output and the raw partial content escaped as
the reply. That is a different and more tractable defect than the SubIA topic
pollution fixed in `4c11f769`, and it needs its own fix: a crew whose output
fails validation must not have its raw buffer delivered.

### Assessment

Phase 1's gate was "the sheet answers, for every historical run, which crew
handled this and did a gate run." It does — and in doing so it found two
systematic bugs that reading replies did not. Neither is fixed here.

Known limitation: best-effort joins (reports predating timestamp capture) can
include a neighbouring question's tail, because crews outlive the HTTP response.
Windows are clamped to the previous question's ticket, which removed most of it;
`gs_report_no_evaluate` and `gs_ambiguous_short_report` still show a leading
neighbour crew. Reports written from now on carry exact timestamps.

---

## Addendum 9 — critic 400 fixed; and an outage while probing it

### The fix (`e698b6c6`, deployed + verified in container)

Every OpenRouter upstream — Azure, Amazon Bedrock, Google, Anthropic — rejects
the same shape identically, so it is our request, not a provider quirk:

```
messages.1: role 'system' must precede an 'assistant' message or end the array
```

**94 occurrences in one afternoon**, and it broke the critic crew 100% of the
time *silently*, because `critic_crew.review()` catches the exception and returns
the crew's original output. This is **D4 from 2026-07-24**, deferred then and
live ever since.

`app/llm_message_order.py` hoists any system message appearing after the first
assistant message (and not final) to the front, preserving order, never editing
content. Wired into `BudgetAwareCompletion.call/acall` — the per-call layer every
factory-built LLM already passes through — before cache-control injection. 14
tests including a wiring assertion, because without the wiring the module is
inert, which is exactly the state the critic was in.

Hoisting rather than re-roling because the offending messages are context
summaries from `history_compression.to_langchain_messages`, which interleaves
`[Previous exchange summary]` system messages between topics — one lands after an
assistant turn whenever a summarised topic follows an unsummarised one.

### The outage

Probing the fix with one `gs_research_deep` question wedged the gateway
(`/health` 000 for ~1694 s) and Postgres crashed and auto-recovered. The
watchdog's restart **failed**:

```
Cannot restart container … PID 17953 is zombie and can not be killed.
Use the --init option when creating containers…
```

Recovered by `docker rm -f` + `docker compose up -d --no-deps gateway`; healthy
in 60 s, `RestartCount=0`, no tracebacks, fix still live. Postgres had already
completed crash recovery on its own (`redo done`, `ready to accept connections`).

**Not caused by the message-order change** — `llm_message_order` appears in
neither stall dump, and the transform is pure list manipulation with no I/O or
locks. What the dumps do show:

* `20260725T200233Z` (19.6 s stall): the asyncio **loop thread** is inside
  `crewai/flow/runtime/__init__.py:1994 kickoff` → `crewai/experimental/
  agent_executor.py:2780 invoke`. **A crew kickoff is executing on the event
  loop thread**, which is precisely the serving-path defect `ANSWERING_V2_PLAN`
  exists to remove. `_commander_pool` exists so this should not happen.
* 3–4 threads still blocked in `psycopg2/pool` despite the 721→1 query fix — that
  fix reduced per-construction cost but did not eliminate pool contention.

So the probe triggered a pre-existing wedge class, not a new one. Two follow-ups
worth filing: find why a crew kickoff reaches the loop thread, and add `--init`
(or `init: true` in compose) to the gateway so a zombie PID cannot block the
watchdog's only recovery mechanism.

### What remains unverified

**The critic fix has not been confirmed end-to-end on a live crew.** It is
unit-tested and verified present in the container, but the probe that would have
proven a real critic run completing without a 400 is what caused the outage. The
honest status is "deployed and unit-verified, not yet observed working in
production".

### Addendum 9b — the critic fix is now verified live, with causation

Two separate confirmations, and they establish different things:

**1. The critic completed for the first time.** Invoked directly in the running
container with a small draft: ran 37.1 s, produced a 2329-char substantive
review, returned a verdict. `control_plane.crew_tasks`:

```
21:05:32 | critic | completed |
17:50:20 | critic | failed    | Task execution failed: litellm.BadReques…
17:37:34 | critic | failed    | Task execution failed: litellm.BadReques…
```

Zero new `invalid_request` errors (delta 0 across the whole probe). **But the
normalizer logged no firing for that run**, so this alone does not prove the fix
caused it — that invocation simply did not build an offending array.

**2. Causation, established directly.** Calling a factory-built LLM with the
exact production shape (`[assistant, system, user]`):

```
CLASS: BudgetAwareCompletion | has _normalize: True
INFO app.llm_message_order  hoisted 1 misplaced system message(s) to the front
RESULT: SUCCEEDED 'OK'
```

The array that previously returned 400 from every upstream now succeeds, with the
repair visibly firing. So the fix demonstrably repairs the reported shape, and the
critic's first successful run is consistent with it.

The critic's review was also *good* — it correctly judged a two-sentence "report
over the years" as not a report, named the missing evidence (SMI time-series, FAO
FRA, crown-condition monitoring) and called it unrepairable rather than revisable.
That quality of review has not been running for days.

**Incidental finding: difficulty scoring is non-deterministic too.** The poem
question scored **difficulty 8** earlier on 2026-07-25 and **difficulty 2** on
re-run, so only the commander ran and the critic never fired (threshold 7). Same
question, same day. This compounds the routing non-determinism already recorded:
whether a question gets adversarial review at all varies run to run.

### Addendum 9c — CORRECTION: the "crew kickoff on the event loop" claim was wrong

Addendum 9 stated that `20260725T200233Z` showed *"a crew kickoff executing on the
event loop thread"*. **That is wrong**, and it is the third hasty stall-dump
reading in this report (see also the Addendum-2 correction on the 15:43 stall).

The mistake: a thread-classifying script matched on the presence of
`asyncio/runners` frames and labelled that thread "the asyncio loop". It is not.
Read to the bottom, the crew thread is:

```
Thread 0x0000fffc46d5f160
  asyncio/runners.py:118 run
  crewai/flow/runtime/__init__.py:1994 kickoff
  crewai/experimental/agent_executor.py:2780 invoke
  crewai/agent/core.py:879 _execute_without_timeout
  concurrent/futures/thread.py:59 run
  concurrent/futures/thread.py:93 _worker      <-- a POOL WORKER
```

It bottoms out in `_worker` — exactly where crew work belongs. CrewAI's `kickoff`
calls `asyncio.run()` internally, creating its own loop *inside that worker*. That
is normal, not a defect.

**What the three dumps actually show, consistently:**

| dump | stall | loop-thread innermost frame | threads |
|---|---|---|---|
| `20260725T181203Z` | 50.2 s | `uvicorn/server.py:74 run` — outermost frame, no blocking call | 73 |
| `20260725T200233Z` | 19.6 s | FastAPI/prometheus route-name resolution | 73 |
| `20260725T201827Z` | 16.8 s | `asyncio/tasks.py:714 sleep` — **idle** | 69 |

Two of three have the loop in **no blocking call whatsoever** — it is simply not
being scheduled. Only 1–4 threads are inside `crewai`/`litellm` at any moment,
against ~70 threads total. The consistent reading is **CPU/GIL starvation from
thread sprawl**, the same conclusion the 15:43 stall reached. The 20:02 FastAPI
frame is where a sample happened to land on a starved loop, not a cause.

**So the actionable finding is thread sprawl, not crew placement.** ~70 threads in
one process: `crew-parallel` (6), `ctx-fetch` (12), `commander`, the vetting pool,
`idle-light`, Discord/Signal clients, plus **one `training-capture` thread spawned
per LLM call** (`rate_throttle.py:499`). The per-call thread is the only unbounded
contributor, and both files involved are `TIER_IMMUTABLE`, which is why it was
left alone earlier — but it should be the first thing measured before any
concurrency ceiling is touched again.

**Methodological note, since this keeps recurring:** a single `faulthandler` dump
is one sample of a starved process. It shows where threads *were*, not what caused
the stall, and the dump's own header comment — claiming the loop thread's frame
names the blocking call — actively misleads when the loop is starved rather than
blocked. Three claims in this report were made from single dumps and two had to be
retracted. Correlate across multiple dumps and count threads before asserting a
mechanism.

---

## Addendum 10 — claims ledger: what is probed, what is inferred

This report is now ten addenda long and will be someone's starting point. Two of
its three stall-dump conclusions had to be retracted, so every substantive claim
is graded here by the evidence actually behind it. The original
`ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md` was believed wholesale and sent this
effort down a wrong path for a day; this ledger exists so that cannot repeat.

Grades: **PROBED** = a direct experiment or measurement was run · **MEASURED** =
counted from logs/DB programmatically · **OBSERVED** = seen in one source, not
independently confirmed · **INFERRED** = reasoning, no direct test ·
**UNKNOWN** = explicitly unresolved · **RETRACTED** = asserted then withdrawn.

### Fixes — all PROBED

| claim | grade | evidence |
|---|---|---|
| Evidence gate's two halves disagreed on `[Sn]`; KB chunk ids made it unpassable | PROBED | code read + tests that fail on the old logic and pass on the new |
| Creative budget compared whole-request spend to a per-run cap | PROBED | DB shows two runs with byte-identical 71,095 tok / $0.198364, second lasting 0.32 s; unit test reproduces |
| Request cost tracker leaked into pooled threads | PROBED | test asserts the tracker is absent from pool workers after a run |
| 721 Postgres queries per agent construction → 1 | PROBED | measurement executed against unmodified `3e1c3797` printed `721`; live container now prints `1` |
| SubIA context block became the crew's task | PROBED | DB topics read `--- SubIA Context ---`; fix live-verified in container |
| Critic 400 (system-after-assistant) | PROBED **with causation** | offending array `[assistant, system, user]` through a factory LLM: normalizer fires, call returns `OK`; previously 400 from every upstream |
| Brave 402 was a self-imposed $25/mo cap | PROBED | direct API call returned `current_spend 25.0 / usage_limit 25.0` |
| Substring scorer counts 5 of 6 known failures as success | PROBED | executed against the six shapes |

### Diagnostic claims — mixed

| claim | grade | note |
|---|---|---|
| 69 × HTTP 402, all failing over to `ollama/llama3.1:8b` during the baseline | MEASURED | counted from `errors.jsonl` |
| First 402 landed during question 2; question 1 ran clean | MEASURED | timestamps |
| **The 402 storm caused the 24-minute Fibonacci** | **INFERRED** | correlation only. Fibonacci later ran 64 s and 35 s, but the 721→1 fix landed in between, so the two causes cannot be separated. Do not quote as established. |
| **max_tokens truncation removed the source list, hiding the literal URLs** | **INFERRED** | supported by the forest critic's own note ("terminates mid-sentence", "does not include the source list") but never directly tested |
| Routing is non-deterministic (same question → `deep_research` vs `research`) | MEASURED | DB crew rows across runs |
| Difficulty scoring is non-deterministic (poem d=8 then d=2 same day) | MEASURED | `tickets.difficulty` |
| Critic crew failed 100% and silently | MEASURED | 5/5 `crew_tasks` rows failed; 94 `invalid_request` events in one afternoon |
| Leaked scaffolding is a `TaskOutput` validation failure | OBSERVED | the crew error string; the delivery mechanism was not traced |
| `gs_dossier` never reached a dossier crew (routed to `pim`) | OBSERVED | crew sequence in one run |
| The 12/12 run was really ~5/12 | OBSERVED + PROBED | replies read by hand; the Phase 2 scorer independently reproduces 6/6 comparable labels |

### Stalls — one retraction, one measurement

| claim | grade | note |
|---|---|---|
| "Synchronous Postgres write in every LLM success callback blocked the loop" | **RETRACTED** | loop thread had no blocking frame; that write already ran on a daemon thread |
| "A crew kickoff is executing on the event loop" | **RETRACTED** | that thread bottoms out in `_worker` — a pool worker, where crew work belongs |
| Stalls are CPU/GIL starvation from thread sprawl | MEASURED | across three dumps the loop thread has no blocking call in two, with 1–4 threads inside `crewai`/`litellm` against ~70 |
| **Thread sprawl, quantified (new)** | **MEASURED** | `/proc/1/status` on the idle gateway: **114 OS threads** — 73 `uvicorn` (the Python threads `faulthandler` shows), **36 `tokio-rt-worker`**, 3 `sqlx-sqlite-wor` |
| The `training-capture` thread-per-LLM-call is the main sprawl contributor | **INFERRED** | it is the only *unbounded* source, but the measurement above shows **a third of all threads are Rust runtimes invisible to `faulthandler`** and unaccounted for by any Python-side analysis. Measure per-source before acting. |

### Unresolved

| question | grade |
|---|---|
| Why `searxng:no_results` was logged while searxng demonstrably works | **UNKNOWN** — `_search_searxng` swallows its exception at `logger.debug`, so no reason is recorded. Add the logged reason before diagnosing again. |
| Whether an ungrounded report passed the evidence gate or was rewritten downstream | **UNKNOWN** — `gs_report_forest` went through `deep_research` yet delivered ungrounded prose; no column records the gate verdict |
| Whether the closed-citation-set prompt licenses labelled-ungrounded reports | **INFERRED** — plausible reading of the instruction; not tested |

### The one thing to take from this

Every claim graded PROBED has a repeatable command or test behind it. Everything
graded INFERRED is a story that fits the evidence — which is exactly what the two
retracted claims also were. **Do not build on an INFERRED row without probing it
first.** That is the single lesson of this whole effort.

---

## Addendum 11 — the routing coin flip, measured and closed (2026-07-26)

Handoff item 1. The mechanism is now **probed**, and it was cheaper to probe than
to re-run the golden set: the fork-selection logic is a deterministic scorer, so
sweeping its one non-reproducible input settles the question offline, for free.

### The mechanism

`app/research/deep_path.py:assess_deep_research` is not an LLM call. It sums
keyword-shape points and the router's `difficulty` integer, and compares the sum
to `deep_research_min_score` (default 4). Only decisions whose `crew` is
`research` are considered; whichever way that comparison lands decides between:

| fork | grounding machinery |
|---|---|
| `deep_research` | evidence gate, untraced-citation check, critique |
| `research` | **none** — `app/crews/research_crew.py` contains no evidence, citation or gate reference at all (verified by grep, 569 lines) |

**A bare report request scored 2 against a threshold of 4.** So "make me a report
on X" reached the gated path only if the router happened to guess difficulty ≥ 8.

### The measurements

**Scorer sweep** (`evals/probe_route_sensitivity.py`, no LLM, no spend):
**4 of 12** golden questions changed fork on `difficulty` alone —
`gs_report_industry` and `gs_multi_and` (deep only at d≥7),
`gs_report_no_evaluate` and `gs_ambiguous_short_report` (deep only at d≥8).
All four are report/analysis class, i.e. exactly the class where grounding matters.

**Production data** (`control_plane.tickets`, post-promotion — `assign_to_crew`
runs at orchestrator.py:3392, after promotion at :3321, so the column records the
fork actually used):

| question | observed difficulty → crew |
|---|---|
| dairy report | 5 → `research`; 7 → `deep_research`; 8 → `deep_research` |
| forests report | 5 → `research`; 8 → `deep_research` |
| Tallinn housing | 5 → `research` (twice) |
| poem | 2 → `commander`; 8 → `creative` |

The forest request, **byte-identical at 183 chars across seven runs**, was scored
5, 7 and 8. The poem row confirms a second threshold of the same shape:
`maybe_promote_to_creative` fires on `difficulty >= 6`.

**One row does not fit** and is not explained: 07-24 14:39, difficulty 7, went
`deep_research` while the then-live scorer (`_REPORT_SHAPE` was added by
`e6fea929` at 07-24 17:00 — verified by re-running the pre-commit scorer) puts
that text at 3/4. The likeliest reading is that the container held the change
before it was committed, which cannot be checked now. Recorded rather than
explained away.

### The fix

A **shape floor**, `requires_grounded_synthesis(text)`: explicit-depth, review
shape, report shape, or analytical-comparison-across-subjects makes retrieved
evidence mandatory *regardless of score*. Grounding therefore depends only on
request text, which is reproducible.

Deliberately not a threshold tweak. The scorer conflated two different questions
— "must this be grounded?" and "how deep should it go?" — and only the first
should gate safety machinery. The score route survives untouched, so difficulty
can still *add* grounding, never withhold it.

The residual is kept visible instead of being claimed away:
`DeepResearchAssessment.deterministic` is False when no difficulty in 1..10 would
have changed the verdict, and `promote_research_decisions` logs a WARNING when it
fires. Before this, "which safety machinery ran" varying between identical runs
was recorded nowhere.

**Result:** all 12 golden questions are difficulty-invariant — 6 always gated, 6
never. The fast questions stay fast (population lookup, poem, chat, calendar,
coding, dossier all remain off the deep path, pinned by test).

### The cost, measured

Report-class tickets, last 4 days: `deep_research` avg **758 s**, `research` avg
**104 s**. So reports get ~7× slower and some will now return an honest "could
not ground this" instead of fluent ungrounded prose — `gs_report_forest` and
`gs_report_industry` already fail that way on the gated path. Operator accepted
this trade, and chose contract-based gating of the fast path as the next step.
Recorded `cost_usd` was ~equal on both forks, but cost attribution is broken
globally (see Bonus above), so no cost claim is made.

### Also closed

**`searxng:no_results` (was UNKNOWN).** The label was inferred from an empty
return value, and every backend returns `[]` both for "matched nothing" and for
"raised". The reason is now recorded per backend by `_record_backend_error` and
rendered by `_chain_label`, so the chain reads either `searxng:no_results` or
`searxng:error(TimeoutError: …)`. A direct probe found SearXNG healthy
(5 results for a natural query), so the historical line was not a persistent
misconfiguration; it cannot be diagnosed retrospectively because no reason was
ever stored.

**`9e9112e7` regression status (was unverified).** Verified: identical failure
sets across `9e9112e7^`, `36747966`, and this tree — 77 failure lines each over a
1,783-test selection. The 51 failures are pre-existing. Baselines are on disk at
`.test-baselines/` (gitignored, outside `/tmp`), reproducible via the new
`docker-compose.test.yml`.

### Claims ledger for this addendum

| claim | grade | evidence |
|---|---|---|
| Fork selection is a deterministic scorer + the router's difficulty integer | PROBED | code read; scorer executed over the golden set at every difficulty |
| `research` has no grounding gate | PROBED | grep over all 569 lines |
| 4/12 golden questions flipped fork on difficulty alone | MEASURED | scorer sweep, output in the probe |
| Identical 183-char text scored 5, 7 and 8 | MEASURED | 7 `tickets` rows, full titles compared |
| Same text reached both forks in production | MEASURED | `assigned_crew` across those rows |
| Ticket `assigned_crew` is post-promotion | PROBED | call order in orchestrator.py (:3321 then :3392) |
| `_REPORT_SHAPE` explains the 07-24 morning `research` row | PROBED | pre-commit scorer re-executed: 3/4 at d=7 |
| The 07-24 14:39 row | **UNKNOWN** | does not fit the scorer-version model; not explained |
| Shape floor makes all 12 difficulty-invariant | PROBED | sweep re-run post-fix; 24 tests, 5 of which fail with the floor disabled |
| deep 758 s vs research 104 s for report-class | MEASURED | `tickets`, 4-day window, n=15 / n=5 |
| SearXNG healthy now | PROBED | live call returned 5 results |
| Why `searxng:no_results` was logged historically | **UNKNOWN** | no reason was ever recorded; instrumented so the next occurrence is diagnosable |
| No regressions from this change | MEASURED | identical 77-line failure sets, +31 passes |

### Deliberately not done

The ONE_PATH_DESIGN §5 golden-set reproducibility re-run was **skipped by
operator decision**: it would confirm the consequence, not the mechanism, at 12+
dispatches and the wedge risk that materialised twice on 07-24. The mechanism is
established from production data plus an offline sweep.

Still open, unchanged: thread sprawl (36 unattributed `tokio-rt-worker`),
content-starvation caps, the `TaskOutput` validation leak, whether an ungrounded
report ever passed the evidence gate, and the eval Phase 2 full-reply gate.

---

## Addendum 12 — the content-starvation item was wrong about the mechanism (2026-07-26)

The deferred list carried "the 4096-token writer ceiling" and "`max_tokens`
truncation still cuts drafts mid-sentence" as causes of thin reports. Addendum 10
graded the underlying claim **INFERRED**. Measuring it first changed the answer.

### Measured

`workspace/llm_benchmarks.db:token_usage`, **62,675 calls over 14 days**, counting
completions pinned exactly at a known cap:

| cap | exact hits | where it comes from |
|---|---|---|
| 512 | **85** | `goodhart_guard`, `discover-topics`, local prediction/compression |
| 1024 | 6 | the router |
| 4096 | 2 | — |
| 2500 / 3000 / 3500 | **0** | `_focused_completion` default / draft / critique |

**The research draft and critique caps are never reached.** The 512 pin that *is*
being hit 85 times belongs to internal machinery (`goodhart-check` on Sonnet,
`discover-topics` on local qwen), not to any user-facing answer.

Coverage was verified before drawing that conclusion: rows do exist inside a known
`deep_research` window (2026-07-25T17:00–17:35). A first attempt found "zero rows"
there and nearly became a fourth hasty claim — the ledger stores ISO `T`
timestamps and the query used a space separator. The absence was in the query.

### So the likelier mechanism is a character clamp, and those recorded nothing

Two clamps sit directly on the report path in `app/research/run.py`:

* `investigation[:4000]` → the draft prompt
* `draft[:8000]` → the critique prompt

The second matters most: the evidence gate inspects `HINT_CRITIQUE` in preference
to `HINT_DRAFT`, so anything clipped at that hop is invisible to the gate —
including a trailing source list. That produces the observed symptom with no
`max_tokens` truncation at all, which is exactly what the ledger shows.

A character clamp can never appear in a token ledger, and neither clamp logged
anything. So this stays a **hypothesis**, not a finding.

### Shipped: recording, not a cap change

* `app/content_clamp.py` — `clamp(text, limit, what=...)` replaces `text[:n]` at
  both report-path sites, logging the overflow with the hop name and keeping
  per-hop counters (`times_clamped`, `chars_dropped`, `largest_drop`).
* `_focused_completion` now logs when `finish_reason == "length"` — the one
  truncation signal a cost ledger structurally cannot provide, since a capped
  completion and a naturally-ending one have identical token counts. Discarding it
  is why the claim stayed untested for two days.

Deliberately **no limit was raised**. Whether 4000/8000/3000 are too tight is a
question the next real report now answers with numbers, per
`feedback_verify_before_recommending`. Raising them blind would repeat the pattern
that produced the reverted relevance filter.

### Claims ledger

| claim | grade | evidence |
|---|---|---|
| Research caps 2500/3000/3500 are never hit | MEASURED | 0 exact hits in 62,675 calls / 14 days |
| Something truncates at 512, 85 times | MEASURED | same query; roles and models identified |
| Those 512 hits are internal, not the answer path | OBSERVED | roles are `goodhart-check` / `discover-topics` / null-on-local |
| The ledger covers the deep-research window | PROBED | rows listed for 2026-07-25T17:00–17:35 after fixing the timestamp format |
| `draft->critique` clipping can hide a source list from the gate | **INFERRED** | the gate prefers `HINT_CRITIQUE` (code read) and the clamp precedes it, but no instance is recorded. Instrumented; do not quote as established. |
| Clamps and `finish_reason` were recorded nowhere before | PROBED | code read; 11 tests pin the new recording |
| No regressions | MEASURED | identical failure set vs `36747966`; the one extra row (`test_overspend_clamps_headroom_to_zero`) was newly *selected* by widening `-k` with "clamp" and fails on pristine HEAD too — verified in a worktree |

---

## Addendum 13 — the fast fork gets an evidence set (2026-07-28)

The operator-chosen next step from Addendum 11: contract-based gating of the
fast path — "give `ResearchCrew` an evidence set so it can be checked without
forcing the slow fork". This is the first increment: **capture everywhere,
check the `research` fork, observe before enforcing.**

### What exists now

* **`app/evidence_capture.py`** — a per-request, thread-safe, bounded recorder
  attached around every crew dispatch (`orchestrator._run_crew_inner`).
  `search_brave` (all three backends), `web_fetch` and the firecrawl
  scrape/extract/search tools report every URL they return. Both structured
  result fields and URLs *inside* returned content count — the research
  prompt's own rule is "every URL you cite must be one a tool actually
  returned", and a URL in a fetched page was. `ResearchCrew._run_parallel`
  re-attaches the parent's recorder inside its pool threads (ContextVars do
  not propagate there on their own).
* **`app/crews/grounding.py`** — the fast-fork counterpart of the deep gate's
  untraced-citation check. Coverage semantics deliberately mirror
  `deep_path._deep_evidence_gate_for` (cited token equals or is a substring of
  a returned identifier, so a retrieved deep link covers its own domain), plus
  trailing-slash normalisation on both sides. URLs present in the task input
  are allowed — citing what you were handed is not fabrication.
* **Wiring**: check runs after `output_integrity`, and never overwrites a
  leakage cause with a grounding cause.

### Mode: observe by default, and why

`FAST_PATH_GROUNDING` = `off` | `observe` (default) | `enforce`. In observe
mode an untraced citation is logged (with per-origin evidence counts) and
counted, and the reply is delivered unchanged. The reverted relevance filter
(Addendum 5) is the reason: it shipped against fixtures whose input shape
production never passes. This checker has never seen a real fast-fork reply,
so it measures first; the flip to enforce is a decision to make on those
logs. In enforce mode a violation becomes the typed no-answer signal
(`outcome.record_no_answer`), so the orchestrator reports the real cause
instead of delivering fabricated citations.

### Deliberate narrowings

* **URLs only.** The recorder cannot see a DOI inside a fetched PDF; checking
  DOI/arXiv citations here would flag legitimately-sourced identifiers. Those
  remain the deep gate's job, against its structured evidence set.
* **`research` crew only.** `deep_research` has its own stricter gate;
  other crews' citation semantics are unmeasured.
* **Enforcement requires a non-empty captured evidence set.** Un-hooked tools
  (memory, KB search, composio, wiki, youtube) can legitimately source a
  citation; with nothing captured, fabrication and unrecorded provenance are
  indistinguishable, and that ambiguity must not destroy a real answer.
  Un-hooked coverage shows up in observe logs as untraced citations with a
  named origin breakdown — widening the hooks is a data-driven follow-up.

### One bug the tests caught before it shipped

`EvidenceRecorder` defines `__len__`, so an **empty recorder is falsy** —
`recorder or EvidenceRecorder()` re-attached worker threads to a *fresh*
recorder and silently discarded everything a sub-agent recorded. Only the
thread-propagation test caught it; single-threaded fixtures cannot see this
failure. Fixed with `is not None`.

### Claims ledger for this addendum

| claim | grade | evidence |
|---|---|---|
| Capture records what the hooked tools return, across threads | PROBED | 26 tests incl. a pool-thread propagation test and a behavioral `_run_parallel` test that fails if the re-attach wiring is removed |
| The check flags the incident shape (padded org-homepage bibliography) | PROBED | test fixture is the Addendum-3 shape (`piimaliit.ee`, `ec.europa.eu/eurostat` cited, never retrieved) |
| Covered-domain / trailing-slash / input-supplied URLs are not flagged | PROBED | dedicated false-positive tests |
| Observe mode cannot suppress a reply | PROBED | test asserts no no-answer signal is recorded |
| No regressions | MEASURED | identical 92-line failure sets (91 F + 1 E, pre-existing) on pristine `a92f0fcf` and this tree over a 2,303-test selection (Addendum-3 `-k` widened with research/web_search/web_fetch/firecrawl/integrity/grounding/evidence/clamp); +26 passes |
| How real fast-fork replies cite, and how often citations go untraced | **UNKNOWN** | that is precisely what observe mode measures. **Do not flip to `enforce` until the observe logs have been read.** |

---

## Addendum 14 — the 36 `tokio-rt-worker` threads are chromadb's Rust core (2026-07-28)

Addendum 10 flagged a third of the gateway's threads as "Rust runtimes
invisible to `faulthandler` and unaccounted for by any Python-side analysis",
and made attribution a precondition for touching any concurrency ceiling.
Attributed by experiment, in the deployed container:

* `import chromadb` → +15 threads, all named `python` (telemetry/BLAS pools) —
  **not** the Rust workers.
* `chromadb.PersistentClient(path=/tmp/…)` → **+5 `tokio-rt-worker`,
  +2 `sqlx-sqlite-wor`** — the exact names in the gateway census. (Throwaway
  path; the live store is single-writer and was not touched.)
* one collection write → tokio 5→9 (lazy growth), sqlx stays 2, +5 `python`
  (the onnx embedding pool).

Live census at the post-deploy boot: **133 threads = 75 `uvicorn` +
36 `tokio-rt-worker` + 22 `sqlx-sqlite-wor`**. sqlx workers are 2 per client →
~11 clients, and the workspace holds exactly **11 chroma stores** (7 KBs +
a test snapshot + three May drill-scratch dirs); clients are path-cached
(`get_client_for_path`), so this thread population scales with the number of
distinct stores opened, **not with request load**. `sqlx-sqlite-wor` grew
3 (07-25) → 22 (07-28), consistent with more stores having been opened in this
boot, bounded by the number of distinct paths — worth a glance only if it
keeps climbing past the store count (dated drill/test paths do add new cache
entries).

Implication for the deferred concurrency item: these ~58 Rust threads are I/O
runtime workers that should not hold the GIL, so the GIL/CPU-starvation stall
analysis concerns the 75 Python (`uvicorn`) threads, not the headline 133.

| claim | grade | evidence |
|---|---|---|
| `tokio-rt-worker` + `sqlx-sqlite-wor` come from chromadb client construction | PROBED | direct causation in a fresh process; thread names match the census exactly |
| 22 sqlx ≈ 11 clients ↔ 11 stores on disk | MEASURED + OBSERVED | arithmetic matches; which stores are open in-process is not directly verifiable from outside |
| per-client tokio worker count | MEASURED in probe only | 5 at construction, 9 after one write; live 36 across ~11 clients averages ~3.3 — sizing varies, not pinned |
| Rust workers don't participate in GIL starvation | **INFERRED** | standard extension-runtime behaviour; not directly measured here |
