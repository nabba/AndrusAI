"""decade_recall.retrieval — scope-filterable token-overlap search.

Gap 4 of the 2026-05-24 ultrathink analysis closure.

Same retrieval model as conversation_memory (Q17.8): token overlap +
recency. The new dimension is ``scope``, which filters by source.

Public API
==========

  * ``recall_history(query, scopes=None, window_years=10, top_k=10)``
    — primary entry. Returns top-K AuditReference rows newest-first.
  * ``summary(scopes=None, window_years=10)`` — counts per scope per
    year, used by the daily briefing's annual-arc surface.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_DEFAULT_TOP_K = 10
_DEFAULT_WINDOW_YEARS = 10


def _workspace_root() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _index_path() -> Path:
    return _workspace_root() / "decade_recall" / "index.jsonl"


def _enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_decade_recall_enabled())
    except Exception:
        return True


@dataclass
class AuditReference:
    """One hit from the index — newest-first by ts."""

    ts: str
    scope: str
    kind: str
    preview: str
    ref: str | None
    score: float
    tokens_matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "scope": self.scope,
            "kind": self.kind,
            "preview": self.preview,
            "ref": self.ref,
            "score": round(float(self.score), 4),
            "tokens_matched": self.tokens_matched,
        }


def _query_tokens(query: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(query or "")}


def _score_row(query_set: set[str], tokens: list[str]) -> tuple[float, list[str]]:
    if not query_set or not tokens:
        return 0.0, []
    row_set = set(tokens)
    matched = list(query_set & row_set)
    if not matched:
        return 0.0, []
    overlap = len(matched) / max(1, len(query_set))
    density = len(matched) / max(1, len(tokens))
    return (overlap * 0.7) + (density * 0.3), matched


def recall_history(
    query: str,
    *,
    scopes: list[str] | None = None,
    window_years: int = _DEFAULT_WINDOW_YEARS,
    top_k: int = _DEFAULT_TOP_K,
    kinds: set[str] | None = None,
) -> list[AuditReference]:
    """Search the unified index. Returns newest-first by ts.

    ``scopes`` filters by source (continuity / changes / drills /
    executor / agreement / governance). ``None`` means all.
    ``kinds`` filters by the per-source event kind (e.g. only
    'cr_applied' rows within scope='changes').
    """
    if not _enabled():
        return []
    p = _index_path()
    if not p.exists():
        return []
    qset = _query_tokens(query)
    if not qset:
        return []

    scope_filter = set(scopes) if scopes else None
    cutoff_iso = (
        datetime.now(timezone.utc)
        - timedelta(days=int(window_years * 365.25))
    ).isoformat()

    scored: list[AuditReference] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = str(row.get("ts") or "")
                if ts and ts < cutoff_iso:
                    continue
                scope = str(row.get("scope") or "")
                if scope_filter and scope not in scope_filter:
                    continue
                kind = str(row.get("kind") or "")
                if kinds and kind not in kinds:
                    continue
                tokens = list(row.get("tokens") or [])
                score, matched = _score_row(qset, tokens)
                if score <= 0.0:
                    continue
                scored.append(
                    AuditReference(
                        ts=ts,
                        scope=scope,
                        kind=kind,
                        preview=str(row.get("preview") or ""),
                        ref=row.get("ref"),
                        score=score,
                        tokens_matched=matched,
                    )
                )
    except OSError:
        logger.debug("decade_recall: read failed", exc_info=True)
        return []

    # Newest-first by ts. Ties broken by score descending.
    scored.sort(key=lambda r: (r.ts, r.score), reverse=True)
    return scored[:top_k]


def summary(
    *,
    scopes: list[str] | None = None,
    window_years: int = _DEFAULT_WINDOW_YEARS,
) -> dict[str, Any]:
    """Counts per scope per year. Used by the briefing's annual-arc
    surface and the operator dashboard."""
    if not _enabled():
        return {"skipped_reason": "master_switch_off"}
    p = _index_path()
    if not p.exists():
        return {"total": 0, "by_scope_year": {}}
    scope_filter = set(scopes) if scopes else None
    cutoff_iso = (
        datetime.now(timezone.utc)
        - timedelta(days=int(window_years * 365.25))
    ).isoformat()
    total = 0
    by_scope_year: dict[str, dict[str, int]] = {}
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = str(row.get("ts") or "")
                if ts and ts < cutoff_iso:
                    continue
                scope = str(row.get("scope") or "")
                if scope_filter and scope not in scope_filter:
                    continue
                year = ts[:4] if ts else "unknown"
                by_scope_year.setdefault(scope, {}).setdefault(year, 0)
                by_scope_year[scope][year] += 1
                total += 1
    except OSError:
        return {"total": 0, "by_scope_year": {}}
    return {"total": total, "by_scope_year": by_scope_year}


__all__ = ["AuditReference", "recall_history", "summary"]
