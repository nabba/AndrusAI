"""History / chronicle topic — recent task activity + system narrative.

Surfaces what the system has actually been doing: recent task records
(from conversation_store), the system_chronicle excerpt (operational
narrative), and recent error/audit entries.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def gather(*, recent_hours: int = 24, max_tasks: int = 12,
           chronicle_chars: int = 1200) -> dict:
    out: dict = {
        "recent_tasks": [],
        "task_summary": {},
        "chronicle_excerpt": "",
        "recent_errors": [],
        "narrative_audit_recent": [],
    }

    # ── Task statistics ────────────────────────────────────────────
    try:
        from app.conversation_store import count_recent_tasks, avg_response_time
        total, successful = count_recent_tasks(hours=recent_hours)
        out["task_summary"] = {
            "hours_window": recent_hours,
            "total_tasks": total,
            "successful": successful,
            "success_rate": round(successful / max(1, total), 3),
            "avg_response_time_s": round(
                float(avg_response_time(hours=recent_hours) or 0.0), 2
            ),
        }
    except Exception as exc:
        logger.debug("introspection.chronicle: task summary failed: %s", exc)

    # ── System chronicle excerpt ───────────────────────────────────
    for path_candidate in (
        "/app/workspace/system_chronicle.md",
        "workspace/system_chronicle.md",
    ):
        try:
            p = Path(path_candidate)
            if p.exists():
                txt = p.read_text(encoding="utf-8", errors="ignore")
                out["chronicle_excerpt"] = txt[-chronicle_chars:].strip()
                break
        except Exception:
            continue

    # ── Recent errors (causal evidence) ────────────────────────────
    try:
        from app.error_handler import recent_errors
        errs = recent_errors(hours=recent_hours, limit=8) or []
        out["recent_errors"] = [
            {
                "kind": (e or {}).get("error_type", "unknown"),
                "context": str((e or {}).get("context", ""))[:140],
                "ts": (e or {}).get("timestamp", ""),
            }
            for e in errs
        ]
    except Exception as exc:
        logger.debug("introspection.chronicle: error journal failed: %s", exc)

    # ── Narrative audit (drift findings, corrections, etc.) ────────
    try:
        from app.subia.safety.narrative_audit import read_audit_entries
        entries = read_audit_entries(limit=10) or []
        out["narrative_audit_recent"] = [
            {
                "at": getattr(e, "at", ""),
                "severity": getattr(e, "severity", "info"),
                "finding": str(getattr(e, "finding", ""))[:200],
            }
            for e in entries
        ]
    except Exception as exc:
        logger.debug("introspection.chronicle: audit gather failed: %s", exc)

    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Recent activity / chronicle (operational narrative)"]
    s = data.get("task_summary", {}) or {}
    if s:
        lines.append(
            f"Tasks in last {s.get('hours_window',24)}h: "
            f"{s.get('total_tasks',0)} total, "
            f"{s.get('successful',0)} succeeded "
            f"({s.get('success_rate',0)*100:.0f}% success rate, "
            f"avg latency {s.get('avg_response_time_s',0)}s)"
        )
    if data.get("chronicle_excerpt"):
        lines.append("System chronicle excerpt (most recent operational notes):")
        for cl in data["chronicle_excerpt"].splitlines()[-15:]:
            lines.append(f"  {cl}")
    if data.get("recent_errors"):
        lines.append("Recent errors (causal evidence for any frustration / overload):")
        for e in data["recent_errors"][:5]:
            lines.append(
                f"  - [{e.get('kind','?')}] {e.get('context','')[:100]}"
            )
    if data.get("narrative_audit_recent"):
        lines.append("Narrative audit recent entries (Tier-3 immutable log):")
        for ae in data["narrative_audit_recent"][:5]:
            lines.append(
                f"  - {ae['at']} [{ae['severity']}] {ae['finding'][:140]}"
            )
    return "\n".join(lines)
