"""U4 wiring orchestrator.

PROGRAM §63. Glues U1 (capability extraction), U2 (impact analysis),
U3 (trial harness — looked up from cached results, not run inline),
and U4 (MAJOR auto-CR gate) into one entry point the
:mod:`app.dependency_radar` MAJOR loop can call.

Why the trial is looked up, not run inline
==========================================

A pytest run takes minutes — too slow for the radar daemon's
weekly tick. The trial harness is invoked asynchronously (by
:mod:`app.upgrade_lifecycle.trial_scheduler`, a follow-on) and
persists its results to a small JSON-per-package ledger. This
orchestrator only reads that ledger; if no fresh trial result
exists for ``(package, to_version)``, the gate condition
``trial_not_run`` fails and the radar falls through to its
existing Signal-only behavior. The orchestrator emits a side-channel
"please run a trial" hint via the trial-scheduler so the next
scheduler tick picks it up.

This decoupling is intentional — the slow path is async, the fast
path (radar daemon) stays fast.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.upgrade_lifecycle.changelog_fetcher import (
    _fetch_pypi_metadata,
    extract_for_package,
)
from app.upgrade_lifecycle.impact_analysis import analyze as analyze_impact
from app.upgrade_lifecycle.major_auto_cr import (
    GateOutcome,
    file_major_auto_cr,
)
from app.upgrade_lifecycle.protocol import Capability, TrialResult

logger = logging.getLogger(__name__)


# ── Trial ledger ─────────────────────────────────────────────────────────


def _trials_dir() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "trials"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "trials"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/trials")


def _trial_path(package: str, to_version: str) -> Path:
    safe_pkg = package.lower().replace("/", "_").replace("..", "_")
    safe_ver = to_version.replace("/", "_").replace("..", "_")
    return _trials_dir() / f"{safe_pkg}__{safe_ver}.json"


def lookup_trial(package: str, to_version: str) -> Optional[TrialResult]:
    """Read the most recent persisted trial for ``(package, to_version)``.

    Returns None if no trial has been run yet. The trial-scheduler
    follow-on writes these files; the orchestrator only reads.
    """
    path = _trial_path(package, to_version)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return TrialResult(
            package=str(data.get("package", "")),
            from_version=str(data.get("from_version", "")),
            to_version=str(data.get("to_version", "")),
            status=str(data.get("status", "")),
            pass_count=int(data.get("pass_count") or 0),
            fail_count=int(data.get("fail_count") or 0),
            failures=tuple(data.get("failures") or ()),
            elapsed_s=float(data.get("elapsed_s") or 0.0),
            cost_estimate_usd=float(data.get("cost_estimate_usd") or 0.0),
            session_id=str(data.get("session_id", "")),
        )
    except (TypeError, ValueError):
        return None


def persist_trial(trial: TrialResult) -> None:
    """Write a trial result to the per-package ledger. Idempotent on
    ``(package, to_version)`` — same key overwrites."""
    path = _trial_path(trial.package, trial.to_version)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trial.to_dict(), indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError:
        logger.debug("ul.orchestrator: trial persist failed", exc_info=True)


def request_trial(package: str, to_version: str) -> None:
    """Side-channel "please schedule a trial for this" hint.

    Writes a one-line marker to ``<trials_dir>/_pending.jsonl`` so the
    trial scheduler (follow-on) knows what's queued. Idempotent —
    duplicate requests collapse on the scheduler side.
    """
    try:
        d = _trials_dir()
        d.mkdir(parents=True, exist_ok=True)
        marker = d / "_pending.jsonl"
        row = json.dumps({
            "package": package,
            "to_version": to_version,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True)
        with marker.open("a", encoding="utf-8") as f:
            f.write(row + "\n")
    except OSError:
        logger.debug("ul.orchestrator: request_trial write failed", exc_info=True)


# ── Public API ───────────────────────────────────────────────────────────


def try_auto_cr_for_major(
    *,
    package: str,
    from_version: str,
    to_version: str,
    metadata_fetcher: Optional[Callable[[str], Optional[dict]]] = None,
    llm_builder: Optional[Callable[[], Any]] = None,
    trial_lookup: Optional[Callable[[str, str], Optional[TrialResult]]] = None,
    stage_fn: Optional[Callable] = None,
    request_trial_fn: Optional[Callable[[str, str], None]] = None,
    impact_repo_root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> tuple[bool, Optional[GateOutcome]]:
    """Orchestrate U1+U2+(U3 lookup)+U4 for one MAJOR finding.

    Returns ``(auto_cr_filed: bool, gate_outcome: Optional[GateOutcome])``.

    * ``auto_cr_filed == True`` — the radar should SKIP the Signal-only
      alert for this finding because a CR is now in /cp/changes.
    * ``auto_cr_filed == False`` — fall through to the existing alert.

    Side effects: capability ledger row added (U1), trial request
    queued if missing (so the scheduler picks it up next tick).

    Every external dependency is injectable for tests.
    """
    md_fetch = metadata_fetcher or _fetch_pypi_metadata
    trial_lookup_fn = trial_lookup or lookup_trial
    req_trial = request_trial_fn or request_trial

    pypi_metadata = md_fetch(package)

    # U1 — capability extraction (cheap, idempotent — dedups internally)
    capability: Optional[Capability] = None
    try:
        capability = extract_for_package(
            package, from_version, to_version,
            metadata_fetcher=md_fetch,
            llm_builder=llm_builder,
        )
    except Exception:
        logger.debug("ul.orchestrator: U1 failed for %s", package, exc_info=True)

    # U2 — impact analysis (cheap, deterministic)
    impact = None
    if capability is not None:
        try:
            impact = analyze_impact(capability, repo_root=impact_repo_root)
        except Exception:
            logger.debug("ul.orchestrator: U2 failed for %s", package, exc_info=True)

    # U3 — trial lookup (NOT run inline; lookup from cached ledger)
    trial = trial_lookup_fn(package, to_version)
    if trial is None:
        # Hint the scheduler to run a trial soon.
        try:
            req_trial(package, to_version)
        except Exception:
            logger.debug("ul.orchestrator: request_trial failed", exc_info=True)

    # U4 — gate + maybe file CR
    outcome = file_major_auto_cr(
        package=package, from_version=from_version, to_version=to_version,
        capability=capability, impact=impact, trial=trial,
        pypi_metadata=pypi_metadata, now=now, stage_fn=stage_fn,
    )
    if outcome is None:
        return False, None
    return outcome.passed, outcome
