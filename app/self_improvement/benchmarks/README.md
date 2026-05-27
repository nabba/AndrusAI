# Held-out self-improvement benchmark (TIER_IMMUTABLE)

These files are the **held-out exam** the verified mutation engine uses to decide
whether a self-modification is a real *quality* improvement (see
`app/self_improvement/worktree_eval.py`).

**This directory is TIER_IMMUTABLE.** The Self-Improver must never be able to add,
remove, or edit benchmark tasks — otherwise it could pad its own exam with trivial
tasks to manufacture a passing grade (a Goodhart failure). Only the operator
curates these, via a normal (human-reviewed) commit.

## How it's used

For a change to a target file, `worktree_eval.load_benchmark(target_file)` collects
every task whose `target_prefixes` matches. Each task's `input` is run through the
**real entry point** in BOTH the baseline worktree and the candidate worktree, and
the paired outputs are scored 0–1 by a DGM-separated LLM judge. A change is
`IMPROVED` on the quality axis only when the candidate beats baseline by at least
the immutable effect size **and** wins on more tasks than it loses (so within-noise
deltas like the old engine's `+0.0133` can never read as improvement).

When no benchmark targets a file (the common case today), the change is judged on
correctness alone and routes to an `INVARIANTS_ONLY` verdict — correctness proven,
quality unmeasured — which the operator gate then decides on.

## Schema (`*.json`)

```json
{
  "target_prefixes": ["app/crews/research_crew.py"],
  "tasks": [
    {
      "id": "research-finland-forest-1",
      "input": "How many hectares of forest per capita does Finland have? Cite the source.",
      "rubric": "States a specific per-capita figure with a real, fetched source URL. Penalize fabricated stats or invented URLs."
    }
  ]
}
```

- `target_prefixes` — a task applies when the changed file path starts with any prefix.
- `tasks[].id` — stable unique id (used in evidence + telemetry).
- `tasks[].input` — the prompt sent through the real entry point.
- `tasks[].rubric` — what a good output looks like; handed to the judge verbatim.

Keep tasks small in number and deterministic in intent; each one costs a real
entry-point run × 2 (baseline + candidate) × the judge, inside the per-cycle budget
(`evolution_verified_per_cycle_budget_usd`).
