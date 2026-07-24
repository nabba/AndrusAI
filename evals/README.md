# Answering-pipeline golden set

Phase 1 groundwork from [`docs/ANSWERING_V2_PLAN.md`](../docs/ANSWERING_V2_PLAN.md).
A small set of real-shaped questions + a runner to score delivery rate and
latency through the live dispatch path, so future changes to the answering
pipeline (Phase 2's lead-agent conductor, Phase 3's deep-work engine) can be
compared against a recorded baseline instead of asserted.

- `golden_set.jsonl` — ~12 questions across report / research / writing /
  coding / chat categories, including the exact 2026-07-24 incident question
  (`gs_report_forest`) and several report-shaped variants to check the
  Phase 0 regex fix generalizes.
- `run_eval.py` — sends each question through `POST /api/cp/chat/send`
  (the same `Commander().handle()` path Signal uses), scores whether a
  structural-failure apology string was returned vs a real answer, and
  records latency.

## Running it

**This spends real LLM budget and writes real `control_plane.crew_tasks` /
`audit.log` / ticket rows on whatever gateway you point it at.** Always pass
`--sender` to isolate the eval conversation from the real operator's Signal
thread, and only run it against the live gateway with that cost in mind —
this script does not auto-run as part of tests or CI.

```bash
# Record a baseline (run once, after a fix you want to measure)
python evals/run_eval.py --sender eval-harness --out evals/results/baseline.json

# After a later change, run again and diff
python evals/run_eval.py --sender eval-harness --out evals/results/after_change.json
python evals/run_eval.py --diff evals/results/baseline.json evals/results/after_change.json

# Just the report-class questions, for a quick check
python evals/run_eval.py --only gs_report_forest gs_report_industry gs_report_no_evaluate
```

`evals/results/*.json` is gitignored-by-convention (raw run output, not a
curated artifact) — commit a summary in the relevant PROGRAM.md/docs entry
instead of the raw JSON if a result is worth recording permanently.

## Status

Harness built 2026-07-24; **no baseline has been recorded yet** — running it
for real is a deliberate operator decision given the cost + live-data
implications above, not something to fire automatically. Phase 1's gate
(`docs/ANSWERING_V2_PLAN.md` §4) is: record a baseline now, then require the
Phase 2 conductor to match or beat it in shadow mode before flipping
`answering_v2_enabled`.
