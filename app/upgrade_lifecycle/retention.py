"""P1#d — Retention policy for upgrade-lifecycle artefacts.

The subsystem accumulates four growing artefact classes:

  * **Capability ledgers** (one JSONL per package) — append-only,
    one row per ``(package, to_version)``. Decade-scale: ~10 rows /
    package / year × 100 packages ≈ 10k rows. Per-row size ~1 KB.
    Modest in absolute terms but unbounded growth over decades is
    not free.
  * **Trial result JSONs** (one per ``(package, to_version)``) —
    overwritten on each new trial of the same key. Bounded per-key
    BUT can accumulate orphan keys (packages we trialled once and
    never bumped). Each file ~200 bytes; capped by uniqueness, not
    growth.
  * **Trial pending queue** (`_pending.jsonl`) — append-only line
    file. The scheduler deletes processed rows but a crash could
    leave the file growing unbounded.
  * **Budget ledger** (`extraction_budget_ledger.jsonl` +
    ``adoption/budget_ledger.jsonl``) — one row per LLM attempt.
    Bounded by the budget itself (50 rows/month) but old months'
    rows aren't useful past ~2 years for audit.

This module exposes three operations, each idempotent + safe to
run repeatedly:

  * :func:`compact_capability_ledgers` — drops superseded rows
    (older row with same ``to_version`` as a younger row), preserves
    one row per ``(package, to_version)``. Hash chain rebuilt to
    keep verify_chain happy.
  * :func:`prune_trial_results` — removes per-package result JSONs
    whose ``to_version`` does not appear in any current capability
    row (i.e. the trial was for a version we no longer track).
  * :func:`prune_budget_ledgers` — keeps the trailing
    :data:`_BUDGET_LEDGER_RETAIN_DAYS` days of rows in each budget
    ledger, atomic-rewrites the file. Older rows continue to live
    in the continuity-ledger as the durable audit record.

The retention pass runs from a new idle job (LIGHT, weekly internal
cadence) registered alongside the existing ul idle jobs.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_BUDGET_LEDGER_RETAIN_DAYS = 730   # ~2 years
_PENDING_QUEUE_MAX_ROWS = 1000
_TRIAL_RESULT_RETAIN_DAYS = 365    # 1 year for orphan trial JSONs


# ── Path helpers ─────────────────────────────────────────────────────────


def _ul_root() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override)
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle")


def _capabilities_dir() -> Path:
    return _ul_root() / "capabilities"


def _trials_dir() -> Path:
    return _ul_root() / "trials"


def _state_path() -> Path:
    return _ul_root() / "retention_state.json"


# ── Capability ledger compaction ─────────────────────────────────────────


def _read_jsonl_rows(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def compact_capability_ledger(package_path: Path) -> dict:
    """Rewrite *package_path* keeping the LATEST row per ``to_version``.

    Older rows for the same ``to_version`` are dropped. Hash chain is
    rebuilt — old hashes are discarded; the new chain restarts from
    GENESIS. The continuity ledger keeps the durable audit if the
    operator needs to reconstruct history.

    Returns ``{rows_before, rows_after, dropped, path}``.
    """
    rows = _read_jsonl_rows(package_path)
    if not rows:
        return {"rows_before": 0, "rows_after": 0, "dropped": 0,
                "path": str(package_path)}

    # Pick the LAST row per to_version (since file is append-only,
    # later rows are newer extractions).
    latest_by_version: dict[str, dict] = {}
    for row in rows:
        payload = row.get("payload") or {}
        ver = str(payload.get("to_version") or "")
        if not ver:
            continue
        latest_by_version[ver] = row

    survivors = list(latest_by_version.values())
    survivors.sort(key=lambda r: str(
        (r.get("payload") or {}).get("extracted_at") or ""
    ))
    dropped = len(rows) - len(survivors)
    if dropped <= 0:
        return {"rows_before": len(rows), "rows_after": len(rows),
                "dropped": 0, "path": str(package_path)}

    # Rebuild the hash chain from GENESIS.
    try:
        from app.upgrade_lifecycle.changelog_fetcher import (
            _GENESIS_HASH,
            _canonical_json,
            _compute_row_hash,
        )
    except Exception:
        logger.debug("ul.retention: hash helpers unavailable", exc_info=True)
        return {"rows_before": len(rows), "rows_after": len(rows),
                "dropped": 0, "path": str(package_path)}

    rebuilt: list[dict] = []
    prev_hash = _GENESIS_HASH
    for row in survivors:
        payload = row.get("payload") or {}
        new_hash = _compute_row_hash(prev_hash, payload)
        rebuilt.append({
            "payload": payload,
            "prev_hash": prev_hash,
            "hash": new_hash,
        })
        prev_hash = new_hash

    # Atomic rewrite.
    try:
        tmp = package_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for row in rebuilt:
                f.write(_canonical_json(row) + "\n")
        tmp.replace(package_path)
    except OSError:
        logger.debug(
            "ul.retention: compact write failed for %s", package_path,
            exc_info=True,
        )
        return {"rows_before": len(rows), "rows_after": len(rows),
                "dropped": 0, "path": str(package_path)}

    return {
        "rows_before": len(rows),
        "rows_after": len(rebuilt),
        "dropped": dropped,
        "path": str(package_path),
    }


def compact_capability_ledgers() -> dict:
    """Walk every per-package ledger + compact each one.

    Returns ``{ledgers_processed, rows_dropped_total}``.
    """
    cap_dir = _capabilities_dir()
    if not cap_dir.exists():
        return {"ledgers_processed": 0, "rows_dropped_total": 0}
    total_dropped = 0
    processed = 0
    for path in cap_dir.glob("*.jsonl"):
        result = compact_capability_ledger(path)
        processed += 1
        total_dropped += int(result.get("dropped") or 0)
    return {
        "ledgers_processed": processed,
        "rows_dropped_total": total_dropped,
    }


# ── Trial result pruning ─────────────────────────────────────────────────


def _capability_to_versions() -> set[str]:
    """Set of every ``to_version`` we currently track across all packages.

    Used to identify orphan trial results.
    """
    out: set[str] = set()
    cap_dir = _capabilities_dir()
    if not cap_dir.exists():
        return out
    for path in cap_dir.glob("*.jsonl"):
        for row in _read_jsonl_rows(path):
            payload = row.get("payload") or {}
            ver = str(payload.get("to_version") or "")
            if ver:
                out.add(ver)
    return out


def prune_trial_results(now: Optional[datetime] = None) -> dict:
    """Remove orphan trial-result JSONs.

    An "orphan" is a per-(package, to_version) JSON whose to_version
    isn't in any current capability ledger AND whose mtime is older
    than :data:`_TRIAL_RESULT_RETAIN_DAYS`. The age gate prevents
    deleting a trial result for a package whose capability ledger
    hasn't yet been generated (race).

    Returns ``{removed, kept, path}``.
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=_TRIAL_RESULT_RETAIN_DAYS)
    trials = _trials_dir()
    if not trials.exists():
        return {"removed": 0, "kept": 0, "path": str(trials)}

    keep_versions = _capability_to_versions()
    removed = 0
    kept = 0
    for path in trials.glob("*.json"):
        # Skip the pending-queue JSONL — only result JSONs.
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ver = str(data.get("to_version") or "")
        if ver in keep_versions:
            kept += 1
            continue
        try:
            mtime = path.stat().st_mtime
            if datetime.fromtimestamp(mtime, tz=timezone.utc) > cutoff:
                kept += 1   # too young — leave it
                continue
        except OSError:
            kept += 1
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            kept += 1
    return {"removed": removed, "kept": kept, "path": str(trials)}


