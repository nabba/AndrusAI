"""Control-plane dashboard routes — upgrade-lifecycle topic.

PROGRAM §63 — U7 (operator surfaces). Wired into the parent router in
``dashboard_api.py`` via ``include_router``; no prefix/auth on this
sub-router because the parent supplies both.

Endpoints (under ``/api/cp``):

  * ``GET  /upgrade-lifecycle/state`` — overall subsystem status
    (master switches, current budget, rate-limit counter, last
    snapshot year).
  * ``GET  /upgrade-lifecycle/capabilities/{package}`` — list the
    persisted :class:`Capability` rows for a package.
  * ``GET  /ecosystem/snapshots`` — list every annual snapshot
    written to ``workspace/upgrade_lifecycle/ecosystem/``.
  * ``GET  /ecosystem/snapshots/{year}`` — return the snapshot dict
    PLUS the rendered markdown.
  * ``POST /ecosystem/major-upgrades/accept`` — operator accepts one
    major-upgrade row. Routes to CR (non-framework) or Tier-3
    proposal (framework).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# No prefix here — parent router carries `/api/cp` + auth.
router = APIRouter()


class AcceptMajorBody(BaseModel):
    year: int
    package: str
    to_version: str
    operator_actor: str = "operator"


class GenerateSnapshotBody(BaseModel):
    """Force-generate a snapshot for a given year.

    ``year`` defaults to the current calendar year on the server
    (the REST handler resolves it). ``force=True`` regenerates an
    existing snapshot while preserving per-row operator decisions
    (accepted / deferred / rejected statuses + cr_id pointers).
    """

    year: int | None = None
    force: bool = False


@router.get("/upgrade-lifecycle/state")
def upgrade_lifecycle_state() -> dict[str, Any]:
    """One-shot status summary for the React UpgradeLifecycleCard."""
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_enabled,
            get_upgrade_lifecycle_capability_extraction_enabled,
            get_upgrade_lifecycle_trial_enabled,
            get_upgrade_lifecycle_major_auto_cr_enabled,
            get_upgrade_lifecycle_capability_adoption_enabled,
            get_upgrade_lifecycle_capability_budget_usd_quarterly,
            get_ecosystem_snapshot_enabled,
            get_upgrade_lifecycle_apply_hook_enabled,
            get_upgrade_lifecycle_requirements_writer_enabled,
            get_upgrade_lifecycle_dockerfile_writer_enabled,
            get_upgrade_lifecycle_pyproject_writer_enabled,
        )
    except Exception:
        raise HTTPException(503, "runtime_settings unavailable")

    out: dict[str, Any] = {
        "switches": {
            "upgrade_lifecycle_enabled": get_upgrade_lifecycle_enabled(),
            "upgrade_lifecycle_capability_extraction_enabled": get_upgrade_lifecycle_capability_extraction_enabled(),
            "upgrade_lifecycle_trial_enabled": get_upgrade_lifecycle_trial_enabled(),
            "upgrade_lifecycle_major_auto_cr_enabled": get_upgrade_lifecycle_major_auto_cr_enabled(),
            "upgrade_lifecycle_capability_adoption_enabled": get_upgrade_lifecycle_capability_adoption_enabled(),
            "ecosystem_snapshot_enabled": get_ecosystem_snapshot_enabled(),
            # Apply-hook + writer toggles. Apply-hook is shown on the
            # React card; the three writers are REST-only but surfaced
            # here so an operator querying GET /state can see the full
            # subsystem state at a glance.
            "upgrade_lifecycle_apply_hook_enabled": get_upgrade_lifecycle_apply_hook_enabled(),
            "upgrade_lifecycle_requirements_writer_enabled": get_upgrade_lifecycle_requirements_writer_enabled(),
            "upgrade_lifecycle_dockerfile_writer_enabled": get_upgrade_lifecycle_dockerfile_writer_enabled(),
            "upgrade_lifecycle_pyproject_writer_enabled": get_upgrade_lifecycle_pyproject_writer_enabled(),
        },
        "quarterly_budget_usd": get_upgrade_lifecycle_capability_budget_usd_quarterly(),
    }

    # Budget + rate-limit state
    try:
        from app.upgrade_lifecycle.capability_adoption import (
            crs_this_week,
            current_quarter_spend,
            remaining_quarter_budget,
        )
        now = datetime.now(timezone.utc)
        out["budget_used_usd"] = current_quarter_spend(now=now)
        out["budget_remaining_usd"] = remaining_quarter_budget(now=now)
        out["crs_this_week"] = crs_this_week(now=now)
    except Exception:
        out["budget_used_usd"] = None
        out["budget_remaining_usd"] = None
        out["crs_this_week"] = None

    # Latest snapshot year
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import _snapshot_dir
        snap_dir = _snapshot_dir()
        if snap_dir.exists():
            years = sorted(int(p.stem) for p in snap_dir.glob("*.json")
                          if p.stem.isdigit())
            out["latest_snapshot_year"] = years[-1] if years else None
            out["available_snapshot_years"] = years
        else:
            out["latest_snapshot_year"] = None
            out["available_snapshot_years"] = []
    except Exception:
        out["latest_snapshot_year"] = None
        out["available_snapshot_years"] = []

    # Capability extraction counts
    try:
        from app.upgrade_lifecycle.changelog_fetcher import _capabilities_dir
        cap_dir = _capabilities_dir()
        if cap_dir.exists():
            files = list(cap_dir.glob("*.jsonl"))
            out["capability_packages_count"] = len(files)
        else:
            out["capability_packages_count"] = 0
    except Exception:
        out["capability_packages_count"] = 0

    return out


@router.get("/upgrade-lifecycle/capabilities/{package}")
def upgrade_lifecycle_capabilities(package: str) -> dict[str, Any]:
    """List Capability rows for one package."""
    try:
        from app.upgrade_lifecycle.changelog_fetcher import read_capabilities
    except Exception:
        raise HTTPException(503, "changelog_fetcher unavailable")
    caps = read_capabilities(package)
    return {
        "package": package,
        "count": len(caps),
        "rows": [c.to_payload() for c in caps],
    }


@router.get("/ecosystem/snapshots")
def ecosystem_snapshots() -> dict[str, Any]:
    """List every annual snapshot's year + summary."""
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import (
            _read_snapshot, _snapshot_dir,
        )
    except Exception:
        raise HTTPException(503, "ecosystem_snapshot unavailable")
    snap_dir = _snapshot_dir()
    if not snap_dir.exists():
        return {"years": []}
    years: list[dict[str, Any]] = []
    for path in sorted(snap_dir.glob("*.json")):
        if not path.stem.isdigit():
            continue
        yr = int(path.stem)
        snap = _read_snapshot(yr)
        if snap is None:
            continue
        years.append({
            "year": yr,
            "generated_at": snap.generated_at,
            "major_upgrade_count": len(snap.major_upgrades),
            "accepted_count": sum(
                1 for m in snap.major_upgrades if m.status == "accepted"
            ),
        })
    return {"years": years}


