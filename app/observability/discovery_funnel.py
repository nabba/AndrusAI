"""discovery_funnel — Counts the observation → action loop across 90 days.

Gap #6 (2026-05-24): the system observes a lot. paper_pipeline finds.
library_radar finds. capability_gap_analyzer finds. dependency_radar
finds. error_diagnosis composes structured-fix CRs. The backlog grows.
The **execution rate** — how many findings became applied changes —
isn't surfaced anywhere. If the answer is "0 in 90 days," the entire
observation half of the loop is theatre.

What this module produces
=========================

A per-source funnel:

  staged (proposal_bridge or trial_state)
    ↓
  CR filed (change_requests with that requestor)
    ↓
  CR APPLIED (terminal state)

For each origin (library_radar, capability_gap, paper_pipeline,
error_diagnosis, vendor_sunset, …) we report:

  * Proposals staged in the window.
  * CRs filed in the window.
  * CRs applied / rejected / rolled_back / pending in the window.
  * Funnel ratios: ``filed/staged`` and ``applied/filed``.

The result is consumed by:

  * The weekly companion briefing (one-section "📊 Discovery →
    adoption (90d)" block).
  * The REST surface ``GET /api/cp/funnel`` for an at-a-glance card.
  * The capability_inventory writer (so future-Andrus sees both
    "what can we do" and "what are we discovering vs adopting").

Design choices
==============

  * Pure file-walking, zero LLM. The signal is in counts, not in
    cleverness.
  * Window-bounded so the numbers are scale-invariant as the system
    ages. Default 90d.
  * Stagnant-source detection: a source with ≥5 stagings in the
    window AND ``applied == 0`` is surfaced as a finding worth
    investigating.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


NAME = "discovery_funnel"
MASTER_SWITCH_KEY = "discovery_funnel_enabled"

DEFAULT_WINDOW_DAYS = 90
_STATE_FILE_NAME = "discovery_funnel.json"
_STAGNANT_THRESHOLD = 5  # min stagings in window before stagnation alert fires


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_discovery_funnel_enabled
        return get_discovery_funnel_enabled()
    except Exception:
        return os.getenv(
            "DISCOVERY_FUNNEL_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _state_path() -> Path:
    return _workspace() / "observability" / _STATE_FILE_NAME


def _parse_iso(s: Any) -> Optional[float]:
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# ── Data shapes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceCounts:
    """All counts are within the time window passed to ``compute()``."""
    source: str
    staged: int = 0
    cr_filed: int = 0
    cr_applied: int = 0
    cr_rejected: int = 0
    cr_rolled_back: int = 0
    cr_pending: int = 0

    @property
    def filed_ratio(self) -> Optional[float]:
        if self.staged == 0:
            return None
        return self.cr_filed / self.staged

    @property
    def applied_ratio(self) -> Optional[float]:
        if self.cr_filed == 0:
            return None
        return self.cr_applied / self.cr_filed

    @property
    def is_stagnant(self) -> bool:
        """True iff a source produces stagings but no applied CRs.

        A nuanced definition: pure-rejection is not stagnation (the
        gate is doing its job). The signal we want is "we keep finding
        but nothing makes it into the system."
        """
        return self.staged >= _STAGNANT_THRESHOLD and self.cr_applied == 0


# ── Collectors ──────────────────────────────────────────────────────────


def _count_proposal_bridge_stagings(window_start: float) -> dict[str, int]:
    """Walk ``workspace/proposal_bridge/<source>/*.json`` and count
    rows inside the window."""
    counts: dict[str, int] = defaultdict(int)
    root = _workspace() / "proposal_bridge"
    if not root.exists():
        return counts
    try:
        for source_dir in root.iterdir():
            if not source_dir.is_dir():
                continue
            for f in source_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ts = _parse_iso(data.get("created_at") or data.get("ts"))
                if ts is None or ts < window_start:
                    continue
                counts[source_dir.name] += 1
    except OSError:
        return counts
    return counts


def _count_library_radar_trials(window_start: float) -> int:
    """Trials staged by ``library_radar``. Distinct from
    proposal_bridge — library_radar maintains its own trial ledger."""
    path = _workspace() / "library_radar" / "trial_state.jsonl"
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = _parse_iso(row.get("ts"))
                if ts is None or ts < window_start:
                    continue
                n += 1
    except OSError:
        return 0
    return n


def _walk_change_requests(window_start: float) -> dict[str, dict[str, int]]:
    """Walk ``workspace/change_requests/*.json`` and group by requestor.

    Returns ``{requestor: {filed: N, applied: N, rejected: N,
    rolled_back: N, pending: N}}``.
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {
        "filed": 0, "applied": 0, "rejected": 0, "rolled_back": 0, "pending": 0,
    })
    root = _workspace() / "change_requests"
    if not root.exists():
        return counts
    try:
        for f in root.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            ts = _parse_iso(data.get("created_at"))
            if ts is None or ts < window_start:
                continue
            requestor = str(data.get("requestor") or "unknown")
            status = str(data.get("status") or "").strip().lower()
            counts[requestor]["filed"] += 1
            if status == "applied":
                counts[requestor]["applied"] += 1
            elif status == "rejected":
                counts[requestor]["rejected"] += 1
            elif status in ("rolled_back", "reverted"):
                counts[requestor]["rolled_back"] += 1
            else:
                counts[requestor]["pending"] += 1
    except OSError:
        return counts
    return counts


# ── Compute ─────────────────────────────────────────────────────────────


def compute(*, window_days: int = DEFAULT_WINDOW_DAYS, now: Optional[float] = None) -> dict[str, Any]:
    """Compute the discovery → adoption funnel. Pure-read; no writes."""
    cur = float(now) if now is not None else time.time()
    window_start = cur - window_days * 86400

    stagings = _count_proposal_bridge_stagings(window_start)
    lib_radar_trials = _count_library_radar_trials(window_start)
    if lib_radar_trials:
        # library_radar's trial_state.jsonl is parallel to proposal_bridge;
        # merge under the same logical source name.
        stagings["library_radar"] = stagings.get("library_radar", 0) + lib_radar_trials

    cr_counts = _walk_change_requests(window_start)

    # Union the sources across stagings + CR filings — every requestor
    # that produced a CR is a discovery source, even if not in the
    # proposal_bridge dir.
    sources = sorted(set(stagings) | set(cr_counts))
    rows: list[SourceCounts] = []
    for src in sources:
        cr = cr_counts.get(src, {})
        rows.append(SourceCounts(
            source=src,
            staged=stagings.get(src, 0),
            cr_filed=cr.get("filed", 0),
            cr_applied=cr.get("applied", 0),
            cr_rejected=cr.get("rejected", 0),
            cr_rolled_back=cr.get("rolled_back", 0),
            cr_pending=cr.get("pending", 0),
        ))

    totals = SourceCounts(
        source="__total__",
        staged=sum(r.staged for r in rows),
        cr_filed=sum(r.cr_filed for r in rows),
        cr_applied=sum(r.cr_applied for r in rows),
        cr_rejected=sum(r.cr_rejected for r in rows),
        cr_rolled_back=sum(r.cr_rolled_back for r in rows),
        cr_pending=sum(r.cr_pending for r in rows),
    )

    stagnant = [r.source for r in rows if r.is_stagnant]

    iso = datetime.fromtimestamp(cur, tz=timezone.utc).isoformat()
    return {
        "as_of": iso,
        "window_days": window_days,
        "sources": [
            {**asdict(r),
             "filed_ratio": r.filed_ratio,
             "applied_ratio": r.applied_ratio,
             "is_stagnant": r.is_stagnant}
            for r in rows
        ],
        "totals": {
            **asdict(totals),
            "filed_ratio": totals.filed_ratio,
            "applied_ratio": totals.applied_ratio,
        },
        "stagnant_sources": stagnant,
    }


# ── Briefing snippet ─────────────────────────────────────────────────────


def briefing_section(*, window_days: int = DEFAULT_WINDOW_DAYS) -> str:
    """One-section markdown for the weekly briefing composer."""
    result = compute(window_days=window_days)
    sources = result["sources"]
    if not sources:
        return ""
    totals = result["totals"]
    lines = [f"📊 **Discovery → adoption ({window_days}d)**"]
    lines.append(
        f"Across {len(sources)} source(s): {totals['staged']} staged · "
        f"{totals['cr_filed']} CR filed · {totals['cr_applied']} applied · "
        f"{totals['cr_rejected']} rejected · {totals['cr_pending']} pending."
    )
    if result["stagnant_sources"]:
        lines.append(
            "⚠ Stagnant sources (≥5 stagings, 0 applied): "
            + ", ".join(f"`{s}`" for s in result["stagnant_sources"])
        )
    return "\n".join(lines)


# ── Persistence (for the REST endpoint) ─────────────────────────────────


def _write_snapshot(result: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8",
        )
    except Exception:
        logger.debug("discovery_funnel: snapshot write failed", exc_info=True)


def latest_snapshot() -> Optional[dict[str, Any]]:
    p = _state_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_once() -> dict[str, Any]:
    """Idle-job entry point. Builds the snapshot + persists it."""
    if not _enabled():
        return {"ran": False, "skipped": True}
    try:
        result = compute()
        _write_snapshot(result)
        return {
            "ran": True,
            "as_of": result["as_of"],
            "n_sources": len(result["sources"]),
            "n_stagnant": len(result["stagnant_sources"]),
        }
    except Exception as exc:
        logger.warning("discovery_funnel: run_once failed", exc_info=True)
        return {"ran": True, "error": str(exc)}


__all__ = [
    "compute",
    "briefing_section",
    "run_once",
    "latest_snapshot",
    "SourceCounts",
    "DEFAULT_WINDOW_DAYS",
]
