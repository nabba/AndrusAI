"""Benchmark suite for cross-model evaluation (Phase C.3, 2026-05-22).

Public surface
──────────────

  * :class:`BenchmarkTask` / :class:`BenchmarkRun` / :class:`LLMResult`
    — the data model
  * :func:`load_tasks` / :func:`get_task` / :func:`catalog_stats`
    — read the YAML catalog
  * :func:`score` + :data:`SCORER_REGISTRY` — pure-function scorers
  * :func:`run_task` / :func:`run_catalog` — the runner
  * :func:`append_run` / :func:`iter_runs` / :func:`stats` — JSONL store
  * :func:`leaderboard` / :func:`per_model` / :func:`per_task` /
    :func:`filter_runs` — aggregator
  * :func:`run_refresh` — idle-scheduler entry point

Design intent
─────────────

  * **Observational + advisory** — never blocks any decision in the
    rest of the system. The leaderboard informs operators about
    cross-model strengths/weaknesses; the LLM cascade selector keeps
    its existing logic intact.
  * **Default OFF** — ``benchmarks_enabled`` ships at False. The query
    + aggregator APIs work without the scheduler running (they just
    return empty), so the operator can turn it on incrementally.
  * **Cost-bounded** — every catalog pass has a hard cap (default
    $1.00) so a runaway prompt + a runaway model can't drain
    budgets.

Composition
───────────

  * Future v2: feed the per-model summary into the LLM selector as
    one signal among many (capability_regression + agreement_ledger
    + Goodhart guard already participate). Today's v1 is purely a
    leaderboard for operator inspection.
  * Cross-references with ``capability_regression``: that subsystem
    measures "did we lose a tool capability?" via deletion-of-tools
    signal; this one measures "how does each model do on a fixed
    eval set?" — orthogonal but complementary.
"""
from __future__ import annotations

from app.benchmarks.aggregator import (
    filter_runs,
    leaderboard,
    per_model,
    per_task,
    per_task_and_model,
    summarise,
)
from app.benchmarks.catalog import (
    VALID_TIERS,
    catalog_stats,
    get_task,
    load_tasks,
)
from app.benchmarks.models import (
    BenchmarkRun,
    BenchmarkTask,
    LLMResult,
)
from app.benchmarks.runner import (
    LLMCall,
    run_catalog,
    run_task,
    run_task_against_all_targets,
)
from app.benchmarks.scheduler_job import run_refresh
from app.benchmarks.scorers import (
    SCORER_REGISTRY,
    score,
)
from app.benchmarks.store import (
    append_run,
    iter_runs,
    load_all,
    stats,
)

__all__ = [
    "BenchmarkRun",
    "BenchmarkTask",
    "LLMCall",
    "LLMResult",
    "SCORER_REGISTRY",
    "VALID_TIERS",
    "append_run",
    "catalog_stats",
    "filter_runs",
    "get_task",
    "iter_runs",
    "leaderboard",
    "load_all",
    "load_tasks",
    "per_model",
    "per_task",
    "per_task_and_model",
    "run_catalog",
    "run_refresh",
    "run_task",
    "run_task_against_all_targets",
    "score",
    "stats",
    "summarise",
]
