"""
app.trajectory.store — Persistence for Trajectory records.

Two backends, both best-effort:

    1. JSON sidecar   (authoritative)
       workspace/trajectories/YYYY-MM-DD/<trajectory_id>.json
       Full trajectory data, human-inspectable, replay-ready.

    2. Chroma index   (auxiliary, semantic lookup)
       Collection: "trajectory_index"
       Compact metadata + embedding of (crew_name + task_description[:400])
       so tip synthesis can retrieve "similar past trajectories" when
       generalising a learning.

A failure in either backend must not raise — all entrypoints wrap in
try/except. The JSON sidecar is the source of truth; the Chroma index
can be rebuilt from sidecars at any time.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.trajectory.types import Trajectory, AttributionRecord

logger = logging.getLogger(__name__)

# Authoritative on-disk location. Aligned with existing `workspace/`
# layout (skills, proposals, memory, …).
_ROOT = Path("/app/workspace/trajectories")
_INDEX_COLLECTION = "trajectory_index"


def _daily_dir(ts_iso: str) -> Path:
    """Group sidecars by UTC day — matches the retention/prune cadence."""
    try:
        day = ts_iso.split("T", 1)[0] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _ROOT / day


# ── JSON sidecar ────────────────────────────────────────────────────────────

def persist_trajectory(trajectory: Trajectory) -> bool:
    """Write the trajectory's JSON sidecar + upsert the Chroma index row.

    Idempotent: re-writing the same trajectory_id overwrites the sidecar
    and upserts the index row. Returns True if the sidecar landed; the
    index row is best-effort and its failure does not affect the result.
    """
    if not trajectory or not trajectory.trajectory_id:
        return False

    # 1. JSON sidecar — authoritative
    sidecar_ok = False
    try:
        dir_ = _daily_dir(trajectory.started_at or "")
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"{trajectory.trajectory_id}.json"
        payload = json.dumps(trajectory.to_dict(), ensure_ascii=False, indent=2)
        # Atomic write: temp file + rename so concurrent readers never see
        # a half-written file. Matches the pattern used by workspace_sync.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        sidecar_ok = True
    except Exception:
        logger.debug("persist_trajectory: sidecar write failed", exc_info=True)

    # 2. Chroma index — auxiliary, semantic lookup
    try:
        _upsert_index(trajectory)
    except Exception:
        logger.debug("persist_trajectory: index upsert failed", exc_info=True)

    return sidecar_ok


def load_trajectory(trajectory_id: str) -> Optional[Trajectory]:
    """Read a trajectory by id. Scans recent daily dirs newest-first."""
    if not trajectory_id:
        return None
    try:
        if not _ROOT.exists():
            return None
        # Scan newest dir first — typical access pattern is "recent runs"
        for day_dir in sorted(_ROOT.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            path = day_dir / f"{trajectory_id}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return Trajectory.from_dict(data)
    except Exception:
        logger.debug("load_trajectory failed", exc_info=True)
    return None


def list_recent_trajectories(limit: int = 20) -> list[dict]:
    """Return compact metadata for the N most recent trajectories."""
    out: list[dict] = []
    try:
        if not _ROOT.exists():
            return out
        paths: list[Path] = []
        for day_dir in sorted(_ROOT.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            for p in sorted(day_dir.glob("traj_*.json"), reverse=True):
                # Attribution sidecars are `traj_<id>.attribution.json`
                # and share the `traj_*.json` prefix — filter them out so
                # list_recent_trajectories returns only trajectory files.
                if p.name.endswith(".attribution.json"):
                    continue
                paths.append(p)
                if len(paths) >= limit:
                    break
            if len(paths) >= limit:
                break
        for p in paths[:limit]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "trajectory_id": d.get("trajectory_id", ""),
                    "task_id": d.get("task_id", ""),
                    "crew_name": d.get("crew_name", ""),
                    "task_description": (d.get("task_description", "") or "")[:200],
                    "started_at": d.get("started_at", ""),
                    "ended_at": d.get("ended_at", ""),
                    "n_steps": len(d.get("steps", []) or []),
                    "outcome_summary": d.get("outcome_summary", {}),
                })
            except Exception:
                continue
    except Exception:
        logger.debug("list_recent_trajectories failed", exc_info=True)
    return out


# ── Attribution sidecar (read/write) ────────────────────────────────────────
#
# Attribution records live alongside their trajectory under the same
# workspace/trajectories/<day>/ path. Kept here in store.py — not in
# attribution.py — so the Self-Improver can read them without needing
# to import the Analyzer module itself. This preserves the safety
# invariant: attribution logic stays infrastructure-level; readers use
# the store.

def persist_attribution(trajectory: Trajectory,
                        record: AttributionRecord) -> bool:
    """Write an AttributionRecord JSON sidecar next to its trajectory.

    Path: workspace/trajectories/<YYYY-MM-DD>/<trajectory_id>.attribution.json
    Atomic write via temp + rename. Best-effort — returns False on error.
    """
    if not trajectory or not record:
        return False
    try:
        import json
        dir_ = _daily_dir(trajectory.started_at or "")
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"{trajectory.trajectory_id}.attribution.json"
        tmp = path.with_suffix(".attribution.json.tmp")
        tmp.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        logger.debug("persist_attribution failed", exc_info=True)
        return False


def load_attribution(trajectory_id: str) -> Optional[AttributionRecord]:
    """Read the AttributionRecord for a trajectory id, scanning recent days."""
    if not trajectory_id:
        return None
    try:
        import json
        if not _ROOT.exists():
            return None
        for day_dir in sorted(_ROOT.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            path = day_dir / f"{trajectory_id}.attribution.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return AttributionRecord.from_dict(data)
    except Exception:
        logger.debug("load_attribution failed", exc_info=True)
    return None


# ── Calibration log reader (read-only; writer lives in calibration.py) ─────
#
# Reading the calibration JSONL is pure I/O and the Self-Improver needs
# these aggregates for observability (see metrics.trajectory_health_summary).
# Keeping the writer and thresholds in `calibration.py` preserves the
# CLAUDE.md safety invariant — evaluation logic stays infrastructure-level
# while the read surface is available to consumers.

# Path is duplicated from calibration.py on purpose so a read doesn't pull
# in the writer module's imports (and thus its attribution linkage).
_CALIBRATION_LOG_PATH = Path("/app/workspace/trajectories/observer_calibration.jsonl")
_CALIBRATION_WINDOW = 100


def _resolved_calibration_path() -> Path:
    """Resolve the calibration log path, honouring test monkeypatches on
    either `calibration._LOG_PATH` or `store._CALIBRATION_LOG_PATH`.
    """
    import sys as _sys
    cal_mod = _sys.modules.get("app.trajectory.calibration")
    if cal_mod is not None:
        p = getattr(cal_mod, "_LOG_PATH", None)
        if p is not None:
            return p
    return _CALIBRATION_LOG_PATH


def read_calibration_rows(n: int = _CALIBRATION_WINDOW) -> list[dict]:
    """Return the last `n` calibration rows from the JSONL log.

    Bounded-memory tail read. Never raises; returns [] when the log is
    missing. Used by observability callers; writers live in
    `app.trajectory.calibration.record_calibration`.
    """
    from collections import deque
    path = _resolved_calibration_path()
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        import json as _json
        buf: deque[str] = deque(maxlen=n)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                buf.append(line)
        for line in buf:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(_json.loads(line))
            except Exception:
                continue
    except Exception:
        logger.debug("read_calibration_rows failed", exc_info=True)
    return out


def observer_calibration_report(n: int = _CALIBRATION_WINDOW) -> dict:
    """Compute per-failure-mode precision/recall over the recent window.

    Pure aggregation — no writes. Returns:
        {
          "samples": int,
          "per_mode": {
            "<mode>": {"tp": int, "fp": int, "fn": int, "tn": int,
                       "precision": float, "recall": float},
            ...
          }
        }
    """
    rows = read_calibration_rows(n)
    per_mode: dict[str, dict[str, int]] = {}
    for r in rows:
        p = r.get("predicted_mode", "none")
        a = r.get("actual_mode", "none")
        for m in (p, a):
            if m and m != "none":
                per_mode.setdefault(m, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})

    for r in rows:
        p = r.get("predicted_mode", "none")
        a = r.get("actual_mode", "none")
        for mode, counts in per_mode.items():
            pred_is_mode = (p == mode)
            actual_is_mode = (a == mode)
            if pred_is_mode and actual_is_mode:
                counts["tp"] += 1
            elif pred_is_mode and not actual_is_mode:
                counts["fp"] += 1
            elif (not pred_is_mode) and actual_is_mode:
                counts["fn"] += 1
            else:
                counts["tn"] += 1

    for mode, counts in per_mode.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        counts["precision"] = round(tp / (tp + fp), 3) if (tp + fp) else 0.0
        counts["recall"]    = round(tp / (tp + fn), 3) if (tp + fn) else 0.0

    return {"samples": len(rows), "per_mode": per_mode}


# ── Chroma index (auxiliary) ────────────────────────────────────────────────

def _upsert_index(trajectory: Trajectory) -> None:
    """Upsert a compact row into trajectory_index for semantic lookup.

    Kept separate from the sidecar so an embedding backend outage never
    blocks the authoritative write. Index is reconstructable from sidecars.
    """
    try:
        from app.memory.chromadb_manager import get_client, embed
    except Exception:
        return

    try:
        col = get_client().get_or_create_collection(
            _INDEX_COLLECTION, metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        return

    outcome = trajectory.outcome_summary or {}
    # Build an embedding-friendly document: task + crew + key outcome
    # signals. Keeping it terse makes semantic matches meaningful.
    doc_txt = "\n".join(filter(None, [
        f"crew: {trajectory.crew_name}",
        f"task: {trajectory.task_description[:400]}",
        f"outcome: confidence={outcome.get('confidence', '')} "
        f"completeness={outcome.get('completeness', '')} "
        f"retries={outcome.get('retries', 0)} "
        f"duration_s={outcome.get('duration_s', 0.0):.1f}"
        if outcome else "",
    ]))
    meta = {
        "trajectory_id": trajectory.trajectory_id,
        "crew_name": trajectory.crew_name,
        "started_at": trajectory.started_at,
        "ended_at": trajectory.ended_at,
        "n_steps": len(trajectory.steps),
        "passed_quality_gate": bool(outcome.get("passed_quality_gate", True)),
        "retries": int(outcome.get("retries", 0) or 0),
        "reflexion_exhausted": bool(outcome.get("reflexion_exhausted", False)),
    }
    try:
        col.upsert(
            ids=[trajectory.trajectory_id],
            documents=[doc_txt],
            metadatas=[meta],
            embeddings=[embed(doc_txt)],
        )
    except Exception:
        logger.debug("trajectory_index upsert failed", exc_info=True)
