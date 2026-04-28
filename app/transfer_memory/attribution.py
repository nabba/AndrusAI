"""Negative-transfer attribution + demotion ladder (Phase 17c).

Idle-scheduler LIGHT job. Walks recent failed trajectories that
included an injected transfer-memory record, classifies each
implicated record under a ``NegativeTransferTag`` (heuristic, no LLM),
and applies the demotion ladder.

Demotion ladder (per record):
    no failures              → record stays active
    ≥1 failure of any tag    → audit log only; no behaviour change
    ≥3 same-tag failures     → "soft demote": add record id to the
                                retriever's blacklist file. Subsequent
                                retrievals exclude this record. Record
                                stays in the KB for dashboard / review.
    ≥5 same-tag failures     → "hard archive": index status flipped to
                                "archived" via update_record(). Stays
                                in the blacklist regardless.

Heuristic classifier (deterministic, no LLM):
    DOMAIN_MISMATCHED_ANCHOR — record.source_domain != target domain
    OVER_ABSTRACTION         — abstraction_score > 0.85 and content terse
    MISAPPLIED_BEST_PRACTICE — fallback when no other tag fits

Persistence:
    ``workspace/transfer_memory/negative_transfer.jsonl`` — audit log
        of (skill_record_id, trajectory_id, tag, ts).
    ``workspace/transfer_memory/demotion_blacklist.jsonl`` — newline-
        delimited list of record IDs the retriever filters out.
    ``workspace/transfer_memory/.last_attribution_at`` — cursor.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.transfer_memory.types import NegativeTransferTag

logger = logging.getLogger(__name__)


_NEG_TRANSFER_FILENAME = "negative_transfer.jsonl"
_BLACKLIST_FILENAME = "demotion_blacklist.jsonl"
_CURSOR_FILENAME = ".last_attribution_at"
_TRAJECTORIES_DIR = Path("/app/workspace/trajectories")

# Demotion thresholds (per record × tag).
_SOFT_DEMOTE_THRESHOLD = 3
_HARD_ARCHIVE_THRESHOLD = 5

# Maximum lookback when the cursor is missing (cold start).
_COLD_START_DAYS = 7


# ── Public entry point ───────────────────────────────────────────────

def run_attribution() -> dict:
    """Idle-scheduler entry. Returns a summary dict.

    Cooperative — reads ``idle_scheduler.should_yield()`` between
    trajectories and breaks early when a user task arrives. Cheap by
    construction; uses the cursor file to avoid re-processing.
    """
    summary = {
        "scanned": 0, "attributed": 0, "soft_demoted": 0,
        "hard_archived": 0, "errors": 0,
    }

    cursor = _read_cursor()
    candidates = _list_candidate_trajectories(since_ts=cursor)
    if not candidates:
        _write_cursor(time.time())
        return summary

    new_blacklist_entries: set[str] = set()
    new_archive_entries: list[str] = []

    for traj_path in candidates:
        if _should_yield_safe():
            logger.info("transfer_memory.attribution: yielding to user task")
            break
        summary["scanned"] += 1
        try:
            traj = _load_trajectory(traj_path)
        except Exception:
            summary["errors"] += 1
            continue
        if traj is None or not _is_failure(traj):
            continue

        injected_ids = traj.get("injected_skill_ids") or []
        if not injected_ids:
            continue

        target_domain = _crew_to_domain(traj.get("crew_name", ""))
        for sid in injected_ids:
            try:
                record = _load_skill_record(sid)
            except Exception:
                continue
            if record is None:
                continue
            if not record.provenance or not record.provenance.get("transfer_scope"):
                # Not a transfer-memory record — skip.
                continue

            tag = _classify(record, target_domain)
            _append_neg_transfer_log({
                "ts": time.time(),
                "skill_record_id": sid,
                "trajectory_id": traj.get("trajectory_id", ""),
                "tag": tag.value,
                "target_domain": target_domain,
                "record_domain": record.provenance.get("source_domain", ""),
                "abstraction_score": float(
                    record.provenance.get("abstraction_score", 0.0) or 0.0
                ),
            })
            summary["attributed"] += 1

            same_tag_count = _count_same_tag(sid, tag)
            if same_tag_count >= _HARD_ARCHIVE_THRESHOLD:
                if _hard_archive(record):
                    new_archive_entries.append(sid)
                    new_blacklist_entries.add(sid)
                    summary["hard_archived"] += 1
            elif same_tag_count >= _SOFT_DEMOTE_THRESHOLD:
                new_blacklist_entries.add(sid)
                summary["soft_demoted"] += 1

    if new_blacklist_entries:
        _extend_blacklist(new_blacklist_entries)

    _write_cursor(time.time())
    logger.info(
        "transfer_memory.attribution: ran "
        "(scanned=%d attributed=%d soft=%d archived=%d)",
        summary["scanned"], summary["attributed"],
        summary["soft_demoted"], summary["hard_archived"],
    )
    return summary


def is_blacklisted(record_id: str) -> bool:
    """Return True iff the record id is in the demotion blacklist.

    Used by the retriever's post-rank filter to drop demoted records
    without needing to mutate KB metadata.
    """
    if not record_id:
        return False
    return record_id in _read_blacklist()


# ── Heuristic classifier ─────────────────────────────────────────────

def _classify(record: Any, target_domain: str) -> NegativeTransferTag:
    """Pick the most specific tag the record + target context supports."""
    prov = record.provenance or {}
    record_domain = prov.get("source_domain", "") or ""
    abstraction = float(prov.get("abstraction_score", 0.0) or 0.0)

    if target_domain and record_domain and record_domain != target_domain:
        return NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR

    word_count = len((record.content_markdown or "").split())
    if abstraction > 0.85 and word_count < 80:
        return NegativeTransferTag.OVER_ABSTRACTION

    # Project leakage post-hoc — content was demoted to global_meta but
    # contains project-noun markers. Cheap regex check.
    transfer_scope = prov.get("transfer_scope", "") or ""
    if transfer_scope == "global_meta":
        try:
            from app.transfer_memory.sanitizer import check
            verdict = check(record.content_markdown or "")
            if verdict.allowed_scope.value != "global_meta":
                return NegativeTransferTag.PROJECT_SCOPE_LEAKAGE
        except Exception:
            pass

    return NegativeTransferTag.MISAPPLIED_BEST_PRACTICE


# ── Persistence helpers ──────────────────────────────────────────────

def _resolve_dir() -> Path:
    from app.transfer_memory.queue import _resolve_dir as base_dir, _ensure_dir
    _ensure_dir()
    return base_dir()


def _append_neg_transfer_log(row: dict) -> None:
    p = _resolve_dir() / _NEG_TRANSFER_FILENAME
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_neg_transfer_log() -> list[dict]:
    p = _resolve_dir() / _NEG_TRANSFER_FILENAME
    if not p.exists():
        return []
    rows: list[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _count_same_tag(skill_record_id: str, tag: NegativeTransferTag) -> int:
    rows = _read_neg_transfer_log()
    return sum(
        1 for r in rows
        if r.get("skill_record_id") == skill_record_id
        and r.get("tag") == tag.value
    )


def _read_blacklist() -> set[str]:
    p = _resolve_dir() / _BLACKLIST_FILENAME
    if not p.exists():
        return set()
    try:
        with p.open("r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()


def _extend_blacklist(ids: set[str]) -> None:
    if not ids:
        return
    p = _resolve_dir() / _BLACKLIST_FILENAME
    existing = _read_blacklist()
    new = ids - existing
    if not new:
        return
    try:
        with p.open("a", encoding="utf-8") as f:
            for rid in sorted(new):
                f.write(f"{rid}\n")
    except Exception:
        logger.debug(
            "transfer_memory.attribution: extend_blacklist failed",
            exc_info=True,
        )


def _read_cursor() -> float:
    p = _resolve_dir() / _CURSOR_FILENAME
    if not p.exists():
        return time.time() - _COLD_START_DAYS * 86400
    try:
        return float(p.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0.0


def _write_cursor(epoch_seconds: float) -> None:
    try:
        p = _resolve_dir() / _CURSOR_FILENAME
        p.write_text(f"{epoch_seconds}\n", encoding="utf-8")
    except Exception:
        pass


# ── Trajectory loaders ───────────────────────────────────────────────

def _list_candidate_trajectories(since_ts: float) -> list[Path]:
    """Find trajectory sidecar JSON files newer than ``since_ts``.

    Falls back to an empty list if the trajectories dir is absent
    (which is normal in environments where trajectory capture is off).
    """
    if not _TRAJECTORIES_DIR.exists():
        return []
    out: list[Path] = []
    try:
        for sub in sorted(_TRAJECTORIES_DIR.iterdir()):
            if not sub.is_dir():
                continue
            for jp in sub.glob("*.json"):
                try:
                    mtime = jp.stat().st_mtime
                except Exception:
                    continue
                if mtime >= since_ts:
                    out.append(jp)
    except Exception:
        return []
    return out


def _load_trajectory(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_failure(traj: dict) -> bool:
    """Flag a trajectory as failure if the outcome looks bad enough.

    Checks both the explicit verdict (when attribution ran) and the
    coarser outcome_summary signals so trajectories from runs without
    attribution still contribute.
    """
    outcome = traj.get("outcome_summary") or {}
    if isinstance(outcome, dict):
        if outcome.get("quality_gate") is False:
            return True
        if str(outcome.get("verdict", "")).lower() in {"failure", "regressed", "baseline_violation"}:
            return True
        if outcome.get("retries", 0) and int(outcome.get("retries", 0)) >= 2:
            return True
    return False


def _load_skill_record(record_id: str):
    """Load a SkillRecord from the index. Returns None on failure.

    Defers the import so this module is testable without the full
    self_improvement stack available.
    """
    try:
        from app.self_improvement.integrator import load_record
        return load_record(record_id)
    except Exception:
        return None


def _hard_archive(record: Any) -> bool:
    """Mark the record as archived in the index. Best-effort.

    Underlying KB metadata is NOT touched — the retriever's blacklist
    filter is what excludes the record from retrieval. This avoids
    poking the four KB-specific store APIs and keeps demotion reversible
    (operator can clear blacklist + status to restore the record).
    """
    try:
        from app.self_improvement.integrator import update_record
        record.status = "archived"
        return bool(update_record(record))
    except Exception:
        return False


# ── Crew domain mapping (mirror retriever) ───────────────────────────

_CREW_TO_DOMAIN: dict[str, str] = {
    "research": "research",
    "researcher": "research",
    "coder": "coding",
    "coding": "coding",
    "writer": "ops",
    "commander": "ops",
    "self_improvement": "evolution",
    "self_improver": "evolution",
    "evaluator": "ops",
    "consolidator": "ops",
}


def _crew_to_domain(crew_name: str) -> str:
    if not crew_name:
        return ""
    return _CREW_TO_DOMAIN.get(crew_name.lower(), "")


def _should_yield_safe() -> bool:
    try:
        from app.idle_scheduler import should_yield
        return bool(should_yield())
    except Exception:
        return False
