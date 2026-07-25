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

The 15:43 wedge's stall dump names a **different** blocking vector than the psycopg2 pool contention seen at 17:16:

```
config.py:269 in mem0_postgres_url
training_collector.py:174 in _store_to_postgres
training_collector.py:161 in _store_record
rate_throttle.py:495 in _store
```

That is a synchronous Postgres write in **every LLM call's success callback**. Caching the benchmark-score query in `create_specialist_llm` is necessary but **not sufficient** — this path must be audited too, and it gets hotter during a retry storm.

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
