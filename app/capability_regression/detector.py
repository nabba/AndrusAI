"""Deletion-only diff over capability snapshots.

The detector compares a ``prev`` snapshot (the last persisted run) with
the ``curr`` (just-captured) and emits a :class:`RegressionReport`.

Three diff categories:

  * ``tools_deleted``        — names in prev.tools but not curr.tools.
  * ``models_truly_deleted`` — names in prev.models but neither in
                                curr.models nor in curr.blocked_models.
                                (If the operator blocked the model via
                                runtime_settings, that's intentional —
                                NOT a regression.)
  * ``models_newly_blocked`` — names in prev.models AND in
                                curr.blocked_models. Informational only.

Additions are silent — capability growth is not a regression signal.

``RegressionReport.has_regression`` is True iff EITHER of the first two
categories is non-empty. The newly-blocked set never triggers it on
its own.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from app.capability_regression.snapshot import CapabilitySnapshot

logger = logging.getLogger(__name__)


@dataclass
class RegressionReport:
    tools_deleted: list[str] = field(default_factory=list)
    models_truly_deleted: list[str] = field(default_factory=list)
    models_newly_blocked: list[str] = field(default_factory=list)
    prev_captured_at: str = ""
    curr_captured_at: str = ""

    @property
    def has_regression(self) -> bool:
        return bool(self.tools_deleted) or bool(self.models_truly_deleted)

    def alert_summary(self) -> str:
        """Human-readable one-paragraph summary for the Signal alert.

        Empty when there's no regression. Newly-blocked models are
        appended as a parenthetical note when present, but their
        absence-or-presence never gates whether the summary fires.
        """
        if not self.has_regression:
            return ""

        lines = []
        if self.tools_deleted:
            n = len(self.tools_deleted)
            sample = ", ".join(self.tools_deleted[:5])
            more = f" (+{n - 5} more)" if n > 5 else ""
            lines.append(f"⚠ {n} tool(s) deleted: {sample}{more}")
        if self.models_truly_deleted:
            n = len(self.models_truly_deleted)
            sample = ", ".join(self.models_truly_deleted[:5])
            more = f" (+{n - 5} more)" if n > 5 else ""
            lines.append(f"⚠ {n} model(s) removed from catalog: {sample}{more}")
        if self.models_newly_blocked:
            n = len(self.models_newly_blocked)
            sample = ", ".join(self.models_newly_blocked[:3])
            more = f" (+{n - 3} more)" if n > 3 else ""
            lines.append(
                f"(also {n} model(s) newly blocked — operator action: "
                f"{sample}{more})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_regression"] = self.has_regression
        return d


def detect_regressions(
    prev: Optional[CapabilitySnapshot],
    curr: CapabilitySnapshot,
) -> RegressionReport:
    """Compare prev to curr. ``prev=None`` → empty report (warm-up).

    The empty-on-first-run behavior is load-bearing: the scheduler job
    uses the first invocation just to seed a baseline, never alerting.
    """
    if prev is None:
        return RegressionReport(curr_captured_at=curr.captured_at)

    prev_tools = set(prev.tools)
    curr_tools = set(curr.tools)
    prev_models = set(prev.models)
    curr_models = set(curr.models)
    curr_blocked = set(curr.blocked_models)

    tools_deleted = sorted(prev_tools - curr_tools)

    models_gone = prev_models - curr_models
    models_newly_blocked = sorted(models_gone & curr_blocked)
    models_truly_deleted = sorted(models_gone - curr_blocked)

    return RegressionReport(
        tools_deleted=tools_deleted,
        models_truly_deleted=models_truly_deleted,
        models_newly_blocked=models_newly_blocked,
        prev_captured_at=prev.captured_at,
        curr_captured_at=curr.captured_at,
    )


def _regression_path() -> Path:
    from app.capability_regression.snapshot import _snapshot_dir
    return _snapshot_dir() / "regressions.jsonl"


def append_regression(report: RegressionReport) -> None:
    """Append a regression record to the audit log.

    Skips empty reports — only true regressions are recorded so the
    file stays a focused incident log, not a snapshot history.
    """
    if not report.has_regression:
        return
    try:
        with _regression_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    except Exception:
        logger.debug(
            "capability_regression: regression-log append failed",
            exc_info=True,
        )
