"""Metrics aggregation for the Transfer Insight Layer (Phase 17d).

Pure read functions over the JSONL audit trails and the SkillRecord
index. Cheap; no LLM, no Chroma writes. Designed to be called from a
control-plane endpoint or a CLI status command.

Public surface:
    get_overview()              — counts at every layer
    get_by_source_kind()        — per-kind compile + outcome counts
    get_recent_activity(days)   — last-N-days time-bucketed counts
    get_top_performers(n)       — most-surfaced records with no negatives
    get_worst_performers(n)     — records with the most negative-transfer entries
    get_sanitizer_stats()       — hard-reject + demotion totals
    get_promotion_candidates()  — current snapshot from promotion.py
    get_negative_transfer_stats() — tag distribution + recent rows

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


_SHADOW_DRAFTS = "shadow_drafts.jsonl"
_SHADOW_RETRIEVALS = "shadow_retrievals.jsonl"
_NEG_TRANSFER = "negative_transfer.jsonl"
_BLACKLIST = "demotion_blacklist.jsonl"
_PROMOTION_CANDIDATES = "promotion_candidates.jsonl"
_PROMOTION_LOG = "promotion_log.jsonl"


# ── Public API ───────────────────────────────────────────────────────

def get_overview() -> dict:
    """Top-line counts across the pipeline."""
    return {
        "queue": {
            "pending": _safe_count_or_size("queue_size"),
        },
        "compiled": {
            "total_drafts_logged": _line_count(_SHADOW_DRAFTS),
        },
        "kb_records": {
            "active_transfer": _kb_record_count(status="active", transfer_only=True),
            "shadow_transfer": _kb_record_count(status="shadow", transfer_only=True),
            "archived_transfer": _kb_record_count(status="archived", transfer_only=True),
        },
        "demotions": {
            "blacklist_size": len(_read_blacklist()),
            "negative_transfer_logged": _line_count(_NEG_TRANSFER),
        },
        "promotion": {
            "candidates": _line_count(_PROMOTION_CANDIDATES),
            "log_entries": _line_count(_PROMOTION_LOG),
            "auto_promote_enabled": _auto_promote_enabled(),
            "retrieval_enabled": _retrieval_enabled(),
            "shadow_logging_enabled": _shadow_logging_enabled(),
        },
        "shadow_retrieval": {
            "logged_events": _line_count(_SHADOW_RETRIEVALS),
        },
    }


def get_by_source_kind() -> dict:
    """Per-source-kind compile + outcome counters.

    Aggregates from ``shadow_drafts.jsonl``: total compiled, hard-rejected
    (no draft, sanitizer findings), errors (LLM / build failures), and
    average abstraction score.
    """
    rows = _read_jsonl(_SHADOW_DRAFTS)
    by_kind: dict[str, dict] = defaultdict(
        lambda: {
            "total": 0, "compiled": 0, "rejected": 0, "errors": 0,
            "abstraction_sum": 0.0, "leakage_sum": 0.0,
        }
    )
    for r in rows:
        kind = r.get("kind", "unknown")
        bucket = by_kind[kind]
        bucket["total"] += 1
        if r.get("draft"):
            bucket["compiled"] += 1
        elif r.get("error"):
            bucket["errors"] += 1
        else:
            bucket["rejected"] += 1
        bucket["abstraction_sum"] += float(r.get("abstraction_score", 0.0) or 0.0)
        bucket["leakage_sum"] += float(r.get("leakage_risk", 0.0) or 0.0)

    out: dict[str, dict] = {}
    for kind, b in by_kind.items():
        n = max(1, b["total"])
        out[kind] = {
            "total": b["total"],
            "compiled": b["compiled"],
            "rejected": b["rejected"],
            "errors": b["errors"],
            "avg_abstraction": round(b["abstraction_sum"] / n, 3),
            "avg_leakage": round(b["leakage_sum"] / n, 3),
        }
    return out


def get_recent_activity(days: int = 7) -> dict:
    """Compile + retrieval activity over the trailing N days."""
    cutoff = time.time() - max(1, days) * 86400
    drafts = _read_jsonl(_SHADOW_DRAFTS)
    retrievals = _read_jsonl(_SHADOW_RETRIEVALS)
    neg = _read_jsonl(_NEG_TRANSFER)

    return {
        "days": days,
        "drafts_compiled": sum(
            1 for r in drafts
            if _row_ts(r, "compiled_at") >= cutoff and r.get("draft")
        ),
        "drafts_rejected": sum(
            1 for r in drafts
            if _row_ts(r, "compiled_at") >= cutoff
            and not r.get("draft") and not r.get("error")
        ),
        "drafts_errored": sum(
            1 for r in drafts
            if _row_ts(r, "compiled_at") >= cutoff and r.get("error")
        ),
        "shadow_retrievals_logged": sum(
            1 for r in retrievals if _row_ts(r, "ts") >= cutoff
        ),
        "negative_transfers_logged": sum(
            1 for r in neg if _row_ts(r, "ts") >= cutoff
        ),
    }


def get_top_performers(n: int = 10) -> list[dict]:
    """Most-surfaced shadow records that have NO negative-transfer entries.

    Returned shape:
      [{"skill_record_id", "topic", "source_kind", "source_domain",
        "transfer_scope", "surface_count", "abstraction_score"}, ...]
    """
    surface_counts = _surface_counts()
    bad = {r.get("skill_record_id", "") for r in _read_jsonl(_NEG_TRANSFER)}
    if not surface_counts:
        return []

    metadata = _shadow_record_metadata()
    rows: list[dict] = []
    for rid, count in surface_counts.most_common():
        if rid in bad:
            continue
        m = metadata.get(rid, {})
        rows.append({
            "skill_record_id": rid,
            "topic": m.get("topic", "")[:160],
            "source_kind": m.get("source_kind", ""),
            "source_domain": m.get("source_domain", ""),
            "transfer_scope": m.get("transfer_scope", ""),
            "surface_count": count,
            "abstraction_score": float(m.get("abstraction_score", 0.0) or 0.0),
        })
        if len(rows) >= n:
            break
    return rows


def get_worst_performers(n: int = 10) -> list[dict]:
    """Records with the most negative-transfer entries, with their tag mix."""
    neg = _read_jsonl(_NEG_TRANSFER)
    by_record: dict[str, Counter] = defaultdict(Counter)
    for r in neg:
        rid = r.get("skill_record_id", "")
        if rid:
            by_record[rid][r.get("tag", "unknown")] += 1

    if not by_record:
        return []

    metadata = _shadow_record_metadata()
    ranked = sorted(
        by_record.items(),
        key=lambda kv: sum(kv[1].values()),
        reverse=True,
    )[:n]

    out: list[dict] = []
    for rid, tags in ranked:
        m = metadata.get(rid, {})
        out.append({
            "skill_record_id": rid,
            "topic": m.get("topic", "")[:160],
            "source_kind": m.get("source_kind", ""),
            "source_domain": m.get("source_domain", ""),
            "total_failures": sum(tags.values()),
            "tag_mix": dict(tags),
        })
    return out


def get_sanitizer_stats() -> dict:
    """Hard-reject + demotion totals across all compiled rows."""
    rows = _read_jsonl(_SHADOW_DRAFTS)
    hard_rejects = 0
    by_finding: Counter = Counter()
    by_max_scope: Counter = Counter()
    for r in rows:
        for kind, _ in r.get("sanitizer_findings", []) or []:
            by_finding[kind] += 1
            if kind.startswith("hard_reject:"):
                hard_rejects += 1
                break
        scope = r.get("sanitizer_max_scope", "")
        if scope:
            by_max_scope[scope] += 1
    return {
        "total_rows_inspected": len(rows),
        "hard_rejects": hard_rejects,
        "by_finding": dict(by_finding.most_common(20)),
        "by_max_scope": dict(by_max_scope),
    }


def get_promotion_candidates() -> list[dict]:
    return _read_jsonl(_PROMOTION_CANDIDATES)


def get_negative_transfer_stats() -> dict:
    """Tag distribution and 20 most-recent entries."""
    rows = _read_jsonl(_NEG_TRANSFER)
    by_tag: Counter = Counter()
    for r in rows:
        by_tag[r.get("tag", "unknown")] += 1
    recent = sorted(rows, key=lambda r: r.get("ts", 0), reverse=True)[:20]
    return {
        "total": len(rows),
        "by_tag": dict(by_tag),
        "recent": recent,
    }


def get_source_to_target_matrix() -> dict:
    """source_domain × target_domain co-occurrence from shadow retrievals.

    Useful for spotting cross-domain transfer that's actually happening
    (target_domain is the crew's domain at retrieval time; source_domain
    is the record's origin).
    """
    rows = _read_jsonl(_SHADOW_RETRIEVALS)
    matrix: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        target = r.get("target_domain", "") or ""
        for surf in r.get("surfaced", []) or []:
            source = surf.get("source_domain", "") or ""
            if source and target:
                matrix[source][target] += 1
    return {src: dict(targets) for src, targets in matrix.items()}


# ── Internals ────────────────────────────────────────────────────────

def _resolve_dir() -> Path:
    from app.transfer_memory.queue import _resolve_dir as base_dir, _ensure_dir
    _ensure_dir()
    return base_dir()


def _read_jsonl(filename: str) -> list[dict]:
    p = _resolve_dir() / filename
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _line_count(filename: str) -> int:
    p = _resolve_dir() / filename
    if not p.exists():
        return 0
    try:
        with p.open("r", encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


def _safe_count_or_size(kind: str) -> int:
    """Defer queue size lookups so this module's import has no Chroma cost."""
    if kind == "queue_size":
        try:
            from app.transfer_memory.queue import queue_size
            return int(queue_size())
        except Exception:
            return 0
    return 0


def _row_ts(row: dict, field: str) -> float:
    try:
        return float(row.get(field, 0.0) or 0.0)
    except Exception:
        return 0.0


def _read_blacklist() -> set[str]:
    p = _resolve_dir() / _BLACKLIST
    if not p.exists():
        return set()
    try:
        with p.open("r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()


def _surface_counts() -> Counter:
    """Aggregate skill_record_id surface counts from shadow_retrievals."""
    counts: Counter = Counter()
    for r in _read_jsonl(_SHADOW_RETRIEVALS):
        for surf in r.get("surfaced", []) or []:
            rid = surf.get("skill_record_id")
            if rid:
                counts[rid] += 1
    return counts


def _shadow_record_metadata() -> dict[str, dict]:
    """Map record_id → draft metadata harvested from shadow_drafts.jsonl.

    The shadow log is the cheap source of truth for record metadata;
    avoiding a Chroma lookup keeps the dashboard fast even with
    thousands of rows.
    """
    meta: dict[str, dict] = {}
    for r in _read_jsonl(_SHADOW_DRAFTS):
        d = r.get("draft") or {}
        rid = d.get("id")
        if not rid:
            continue
        meta[rid] = {
            "topic": d.get("topic", ""),
            "source_kind": d.get("source_kind", ""),
            "source_domain": d.get("source_domain", ""),
            "transfer_scope": d.get("transfer_scope", ""),
            "abstraction_score": d.get("abstraction_score", 0.0),
        }
    return meta


def _kb_record_count(status: str, transfer_only: bool = True) -> int:
    """Count SkillRecords from the index with the given status.

    When ``transfer_only`` is True, requires the record to carry a
    transfer-memory provenance marker (``transfer_scope`` set).
    """
    try:
        from app.self_improvement.integrator import list_records
        records = list_records(status=status, limit=2000)
    except Exception:
        return 0
    if not transfer_only:
        return len(records)
    return sum(
        1 for r in records
        if (r.provenance or {}).get("transfer_scope")
    )


def _retrieval_enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(getattr(get_settings(), "transfer_memory_retrieval_enabled", False))
    except Exception:
        return False


def _shadow_logging_enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(getattr(get_settings(), "transfer_memory_shadow_logging_enabled", True))
    except Exception:
        return True


def _auto_promote_enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(getattr(get_settings(), "transfer_memory_auto_promote_enabled", False))
    except Exception:
        return False