@router.get("/ecosystem/snapshots/{year}")
def ecosystem_snapshot_get(year: int) -> dict[str, Any]:
    """Return one snapshot's full dict + the rendered markdown body."""
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import (
            _markdown_path_for_year, _read_snapshot,
        )
    except Exception:
        raise HTTPException(503, "ecosystem_snapshot unavailable")
    snap = _read_snapshot(year)
    if snap is None:
        raise HTTPException(404, f"no snapshot for year {year}")
    md_path = _markdown_path_for_year(year)
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    return {"snapshot": snap.to_dict(), "markdown": markdown}


@router.post("/ecosystem/major-upgrades/accept")
def ecosystem_accept_major(body: AcceptMajorBody) -> dict[str, Any]:
    """Operator accepts one major-upgrade row; downstream CR or Tier-3 fires."""
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import accept_major_upgrade
    except Exception:
        raise HTTPException(503, "ecosystem_snapshot unavailable")
    result = accept_major_upgrade(
        year=body.year,
        package=body.package,
        to_version=body.to_version,
        operator_actor=body.operator_actor or "operator",
    )
    if not result.get("ok"):
        # 404 for "no snapshot" / "row not found"; 409 for "already accepted"
        reason = result.get("reason", "unknown")
        if reason in ("no_snapshot_for_year", "row_not_found"):
            raise HTTPException(404, reason)
        if reason == "already_accepted":
            raise HTTPException(409, reason)
        raise HTTPException(400, reason)
    return result


@router.post("/ecosystem/snapshots/generate")
def ecosystem_generate_snapshot(body: GenerateSnapshotBody) -> dict[str, Any]:
    """Operator-initiated snapshot generation.

    Used to populate the first snapshot mid-year (before the January
    cron fires) and to refresh an existing snapshot after new
    capabilities have been extracted. Live fetchers reach PyPI for
    framework versions; the call may take a few seconds and the
    operator should expect occasional network failures (each fetcher
    is failure-isolated, so the snapshot still generates with empty
    sections rather than aborting).

    ``force=true`` regenerates an existing snapshot while preserving
    operator decisions on already-accepted rows.
    """
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import generate_snapshot
    except Exception:
        raise HTTPException(503, "ecosystem_snapshot unavailable")
    try:
        snapshot = generate_snapshot(year=body.year, force=body.force)
    except Exception as exc:
        logger.warning("ecosystem_snapshot generate failed", exc_info=True)
        raise HTTPException(500, f"generate failed: {exc}")
    if snapshot is None:
        raise HTTPException(409, "ecosystem_snapshot disabled")
    return {
        "ok": True,
        "year": snapshot.year,
        "generated_at": snapshot.generated_at,
        "major_upgrade_count": len(snapshot.major_upgrades),
    }


@router.post("/upgrade-lifecycle/capability-adoption/run-pass")
def run_capability_adoption_pass() -> dict[str, Any]:
    """Trigger one U5 pass manually (for testing + operator-initiated runs).

    Same code path the weekly idle daemon will take. Rate-limit +
    budget gates apply so manual runs can't bypass the quarterly cap.
    """
    try:
        from app.upgrade_lifecycle.capability_adoption import run_one_pass
    except Exception:
        raise HTTPException(503, "capability_adoption unavailable")
    return run_one_pass()
