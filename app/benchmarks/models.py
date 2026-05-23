"""Data model for the benchmark suite (Phase C.3, 2026-05-22).

Two records, both frozen-dataclass-immutable so callers can pass them
around without worrying about accidental mutation:

  * :class:`BenchmarkTask` — what to test. Loaded from a YAML file in
    ``app/benchmarks/tasks/`` (or constructed in-test). Has an input
    string, an expected answer, the name of a scorer, optional kwargs
    for the scorer, and a list of model-tier targets (cheap / default /
    smart) the runner cycles through.
  * :class:`BenchmarkRun` — what happened. One row per (task, model,
    timestamp). Append-only into the JSONL store.

Plus :class:`LLMResult`, the contract between the runner and whichever
LLM-call function the caller wires in (real cascade, test stub, …).

Why frozen dataclasses?

The benchmark suite is read-heavy: the catalog is loaded once at
process start and queried for every refresh tick; the runs are
appended to one row at a time and never edited. Mutation would be a
bug here, so we make it impossible.

Why ``score`` is a float in [0.0, 1.0] not bool?

Scorers like ``contains`` give partial credit: matching 3 of 4 required
substrings → 0.75. The leaderboard aggregates means, so a single
boolean per run would lose that signal. ``passed`` is the boolean
derived field (``score >= 1.0``) so the simpler "did it pass" view
is also cheap.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class LLMResult:
    """One LLM call's output, captured by the runner.

    The runner passes this to the scorer. ``output`` is the text the
    model produced; the rest is bookkeeping for the leaderboard.

    ``error`` is non-empty when the call failed (timeout, API error,
    refused). The runner still records a row in that case — with
    ``score=0.0, passed=False, error=…`` — so failures are visible
    in the per-model aggregate.
    """

    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str = ""


@dataclass(frozen=True)
class BenchmarkTask:
    """One benchmark task definition.

    Loaded from YAML — see ``app/benchmarks/tasks/*.yaml`` for the
    canonical authoring format. Field names match the YAML keys.

    ``scorer`` is the name of a function in :mod:`app.benchmarks.scorers`
    (e.g. ``"exact_match"`` or ``"contains"``). Unknown scorers cause
    a load-time error in the catalog, never at runtime — easier to
    catch typos that way.

    ``scorer_args`` is forwarded verbatim as kwargs to the scorer.

    ``model_targets`` is a list of tier names (``"cheap"`` / ``"default"``
    / ``"smart"``) the runner expands to concrete model names via
    the LLM factory. A task that's only meaningful at the smart tier
    (e.g. a multi-step reasoning prompt) lists only ``smart`` so the
    cheap tier isn't penalised for not being able to solve it.
    """

    id: str
    name: str
    description: str
    input: str
    expected: Any  # str for exact_match/regex; list[str] for contains
    scorer: str
    scorer_args: dict[str, Any] = field(default_factory=dict)
    model_targets: list[str] = field(default_factory=lambda: ["default"])
    # Soft timeout for one LLM call. The runner honors it as a deadline
    # passed to the LLM function; if the LLM function ignores it, the
    # benchmark just runs longer — we don't enforce kill behavior here.
    timeout_s: int = 30
    # Hint for the LLM — cap output tokens so a runaway response
    # doesn't blow the cost cap. None = let the model decide.
    max_tokens: Optional[int] = None
    # Free-form category for grouping in the React leaderboard
    # (e.g. "arithmetic", "code", "reasoning").
    category: str = "general"

    def __post_init__(self) -> None:
        # Light validation — load time, not call time.
        if not self.id or not isinstance(self.id, str):
            raise ValueError(f"BenchmarkTask.id must be a non-empty string")
        if not self.scorer:
            raise ValueError(
                f"BenchmarkTask[{self.id}].scorer cannot be empty"
            )
        if not self.input:
            raise ValueError(
                f"BenchmarkTask[{self.id}].input cannot be empty"
            )
        if not isinstance(self.model_targets, list) or not self.model_targets:
            raise ValueError(
                f"BenchmarkTask[{self.id}].model_targets must be a "
                f"non-empty list"
            )


@dataclass(frozen=True)
class BenchmarkRun:
    """One execution of one task against one model.

    Append-only into ``workspace/benchmarks/runs.jsonl``. ``ts`` is the
    UTC ISO8601 timestamp the run completed.

    ``score`` is in [0.0, 1.0]; ``passed = score >= pass_threshold``
    (default 1.0 — strict). The store doesn't compute ``passed``; the
    aggregator does, so the threshold is configurable at read time.

    ``output_preview`` is the first 200 chars of the model output —
    stored so the operator can spot-check a few rows in the dashboard
    without having to re-run the prompt. Full output would balloon the
    JSONL; 200 chars is plenty to recognise an obvious miss.
    """

    task_id: str
    model: str
    ts: str  # ISO8601 UTC
    score: float
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    output_preview: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSONL-ready dict. (asdict preserves field order.)"""
        return asdict(self)

    def to_json_line(self) -> str:
        """Compact one-line JSON for JSONL writers."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BenchmarkRun":
        """Rehydrate from a JSONL row, tolerating missing optional fields."""
        return cls(
            task_id=str(d["task_id"]),
            model=str(d["model"]),
            ts=str(d["ts"]),
            score=float(d["score"]),
            latency_ms=int(d.get("latency_ms", 0)),
            tokens_in=int(d.get("tokens_in", 0)),
            tokens_out=int(d.get("tokens_out", 0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            output_preview=str(d.get("output_preview", "")),
            error=str(d.get("error", "")),
        )

    @property
    def passed(self) -> bool:
        """Strict pass: score must be exactly 1.0. Partial credit ≠ pass."""
        return self.score >= 1.0


__all__ = [
    "BenchmarkRun",
    "BenchmarkTask",
    "LLMResult",
]
