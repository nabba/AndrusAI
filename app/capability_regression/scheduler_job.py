"""Hourly capability-regression scan.

Single entry point :func:`run_one_pass` is what the idle scheduler
invokes. The function is failure-isolated — any exception is logged
and the snapshot save still happens so the next run has a baseline.

Behavior:
  1. Read ``capability_regression_enabled`` master switch — bail if OFF.
  2. Take current snapshot.
  3. Load prior snapshot (may be None on first ever run).
  4. Detect regressions.
  5. If regression detected: append to audit log + emit Signal alert
     via the canonical notify() (with topic for arbiter dedup) +
     emit a continuity-ledger landmark.
  6. Save current snapshot as the new baseline.

Newly-blocked models alone (no other regression) do not trigger an
alert — that's operator intent surfaced back to the operator.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.capability_regression.detector import (
    RegressionReport,
    append_regression,
    detect_regressions,
)
from app.capability_regression.snapshot import (
    CapabilitySnapshot,
    load_snapshot,
    save_snapshot,
    take_snapshot,
)

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_capability_regression_enabled()
    except Exception:
        logger.debug(
            "capability_regression: master-switch read failed", exc_info=True,
        )
        return True  # fail-open: if runtime_settings is sick, still observe


def _maybe_notify(report: RegressionReport) -> None:
    if not report.has_regression:
        return
    body = report.alert_summary()
    try:
        from app.notify import notify
        notify(
            title="Capability regression detected",
            body=body,
            url="/cp/ops",
            topic="capability_regression",
            tag="capability-regression",
        )
    except Exception:
        logger.debug(
            "capability_regression: notify() failed", exc_info=True,
        )


def _maybe_emit_landmark(report: RegressionReport) -> None:
    if not report.has_regression:
        return
    try:
        from app.identity.continuity_ledger import emit_event
        emit_event(
            kind="capability_regression",
            payload={
                "tools_deleted": report.tools_deleted,
                "models_truly_deleted": report.models_truly_deleted,
                "prev_captured_at": report.prev_captured_at,
                "curr_captured_at": report.curr_captured_at,
            },
            source_module="capability_regression",
        )
    except Exception:
        logger.debug(
            "capability_regression: ledger emit failed", exc_info=True,
        )


def run_one_pass() -> Optional[RegressionReport]:
    """Single scheduler iteration. Returns the report (or None on disabled).

    Tests use the return value to assert detection happened; the
    scheduler itself ignores the return.
    """
    if not _is_enabled():
        logger.debug("capability_regression: disabled, skipping")
        return None

    curr = take_snapshot()
    prev = load_snapshot()
    report = detect_regressions(prev, curr)

    if report.has_regression:
        logger.warning(
            "capability_regression: REGRESSION detected — "
            "%d tool(s), %d model(s)",
            len(report.tools_deleted),
            len(report.models_truly_deleted),
        )
        append_regression(report)
        _maybe_notify(report)
        _maybe_emit_landmark(report)

    save_snapshot(curr)
    return report


# Idle-scheduler-friendly entry point — accepts no args, returns nothing.
def scheduler_entry() -> None:
    try:
        run_one_pass()
    except Exception:
        logger.warning(
            "capability_regression: scheduler entry failed", exc_info=True,
        )
