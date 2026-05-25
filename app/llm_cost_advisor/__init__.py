"""Cost-advisor subsystem.

Weekly LIGHT idle job that watches per-provider spend trends and
proposes cap adjustments via :mod:`app.proposal_bridge`.  Strictly
observational — never auto-applies; operators approve via the
standard change-request workflow.

Public surface:

  * :func:`run` — entry point invoked by the idle scheduler.  Loads
    7-day spend, analyses, files proposals via the bridge.  Returns
    the list of staged proposals (empty when nothing to advise).

Composition with the rest of the cost model
-------------------------------------------

The advisor sits at LAYER 6+ in the cost model — it doesn't gate
calls or change selections; it asks the operator to adjust the
caps that DO gate.  See ``docs/COST_MODEL.md`` for the six-layer
hierarchy.

It is the ONLY component in the cost model that has an opinion
about whether the operator's chosen caps are right; everything
else (per-call cap / per-construction cap / monthly brake /
adaptive back-pressure / cost mode) just enforces operator policy.
"""
import logging
import os
import time
from pathlib import Path

from .analyzer import (
    DailySpend, ProviderObservation, RoleObservation,
    analyze_provider_caps, analyze_role_budgets,
)
from .proposer import propose_adjustments, run as _run_unguarded

logger = logging.getLogger(__name__)


# Cadence guard — at most one analyser pass per 24h.  The LIGHT idle
# phase fires many times per minute; even though proposal_bridge's
# terminal-state guard prevents stage() spam, the per-pass SQL query
# + per-pass dedup lookups are wasteful.  Internal "last run was
# >24h ago" check keeps the advisor truly weekly-ish without
# requiring scheduler-level cadence support.
_CADENCE_PATH = Path(
    os.environ.get(
        "LLM_COST_ADVISOR_LAST_RUN",
        "/app/workspace/llm_cost_advisor/last_run.txt",
    )
)
_CADENCE_SECONDS = 24 * 3600.0


def _read_last_run() -> float:
    """Wall-clock UNIX timestamp of the prior run; 0.0 if unknown."""
    try:
        return float(_CADENCE_PATH.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _write_last_run(ts: float) -> None:
    """Persist the just-completed run's timestamp.  Best-effort."""
    try:
        _CADENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CADENCE_PATH.write_text(f"{ts}\n")
    except OSError:
        logger.debug(
            "llm_cost_advisor: cadence write failed", exc_info=True,
        )


def run() -> list[dict]:
    """Idle-scheduler entry point — master-switch gated, 24h cadence.

    Internal cadence: at most one analyser pass per 24h.  Subsequent
    LIGHT-phase invocations within the window return an empty list
    immediately without touching the SQLite ledger or
    proposal_bridge.  This keeps the LIGHT-pass cost effectively zero
    while still letting the scheduler register us at LIGHT cadence
    (so a missed day doesn't delay by another full day — the next
    LIGHT pass after the 24h window picks it up).

    Master switch: ``cost_advisor_enabled`` in runtime_settings
    (default ON).  Failure-isolated — any error short-circuits to
    empty result so a broken advisor never disrupts the idle phase.
    """
    try:
        from app.runtime_settings import get_cost_advisor_enabled
        if not get_cost_advisor_enabled():
            return []
    except Exception:
        pass

    # Cadence check.  Failure-OPEN: if the timestamp read fails
    # (permissions, missing dir on first boot, …) we treat as
    # "unknown" and run.
    now = time.time()
    last = _read_last_run()
    if last and (now - last) < _CADENCE_SECONDS:
        return []

    try:
        result = _run_unguarded()
    except Exception:
        logger.warning(
            "llm_cost_advisor: run failed", exc_info=True,
        )
        return []

    _write_last_run(now)
    return result


def _reset_cadence_for_tests() -> None:
    """Test helper — wipe the cadence timestamp so a subsequent
    ``run()`` actually executes.  Not part of the public surface."""
    try:
        _CADENCE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "DailySpend",
    "ProviderObservation",
    "RoleObservation",
    "analyze_provider_caps",
    "analyze_role_budgets",
    "propose_adjustments",
    "run",
]