# ── Pending queue rotation ───────────────────────────────────────────────


def cap_pending_queue() -> dict:
    """Truncate ``_pending.jsonl`` if it has grown past the cap.

    Keeps the most recent :data:`_PENDING_QUEUE_MAX_ROWS` lines. The
    scheduler's de-dup means losing older entries is safe — packages
    queued multiple times collapse to the newest request.
    """
    p = _trials_dir() / "_pending.jsonl"
    if not p.exists():
        return {"rotated": False, "rows": 0, "path": str(p)}
    try:
        with p.open("r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return {"rotated": False, "rows": 0, "path": str(p)}
    if len(lines) <= _PENDING_QUEUE_MAX_ROWS:
        return {"rotated": False, "rows": len(lines), "path": str(p)}
    kept = lines[-_PENDING_QUEUE_MAX_ROWS:]
    try:
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        return {"rotated": False, "rows": len(lines), "path": str(p)}
    return {
        "rotated": True,
        "rows": len(kept),
        "path": str(p),
        "dropped": len(lines) - len(kept),
    }


# ── Budget ledger pruning ────────────────────────────────────────────────


def _prune_budget_ledger(path: Path, *, now: datetime) -> dict:
    """Keep only rows from the last _BUDGET_LEDGER_RETAIN_DAYS days."""
    if not path.exists():
        return {"removed": 0, "kept": 0, "path": str(path)}
    cutoff = (now - timedelta(days=_BUDGET_LEDGER_RETAIN_DAYS)).isoformat()
    rows = _read_jsonl_rows(path)
    kept_rows = [r for r in rows if str(r.get("ts") or "") >= cutoff]
    if len(kept_rows) == len(rows):
        return {"removed": 0, "kept": len(rows), "path": str(path)}
    try:
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in kept_rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        tmp.replace(path)
    except OSError:
        return {"removed": 0, "kept": len(rows), "path": str(path)}
    return {
        "removed": len(rows) - len(kept_rows),
        "kept": len(kept_rows),
        "path": str(path),
    }


def prune_budget_ledgers(now: Optional[datetime] = None) -> dict:
    """Trim both extraction + adoption budget ledgers to retention window."""
    now_dt = now or datetime.now(timezone.utc)
    results = []
    for sub_path in (
        _ul_root() / "extraction_budget_ledger.jsonl",
        _ul_root() / "adoption" / "budget_ledger.jsonl",
    ):
        results.append(_prune_budget_ledger(sub_path, now=now_dt))
    return {"ledgers": results}


# ── Composite pass ───────────────────────────────────────────────────────


def run_retention_pass(now: Optional[datetime] = None) -> dict:
    """Run all retention operations once.

    Idempotent. Designed for a weekly LIGHT idle job.
    """
    now_dt = now or datetime.now(timezone.utc)
    out = {
        "started_at": now_dt.isoformat(),
        "compaction": {},
        "trial_prune": {},
        "pending_cap": {},
        "budget_prune": {},
    }
    try:
        out["compaction"] = compact_capability_ledgers()
    except Exception:
        logger.debug("ul.retention: compact failed", exc_info=True)
    try:
        out["trial_prune"] = prune_trial_results(now=now_dt)
    except Exception:
        logger.debug("ul.retention: trial prune failed", exc_info=True)
    try:
        out["pending_cap"] = cap_pending_queue()
    except Exception:
        logger.debug("ul.retention: pending cap failed", exc_info=True)
    try:
        out["budget_prune"] = prune_budget_ledgers(now=now_dt)
    except Exception:
        logger.debug("ul.retention: budget prune failed", exc_info=True)

    # Persist run state for next-tick cadence check.
    try:
        state_path = _state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "last_run_at": now_dt.isoformat(),
            "rows_dropped_total":
                int(out["compaction"].get("rows_dropped_total") or 0),
        }, indent=2, sort_keys=True))
    except OSError:
        pass

    return out
