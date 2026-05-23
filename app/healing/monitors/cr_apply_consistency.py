"""cr_apply_consistency — B3-P2 — verify applied docs CRs landed on disk.

PROGRAM §63.11. Closes the test-coverage gap from P2-10 with a live
production check: even if my unit tests miss a bug specifically for
``docs/proposed_upgrades/`` CR application, this monitor catches it
retroactively in production.

Logic:

  * Read the change-request audit log.
  * For each CR with status APPLIED whose ``path`` starts with
    ``docs/proposed_upgrades/``, check that the file actually
    exists on disk under the repo root.
  * If audit says APPLIED but file is missing → alert. The CR
    workflow promised something it didn't deliver; investigate
    before more CRs land.

Cadence: daily probe; internal weekly cadence; capped at the last
50 applied CRs to keep the walk bounded.

Master switch: ``cr_apply_consistency_monitor_enabled`` (default ON).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


NAME = "cr_apply_consistency"
CADENCE_SECONDS = 24 * 3600
INTERNAL_WEEKLY_S = 7 * 24 * 3600
MASTER_SWITCH_KEY = "cr_apply_consistency_monitor_enabled"

_DOCS_PREFIX = "docs/proposed_upgrades/"
_MAX_RECENT_APPLIED = 50


def _state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "healing" / ".cr_apply_consistency_state.json"
    except Exception:
        return Path(
            "/app/workspace/healing/.cr_apply_consistency_state.json"
        )


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_cr_apply_consistency_monitor_enabled,
        )
        return get_cr_apply_consistency_monitor_enabled()
    except Exception:
        return True


def _audit_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "change_requests" / "audit.jsonl"
    except Exception:
        return Path("/app/workspace/change_requests/audit.jsonl")


def _repo_root() -> Path:
    """Repo root where applied files actually land."""
    try:
        return Path(__file__).resolve().parents[3]
    except Exception:
        return Path("/app")


def _read_state() -> dict:
    import json
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    import json
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug(
            "cr_apply_consistency: state write failed", exc_info=True,
        )


def _iter_recent_applied_docs_crs(
    audit_path: Optional[Path] = None,
    *, limit: int = _MAX_RECENT_APPLIED,
) -> Iterator[tuple[str, str]]:
    """Yield ``(cr_id, path)`` for the most recent APPLIED CRs under
    docs/proposed_upgrades/.

    Walks the audit log once; collapses multiple rows per CR to the
    latest status. Returns the *limit* newest matches.
    """
    import json
    path = audit_path or _audit_path()
    if not path.exists():
        return
    rows_by_cr: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cr_id = row.get("cr_id") or row.get("id") or ""
                target = str(
                    row.get("path") or row.get("target_path") or ""
                )
                if not cr_id or not target.startswith(_DOCS_PREFIX):
                    continue
                rows_by_cr[cr_id] = row
    except OSError:
        return

    # Sort by ts (newest first), take the most recent N.
    applied = []
    for cr_id, row in rows_by_cr.items():
        status = str(row.get("status") or row.get("transition") or "").lower()
        if status not in ("applied", "approved", "applied_ok"):
            continue
        ts = str(row.get("ts") or row.get("timestamp") or "")
        applied.append((ts, cr_id, str(
            row.get("path") or row.get("target_path") or ""
        )))
    applied.sort(reverse=True)
    for _ts, cr_id, target in applied[:limit]:
        yield cr_id, target


def _notify(alerts: list[str]) -> None:
    if not alerts:
        return
    try:
        from app.notify import notify
        body = (
            f"CR audit says APPLIED but file missing on disk for "
            f"{len(alerts)} CR(s) under `docs/proposed_upgrades/`. "
            f"This means the apply pipeline marked APPLIED without "
            f"actually writing. Operator: investigate "
            f"change_requests/apply.py + the host bridge.\n\n"
            + "\n".join(f"- `{a}`" for a in alerts[:10])
        )
        notify(
            title="📋 CR apply consistency drift",
            body=body,
            url="/cp/changes",
            topic="cr_apply_consistency",
            critical=True,    # silent failure of CR machinery — needs eyes
            arbitrate=False,
        )
    except Exception:
        logger.debug(
            "cr_apply_consistency: notify failed", exc_info=True,
        )


def run() -> None:
    """Driver entry — daily probe with weekly internal cadence."""
    if not _enabled():
        return
    now_ts = time.time()
    state = _read_state()
    last_run = float(state.get("last_run_at") or 0.0)
    if last_run > 0 and (now_ts - last_run) < INTERNAL_WEEKLY_S:
        return

    repo = _repo_root()
    missing: list[str] = []
    checked = 0
    for cr_id, target in _iter_recent_applied_docs_crs():
        checked += 1
        abs_path = repo / target
        if not abs_path.exists():
            missing.append(f"{cr_id}: {target}")

    if missing:
        _notify(missing)
        state["last_alert_at"] = now_ts
        state["last_missing_count"] = len(missing)
    state["last_run_at"] = now_ts
    state["checked"] = checked
    _write_state(state)
