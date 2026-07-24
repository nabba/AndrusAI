# Answering Pipeline v2 — Simplification Plan

**Date:** 2026-07-24 · **Status:** PROPOSED (no code changed)
**Companion:** `reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md` (the incident evidence this plan generalizes from)

---

## 0. The verdict

**Yes — the crew system should be simplified, and the simplification is architectural, not cosmetic.**

The answer-quality ceiling is not any single bug. It is the *topology*: an upfront router LLM statically decomposes every request into fixed role-crews that run as independent silos, connected by truncating merge steps, wrapped in chat-latency timeouts, delivered through a synchronous pipe. Every defect in the diagnosis is a symptom of that shape. Patching the shape (fix #1–#5) is worth doing now, but the ideal setup replaces it.

The equally important half of the verdict: **the problem is the serving topology, not the platform.** The memory stack, governance/safety invariants, budgets, Signal resilience, healing monitors, and the paper-factory research spine are assets. They stay. What goes is the layer between "message arrives" and "answer leaves": router → crews → parallel_runner → merge → vetting → concierge.

Scale of what's being replaced: 17 crew modules under `app/crews/`, a 4,216-line `orchestrator.py`, 1,031-line `routing.py`, and **five overlapping research implementations** (`research_crew`, `deep_research_crew`, `delegated_research`, `tools/research_orchestrator`, `app/research/` spine — six counting `dossier_crew`).

---

## 1. Why the crew topology caps quality (generalizing the diagnosis)

1. **Planning without data.** The router LLM (max_tokens=1024) must decompose the task *before any evidence exists* — it can't know that writing depends on research it hasn't seen. Best practice inverted: plans should be drafted by the agent doing the work and revised as evidence arrives.
2. **Static roles, no data flow.** Crews are personas with fixed prompts ("researcher", "writer") rather than steps in a dataflow. Parallel crews share nothing; sequential dependency doesn't exist. The writer answering a research question from model weights is not a bug in a prompt — it's the only thing that topology *can* do.
3. **Message-passing with truncation as the only transport.** Results move between stages as strings clipped at every hop (`[:6000]` merge, `[:6000]` vetting, `[:4000]` debate, 4096-token writer). Long-form output is structurally impossible.
4. **Chat-scoped execution.** Work lives and dies inside one request handler (120 s multi-crew cap; a gateway restart kills in-flight crews; finished-late = discarded). Long work needs a *durable job*, not a long request.
5. **Accumulated inline gates.** Vetting, critic, concierge, epistemic gate, ToM swap, GWT hooks all sit in the hot path, each adding latency and each able to rewrite/shrink output. Quality controls should observe or verify artifacts — not re-transform them inline.
6. **Framework opacity.** CrewAI supplies the ReAct loop, advisory-only `max_execution_time`, delegation quirks, event-pairing warnings — and pins that block Python 3.14 and chromadb 2.x. The parts of CrewAI actually used (a tool loop + role prompt) are ~200 lines of code the repo effectively already owns elsewhere.

---

## 2. The ideal setup — 2026 best practices, applied to this system

Design principles (each maps to a known-good pattern):

- **Agent = model + tools + loop.** One strong lead agent decides per turn: answer directly, call a tool, or open a job. No upfront router LLM. *(Anthropic "Building effective agents": use the simplest pattern; agents for open-ended work, workflows for predictable work.)*
- **Two lanes, explicit contract.**
  - *Interactive lane*: seconds → ~2 min, synchronous reply. Quick answers, lookups, small tool use.
  - *Job lane*: minutes → hours, **durable, resumable, observable**. The agent acknowledges immediately with an honest ETA, sends progress, and delivery is triggered by job completion — never by a request-scoped timer. Late is a non-concept: whenever the job finishes, the artifact ships.
- **Orchestrator–worker only where breadth pays.** Deep research = a lead agent that plans in a scratchpad, fans out *ephemeral, task-scoped search subagents* in parallel (context isolation — each burns its own window and returns distilled findings), then a **single writer pass** for coherence. Sequential where data flows; parallel only for gathering. *(Anthropic's multi-agent research system pattern.)*
- **Filesystem as shared memory.** Subagents write findings to `workspace/jobs/<id>/notes/*.md`; the writer reads files. Artifacts (report.md / .pdf) are the deliverable; Signal gets a summary + attachment. This deletes the entire truncating-merge class.
- **Context is pulled, not pushed.** Replace the 12-loader `ctx_load` push (6 s of RAG stuffing per crew, relevant or not) with retrieval *tools* (`search_memory`, `search_kb`, `recall_conversation`) the agent calls when it needs them. *(Context-engineering: just-in-time retrieval beats prompt stuffing.)*
- **Verify artifacts, don't rewrite streams.** Job lane: run `citation_verifier` (already built) + a critic subagent over the **full** artifact; findings go back to the lead agent to fix. Retire the 6000-char vetting rewrite from this lane; keep cheap schema/refusal checks for interactive replies.
- **Task-class model policy, strongest-model-where-it-matters.** Lead agent & final writer on premium (Sonnet/Opus-class); extraction/summarization sub-steps on cheap models. Replace the difficulty-floor table, promotion pipeline and 360-entry catalog machinery with a small curated model config + health checks (that machinery is what produced the stale-haiku bug).
- **Budget as a job envelope.** Each job gets a dollar budget up front (e.g. report ≈ $2), spends visibly, reports actual cost. Replaces per-call adaptive throttling as the primary control.
- **Deterministic workflows stay workflows.** `company_dossier` and the paper factory are *typed pipelines* — correct as-is. They become tools/skills the agent invokes, not crews the router guesses at.
- **Evals before belief.** A golden set of ~20 real asks (including the forest report) scored on delivered/not, groundedness, completeness, latency, cost. Every phase gates on it; new path runs in shadow before flipping. *(Also per operator rule: measure magnitude before claiming wins.)*

### The forest-report ask, replayed in v2

1. Message arrives → lead agent (premium model, conversation context) recognizes report intent → creates **job** `jobs/2026-07-24-estonia-forest`, replies in ~5 s: *"This needs real research — I'll send the report here in ~15 min."*
2. Job runner (survives restarts; checkpointed): lead plans subtopics in a scratchpad → spawns 3–4 parallel search subagents (forest-cover statistics; logging industry economics; policy/criticism; health/biodiversity data). Each searches + fetches *full pages*, writes `notes/<topic>.md` with citations, returns a 1-paragraph digest.
3. Lead reviews notes, spawns one follow-up subagent for gaps, then a **single writer pass** (premium, high max_tokens) over the notes files → `report.md`.
4. `citation_verifier` + critic subagent over the full report → lead fixes findings → final artifact.
5. Delivery: Signal message with executive summary + `report.md`/PDF attachment; job page on `/cp` shows the timeline, cost ($~1.5), and sources. If the operator had asked a follow-up mid-run, the interactive lane answers with job status.

Everything in steps 2–4 already half-exists in `app/research/` (literature → verify → compose → LaTeX). v2 makes it *the* engine instead of an unreachable side path.

---

## 3. Keep / consolidate / retire

**Keep (untouched or lightly adapted)**
- Signal/Discord delivery, durable outbox, host watchdog, deploy poller
- Memory stack (Mem0/pgvector/Neo4j/ChromaDB + source ledgers) — re-exposed as retrieval tools
- Governance: TIER_IMMUTABLE, change requests, Tier-3 protocol, external action gate, vacation mode
- Cost ledger, budgets, `/cp` dashboards (gains a Jobs page)
- Healing monitors / resilience drills (infra-facing)
- `app/research/` paper-factory spine — **promoted to the deep-research job engine**
- `company_dossier` — becomes an agent-invokable workflow/skill
- Tool implementations (`app/tools/*`) — portable; the BaseTool wrapper is thin

**Consolidate**
- 5 research paths → 1 job engine (the spine) with a breadth-subagent stage
- Utility crews (pim, desktop, devops, financial, media, repo_analysis, tech_radar) → tool bundles / skills the lead agent calls; registry names kept as aliases during migration
- Vetting/critic/epistemic gates → one artifact-verification step (job lane) + one cheap schema check (interactive lane); epistemic claim recording stays as an observer

**Retire from the serving path**
- Router-LLM crew splitting + `run_parallel` head-of-line dispatch for user asks
- `research_crew` / `writing_crew` / `deep_research_crew` / `delegated_*` role-crews
- Merge/synthesis truncation stage; reflexion 3× rerun loop; concierge rewrite (optional for short chat only)
- Meta-agent recipe selection, Theory-of-Mind swap, GWT broadcasts *inline in serving* (move to observational subscribers)
- Difficulty-floor tier table + promotion/rehydrate catalog machinery (job-lane policy: task class → model)
- CrewAI itself (Phase 4, strangler) — unblocks Python 3.14 + chromadb 2.x

---

## 4. Migration plan (strangler, flag-gated, eval-gated)

**Phase 0 — Stop the bleeding (1–2 days). SHIPPED + DEPLOYED + LIVE-VERIFIED 2026-07-24** (commits `e6fea929`/`07a2d6be`/`6368ddc8` on `main`). Delivered as: `adaptive_parallel_timeout()` sized to crew class (subsumes "deliver orphaned results" — crews now finish inside the window instead of being discarded); `_REPORT_SHAPE` regex + `drop_writing_after_deep_research()` (report intent → single deep_research decision, redundant blind writer removed); `thread_pool_size`/`ollama_max_concurrent_crews` widened 6→16 / 2→8 (the semaphore self-starvation fix, via config rather than a reentrant rewrite); `llm_rehydrate.py` provider validation + the 4 stale `provider="anthropic"` promoted rows retired live in Postgres + the bad remap cleared. 16 tests, zero regressions (confirmed against an unmodified-`origin/main` baseline run). Gate met: the exact incident phrasing now scores 5/4 and promotes to `deep_research` with reason `explicit report request`, verified live post-deploy. Full account in `reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md`'s addendum.

**Phase 1 — Job lane + evals (~1 week). Groundwork shipped 2026-07-24; job-lane design REVISED after investigation (see below).** Eval harness shipped: `crewai-team/evals/` — 12-question golden set (incl. the incident question + report-shape variants) + `run_eval.py` driving `POST /api/cp/chat/send` (same `Commander().handle()` path Signal uses), scoring delivery via the actual apology-string markers + latency. Deliberately not yet run for real (spends live LLM budget + writes real audit/ticket data) — recording a baseline is an explicit next operator decision, not automatic.

*Job-lane design — investigation findings (2026-07-24):* the original plan assumed reusing `autonomous_executor`'s `ExecutorRun` was mostly composition. A full investigation of `app/autonomous_executor/` found the **data/audit/budget model is a good fit, but its scheduling model is not**, for this specific use case:
- Runs only advance via the idle scheduler (`idle_scheduler.py`), one step per HEAVY-job tick, round-robined against ~20 other HEAVY jobs, with a hard-coded 60s minimum per-job cadence.
- **Any Signal chat message — even unrelated to the job — resets a 180s idle-settle window and blocks ALL idle work, including the executor tick, for that long.** For an assistant used conversationally through an active day, a durable report job could sit un-advanced for a long, unpredictable stretch — plausibly *worse* than today's bounded (if synchronous) wait.
- **No completion-notification hook exists** for `ExecutorRun` outside the `BLOCKED`-escalation case; `driver._finalise()` doesn't call `app/notify` on `COMPLETED`/`FAILED`. Would need to add one, gated on `requestor` prefix so autonomous background runs (interest-goal-seeded, self-improvement) don't also start pinging Signal.
- `autonomous_executor_enabled` is default OFF and, per the code's own incident comments, has **essentially never been exercised for interactive (as opposed to auto-emitted, approval-gated) use in production** — its one confirmed live run required fixing a broken import that had silently no-op'd every scheduler-advanced Commander dispatch.
- The good news: the *synchronous* `execute_deep_research` path Phase 0's routing fix now correctly sends report-class asks into is not a dead end — it already shares the exact same `build_research_run`/`make_research_adapter` machinery as the async `/delegate research` path, already has a generous internal 2400s (40 min) budget, and is already wrapped by `main.py`'s existing progressive soft(900s)/hard(2700s) timeout with real progress-based extension and "still working on this" messaging. Phase 0 alone likely closes most of the practical gap for now.

*Revised Phase 1 scope:* don't wire plain-chat reports into `ExecutorRun`'s idle-scheduler model as-is — it would trade a bounded synchronous wait for an unbounded, unpredictable one. Two options, needing an explicit decision before building: **(a)** stay on the synchronous `execute_deep_research` path (already reasonably durable-feeling thanks to `main.py`'s extension logic) and treat "durable across restarts" as a smaller, later add — Phase 1's real remaining work becomes recording the eval baseline + a completion-notify hook for the rare case a request DOES get killed by the hard 2700s ceiling; **(b)** give the executor tick its own dedicated cadence independent of the global `is_idle()` gate (a real scheduling fix, not reuse) if true restart-durability is wanted for report-class work specifically. *Gate (unchanged): 100% delivery on job-class asks; baseline recorded — now understood to require resolving the scheduling question above, not just plumbing.*

**Phase 2 — Conductor agent for the interactive lane (~1–2 weeks).** Single premium-model agent loop replaces router+crews for plain messages: tool belt = web_search + web_fetch (full-page), retrieval tools, files, `open_job`, existing utility tools. Slash commands and existing fast-paths stay. Run in **shadow** (both paths execute, new one logged) → compare on evals → flip default behind `answering_v2_enabled`; old path remains one flag away. *Gate: eval parity on interactive asks, win on tool-use asks; operator acceptance.*

**Phase 3 — Deep-work engine (~2–4 weeks).** Orchestrator–worker on the `app/research` spine: parallel search subagents writing to the job workspace, single writer over the notes corpus, citation verification + critic on the full artifact, per-job budget envelope. Consolidate/retire the four redundant research paths. Convert utility crews to tool bundles as they come up. *Gate: report-class evals beat the old system on completeness + groundedness at ≤ latency; zero discarded-work incidents.*

**Phase 4 — Decommission (ongoing).** Remove CrewAI from the serving path (thin in-house loop: `llm_factory` + tool registry + a ~200-line ReAct executor, or the Claude Agent SDK pattern); prune `app/crews/` to the workflow survivors; simplify model selection to the curated config; move consciousness/meta hooks fully to observer subscriptions; dead-code sweep (the 2026-06-01 audit list is the starting point). *Gate: dead-code audit clean; Python 3.14 / chromadb-2 unblocked; orchestrator.py < 1,000 lines.*

---

## 5. Risks & counterweights

- **Regression risk** → shadow mode + golden-set evals + per-phase flags; old path deletable only after 2 clean weeks.
- **"Multi-agent is good, why remove it?"** → v2 keeps multi-agent where it demonstrably pays (parallel search breadth, adversarial verification) and removes it where it demonstrably hurts (static role silos, blind writers). Fewer agents, better cast.
- **Premium-model cost** → bounded by per-job envelopes and visible per-job reporting; today's waste is worse (two discarded runs cost $1.35 and delivered nothing; the 402-storm fallbacks burned tokens on a model that can't call tools).
- **Utility-crew long tail** (pim/desktop/financial…) → alias-preserving conversion, one at a time, lowest-traffic last.
- **Operator bandwidth** → each phase independently shippable and valuable; Phase 0 alone fixes the user-visible failure.

## 6. Success metrics (measured, not asserted)

1. **Delivery rate** on job-class asks: 100% (was 0% in the two incidents).
2. **Groundedness**: % of report claims with verified citations (citation_verifier pass rate).
3. **Completeness**: golden-set rubric score vs old baseline.
4. **Latency honesty**: |promised ETA − actual| ; progress ping present.
5. **Cost per deliverable**: ≤ old path *including* its discarded work.
6. **Serving-path size**: crews in hot path 17 → ~3 workflows; orchestrator.py 4,216 → <1,000 lines; research implementations 5 → 1.
