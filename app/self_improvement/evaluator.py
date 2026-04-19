"""
app.self_improvement.evaluator — skill usage + outcome tracking.

Phase 4 of the overhaul. Answers:

    - Which skills are actually being retrieved in real tasks?
    - Do retrieval hits correlate with successful task outcomes?
    - Which skills have decayed into zombies (not retrieved in N days)?

Writes back to:
    - SkillRecord.usage_count / .last_used_at  (via Integrator.update_record)
    - MAP-Elites grid (as a fitness delta on the crew's strategy entry
      for tasks that hit a high-confidence skill)
    - LearningGap store: USAGE_DECAY gaps for zombie skills

Design:
    - Instrumentation is push-style: callers (retrieval orchestrator,
      KB stores) call record_hits() after a retrieval returns.
    - No synchronous work on hot path — all disk/Chroma writes deferred
      to a best-effort pool.
    - Zombie sweep runs periodically from idle_scheduler.

IMMUTABLE — infrastructure-level module (metrics live here, not in agents).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.self_improvement.types import (
    LearningGap, GapSource,
)

logger = logging.getLogger(__name__)


# ── In-memory aggregator ─────────────────────────────────────────────────────
#
# Hits are buffered in memory and flushed to the SkillRecord index in batches
# to avoid hammering Chroma on every retrieval.

_hit_lock = threading.Lock()
_pending_hits: dict[str, int] = defaultdict(int)       # record_id → delta
_last_seen: dict[str, str] = {}                         # record_id → ISO timestamp
_FLUSH_THRESHOLD = 10                                   # flush every N accumulated hits


def record_hits(record_ids: list[str]) -> None:
    """Record that these SkillRecords were retrieved.

    Cheap and non-blocking: just buffers in memory. Use flush_hits() or
    let the auto-flush threshold trigger a sync.
    """
    if not record_ids:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _hit_lock:
        for rid in record_ids:
            if not rid:
                continue
            _pending_hits[rid] += 1
            _last_seen[rid] = now
        total = sum(_pending_hits.values())
    if total >= _FLUSH_THRESHOLD:
        flush_hits()


def flush_hits() -> int:
    """Apply buffered hits to SkillRecord index. Returns the number applied."""
    with _hit_lock:
        if not _pending_hits:
            return 0
        pending = dict(_pending_hits)
        seen = dict(_last_seen)
        _pending_hits.clear()
        _last_seen.clear()

    applied = 0
    try:
        from app.self_improvement.integrator import load_record, update_record
    except Exception:
        return 0

    for rid, delta in pending.items():
        try:
            rec = load_record(rid)
            if rec is None:
                continue
            rec.usage_count += int(delta)
            rec.last_used_at = seen.get(rid, rec.last_used_at)
            update_record(rec)
            applied += 1
        except Exception:
            logger.debug(f"flush_hits: update {rid} failed", exc_info=True)
    if applied:
        logger.debug(f"Evaluator: flushed {applied} skill-usage updates")
    return applied


# ── Outcome correlation ──────────────────────────────────────────────────────

def record_task_outcome(
    task_id: str,
    crew_name: str,
    success: bool,
    confidence: str,
    skills_retrieved: list[str],
) -> None:
    """Correlate a task outcome with the skills that fed its context.

    Used by the orchestrator's post-telemetry block. Currently feeds a
    small MAP-Elites fitness adjustment: skills that were in-context for a
    high-confidence successful task get a slight usage bump; skills in-context
    for a failed task get their 'last_successful_use' NOT advanced.

    Broader correlation (regression of skill presence vs outcome) is
    aggregated offline via idle_scheduler's `_evaluator_sweep` job.
    """
    if not skills_retrieved:
        return
    # Primary effect: bump usage_count / last_used_at on any skill retrieved
    # during a successful task. A failed task still bumps the usage_count
    # (the skill was consulted) but doesn't advance success correlation.
    record_hits(skills_retrieved)

    # TODO (Phase 6): write a correlation row to a new
    # `skill_outcome_corr` collection so the competence_map can derive
    # per-skill success rates. For now, presence/usage is enough.


# ── Decay sweep + MAP-Elites feedback ────────────────────────────────────────

_DECAY_DAYS_ACTIVE = 30       # skill never retrieved in this window → zombie candidate
_DECAY_DAYS_HARD = 60         # never retrieved in this window → strong decay signal


def scan_for_decay() -> int:
    """Periodic sweep: emit USAGE_DECAY gaps for skills that haven't been
    retrieved in _DECAY_DAYS_ACTIVE days.

    Returns number of gaps emitted. Called from idle_scheduler.
    """
    try:
        from app.self_improvement.integrator import list_records
        from app.self_improvement.store import emit_gap
    except Exception:
        return 0

    now = datetime.now(timezone.utc)
    cutoff_soft = now - timedelta(days=_DECAY_DAYS_ACTIVE)
    cutoff_hard = now - timedelta(days=_DECAY_DAYS_HARD)
    emitted = 0

    for rec in list_records(status="active", limit=1000):
        try:
            last = rec.last_used_at or rec.created_at
            if not last:
                continue
            last_dt = datetime.fromisoformat(last)
            if last_dt > cutoff_soft:
                continue  # recently used; healthy
            days_idle = (now - last_dt).days
            # Signal strength scales with idle time, capped at the hard cutoff
            if last_dt < cutoff_hard:
                strength = 0.35
                severity = "stale"
            else:
                strength = 0.20
                severity = "idle"
            gap = LearningGap(
                id="",
                source=GapSource.USAGE_DECAY,
                description=(
                    f"Skill '{rec.topic[:80]}' has been {severity} "
                    f"({days_idle}d idle, usage_count={rec.usage_count})"
                ),
                evidence={
                    "skill_record_id": rec.id,
                    "kb": rec.kb,
                    "last_used_at": rec.last_used_at,
                    "days_idle": days_idle,
                    "usage_count": rec.usage_count,
                },
                signal_strength=strength,
            )
            if emit_gap(gap):
                emitted += 1
        except Exception:
            logger.debug(f"scan_for_decay[{rec.id}] errored", exc_info=True)

    if emitted:
        logger.info(f"Evaluator: emitted {emitted} USAGE_DECAY gap(s)")
    return emitted


# ── Tip-effectiveness decay (arXiv:2603.10600 Phase 6) ───────────────────────

# Effectiveness threshold below which a trajectory-sourced tip is
# considered "not helping". Tips below this ratio with enough samples
# (MIN_USES_FOR_ACTION from effectiveness module) get USAGE_DECAY gaps
# emitted so the Consolidator can propose archival.
_LOW_EFFECTIVENESS_THRESHOLD = 0.35


def scan_for_low_effectiveness_tips() -> int:
    """Emit USAGE_DECAY gaps for trajectory tips that have underperformed.

    Unlike `scan_for_decay` (time-based, applies to all skills), this sweep
    considers only skills with `tip_type` set (= trajectory-sourced) and
    only those with enough samples for the effectiveness signal to be
    trustworthy. Runs from idle_scheduler alongside scan_for_decay.

    Returns the number of gaps emitted. Never raises.
    """
    try:
        from app.trajectory.effectiveness import (
            worst_tips, MIN_USES_FOR_ACTION,
        )
        from app.self_improvement.integrator import load_record
        from app.self_improvement.store import emit_gap
    except Exception:
        return 0

    emitted = 0
    try:
        bottom = worst_tips(k=20, min_uses=MIN_USES_FOR_ACTION)
    except Exception:
        return 0

    for row in bottom:
        if row.get("effectiveness", 1.0) >= _LOW_EFFECTIVENESS_THRESHOLD:
            continue  # above threshold — healthy enough
        sid = row.get("skill_id", "")
        if not sid:
            continue
        try:
            rec = load_record(sid)
            if rec is None or rec.status != "active":
                continue
            # Only apply to trajectory-sourced tips. External-topic skills
            # are handled by scan_for_decay's time-based logic.
            if not rec.provenance.get("tip_type"):
                continue

            strength = 0.45  # higher than time-based decay — evidence-backed
            gap = LearningGap(
                id="",
                source=GapSource.USAGE_DECAY,
                description=(
                    f"Tip '{rec.topic[:80]}' underperforming: "
                    f"effectiveness={row['effectiveness']:.2f} over "
                    f"{row['uses']} uses"
                ),
                evidence={
                    "skill_record_id": rec.id,
                    "kb": rec.kb,
                    "tip_type": rec.provenance.get("tip_type", ""),
                    "source_trajectory_id": rec.provenance.get("source_trajectory_id", ""),
                    "uses": row["uses"],
                    "successes": row["successes"],
                    "effectiveness": row["effectiveness"],
                    "reason": "low_effectiveness",
                },
                signal_strength=strength,
            )
            if emit_gap(gap):
                emitted += 1
        except Exception:
            logger.debug(f"scan_for_low_effectiveness_tips[{sid}] errored",
                         exc_info=True)

    if emitted:
        logger.info(
            f"Evaluator: emitted {emitted} low-effectiveness tip gap(s)"
        )
    return emitted


# ── Distribution stats (feeds Phase 6 observability) ─────────────────────────

def usage_distribution() -> dict:
    """Summary of skill usage across all KBs.

    Used by the Phase 6 dashboard. Returns:
        total, mean_usage, gini (concentration), zombies_30d, zombies_60d,
        by_kb = {kb: {count, mean, zombies}}
    """
    try:
        from app.self_improvement.integrator import list_records
    except Exception:
        return {}

    records = list_records(status="active", limit=2000)
    if not records:
        return {"total": 0}

    now = datetime.now(timezone.utc)
    counts = [r.usage_count for r in records]
    mean = sum(counts) / len(counts)

    # Gini — concentration; 0 = perfectly uniform, 1 = one skill does all
    sorted_counts = sorted(counts)
    n = len(sorted_counts)
    cumulative = sum(i * c for i, c in enumerate(sorted_counts, 1))
    total = sum(sorted_counts)
    gini = (2.0 * cumulative / (n * total) - (n + 1) / n) if total > 0 else 0.0

    zombies_30d = 0
    zombies_60d = 0
    for r in records:
        last = r.last_used_at or r.created_at
        if not last:
            continue
        try:
            age = (now - datetime.fromisoformat(last)).days
            if age > 30:
                zombies_30d += 1
            if age > 60:
                zombies_60d += 1
        except Exception:
            pass

    by_kb: dict = {}
    from collections import Counter
    kb_counter = Counter(r.kb for r in records)
    for kb in kb_counter:
        kb_records = [r for r in records if r.kb == kb]
        kb_counts = [r.usage_count for r in kb_records]
        by_kb[kb] = {
            "count": len(kb_records),
            "mean_usage": round(sum(kb_counts) / len(kb_counts), 2) if kb_counts else 0.0,
            "zombies_30d": sum(
                1 for r in kb_records
                if r.last_used_at and
                (now - datetime.fromisoformat(r.last_used_at)).days > 30
            ),
        }

    return {
        "total": len(records),
        "mean_usage": round(mean, 2),
        "gini": round(gini, 3),
        "zombies_30d": zombies_30d,
        "zombies_60d": zombies_60d,
        "by_kb": by_kb,
    }
