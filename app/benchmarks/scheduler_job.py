"""Idle-scheduler entry point for the benchmark suite (Phase C.3, 2026-05-22).

One scheduler tick = one full catalog pass against every model tier,
subject to:

  * master switch ``benchmarks_enabled`` (default OFF — suite ships
    dormant)
  * cadence guard (default 24h between passes; the leaderboard
    doesn't need fresher data than that)
  * per-pass cost cap (default $1.00 — bounds the blast radius if
    the catalog or a model regression sends costs spiralling)

The job is failure-isolated: an exception in one task doesn't poison
the pass; the runner already absorbs each call independently.

The default ``LLMCall`` resolution path lazy-imports the LLM cascade
and resolves a tier to a concrete model. Test paths inject a stub
instead.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.benchmarks.catalog import load_tasks
from app.benchmarks.models import LLMResult
from app.benchmarks.runner import LLMCall, run_catalog

logger = logging.getLogger(__name__)


# Default cadence: 24 hours. Override via ``BENCHMARKS_CADENCE_S``.
_DEFAULT_CADENCE_S = 24 * 60 * 60

_STATE_FILENAME = "scheduler_state.json"

_state_lock = threading.RLock()


# ── Master switch + cadence ─────────────────────────────────────────


def _is_enabled() -> bool:
    """Read the master switch. Any failure → False (failure-isolated)."""
    try:
        from app.runtime_settings import get_benchmarks_enabled
        return get_benchmarks_enabled()
    except Exception:
        return False


def _cadence_s() -> int:
    """Read cadence override from env, fall back to default."""
    import os
    raw = os.environ.get("BENCHMARKS_CADENCE_S", "").strip()
    if not raw:
        return _DEFAULT_CADENCE_S
    try:
        v = int(raw)
        if v <= 0:
            return _DEFAULT_CADENCE_S
        return v
    except (TypeError, ValueError):
        return _DEFAULT_CADENCE_S


def _max_pass_cost_usd() -> float:
    """Per-pass cost cap. Override via env. Default $1.00."""
    import os
    raw = os.environ.get("BENCHMARKS_MAX_PASS_COST_USD", "").strip()
    if not raw:
        return 1.00
    try:
        v = float(raw)
        if v <= 0:
            return 1.00
        return v
    except (TypeError, ValueError):
        return 1.00


# ── State (cadence guard) ───────────────────────────────────────────


def _state_path() -> Path:
    from app.benchmarks.store import get_base_dir
    return get_base_dir() / _STATE_FILENAME


def _read_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("benchmarks.scheduler: state write failed: %s", exc)


def reset_state_for_tests() -> None:
    """Test helper — clear the cadence state so the next pass fires."""
    path = _state_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


# ── Default LLMCall resolver ────────────────────────────────────────


def _default_llm_call(
    *,
    prompt: str,
    model_tier: str,
    max_tokens: Optional[int],
    timeout_s: int,
) -> LLMResult:
    """Production llm_call that resolves a tier to a concrete model
    via the LLM cascade.

    Lazy-imports the cascade so this module doesn't pull in heavy
    LLM dependencies on test paths. Falls back to ``LLMResult(error=…)``
    on any failure — every catalog pass keeps running.
    """
    started = time.monotonic()
    # Benchmark tier names (cheap/default/smart, see catalog.VALID_TIERS) →
    # the LLM factory's force_tier vocabulary (budget/mid/premium). The prior
    # code imported a non-existent ``app.llm.factory.get_llm_for_tier``, so
    # every run short-circuited to score=0.0 before the model was ever called
    # (the suite has produced only zeros since it began on 2026-05-23).
    _TIER_MAP = {"cheap": "budget", "default": "mid", "smart": "premium"}
    try:
        from app.llm_factory import create_specialist_llm
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return LLMResult(
            output="",
            error=f"llm_factory unavailable: {exc}",
            latency_ms=elapsed_ms,
        )

    try:
        llm = create_specialist_llm(
            role="default",
            force_tier=_TIER_MAP.get(model_tier, "mid"),
            max_tokens=max_tokens or 2048,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return LLMResult(
            output="",
            error=f"tier {model_tier!r} unresolvable: {exc}",
            latency_ms=elapsed_ms,
        )

    try:
        # CrewAI/langchain LLMs expose ``.call`` and ``.invoke`` shapes;
        # we use the most common ``call`` form. Real cost/token
        # accounting requires deeper integration — for v1 we record
        # zeros for cost/tokens and rely on the audit log for cost.
        response = llm.call(prompt)
        output = str(response) if response is not None else ""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return LLMResult(
            output=output,
            latency_ms=elapsed_ms,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return LLMResult(
            output="",
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=elapsed_ms,
        )


# ── Public entry point ──────────────────────────────────────────────


def run_refresh(
    *,
    force: bool = False,
    llm_call: Optional[LLMCall] = None,
) -> dict:
    """One scheduler tick. Returns a summary dict for operator visibility.

    Parameters
    ----------
    force
        Bypass the master-switch + cadence guards. Useful for
        operator-initiated runs from the dashboard / Signal /
        ``python -m app.benchmarks``.
    llm_call
        Override the LLM-call function (tests / ad-hoc model probes).
        ``None`` → use the default cascade resolver.

    Returns
    -------
    dict
        ``{"ran": bool, "skipped_reason": str, "n_runs": int,
           "elapsed_s": float, "cost_usd": float, "error": str}``.

    Never raises.
    """
    if not _is_enabled() and not force:
        return {
            "ran": False, "skipped_reason": "master_switch_off",
            "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0, "error": "",
        }

    with _state_lock:
        state = _read_state()
        cadence = _cadence_s()
        last_at = float(state.get("last_refresh_at", 0))
        now = time.time()
        if not force and (now - last_at) < cadence:
            return {
                "ran": False,
                "skipped_reason": (
                    f"cadence_guard: {int(now - last_at)}s since last "
                    f"pass < {cadence}s window"
                ),
                "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0, "error": "",
            }
        state["last_refresh_at"] = now
        _write_state(state)

    started = time.monotonic()
    try:
        tasks = load_tasks()
        if not tasks:
            elapsed = time.monotonic() - started
            return {
                "ran": False, "skipped_reason": "catalog_empty",
                "n_runs": 0, "elapsed_s": elapsed, "cost_usd": 0.0,
                "error": "",
            }
        runs = run_catalog(
            tasks,
            llm_call=llm_call or _default_llm_call,
            persist=True,
            max_cost_usd=_max_pass_cost_usd(),
        )
    except Exception as exc:
        elapsed = time.monotonic() - started
        logger.exception("benchmarks.scheduler: pass failed")
        with _state_lock:
            state = _read_state()
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            state["last_failed_at"] = time.time()
            _write_state(state)
        return {
            "ran": False, "skipped_reason": "exception",
            "n_runs": 0, "elapsed_s": elapsed, "cost_usd": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    elapsed = time.monotonic() - started
    total_cost = sum(r.cost_usd for r in runs)
    with _state_lock:
        state = _read_state()
        state["last_success_at"] = time.time()
        state["last_n_runs"] = len(runs)
        state["last_cost_usd"] = round(total_cost, 6)
        state["last_elapsed_s"] = round(elapsed, 3)
        state["last_indexed_at_iso"] = datetime.now(
            timezone.utc,
        ).isoformat()
        state.pop("last_error", None)
        _write_state(state)

    logger.info(
        "benchmarks.scheduler: ran %d benchmark(s) in %.2fs ($%.4f)",
        len(runs), elapsed, total_cost,
    )
    return {
        "ran": True, "skipped_reason": "",
        "n_runs": len(runs),
        "elapsed_s": round(elapsed, 3),
        "cost_usd": round(total_cost, 6),
        "error": "",
    }


__all__ = [
    "reset_state_for_tests",
    "run_refresh",
]
