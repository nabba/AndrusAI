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
