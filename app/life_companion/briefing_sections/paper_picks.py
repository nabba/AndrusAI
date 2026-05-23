"""paper_picks — non-codeable but relevant paper-pipeline rows.

The existing briefing has a `_gather_codeable_papers` section that
surfaces papers the LLM marked actionable. This candidate surfaces
the *other* side — high-relevance papers the LLM declined as
non-codeable (theoretical, hardware-specific, etc.) which are still
worth knowing about.

Reads ``workspace/proposed_experiments.jsonl`` (paper_pipeline +
news_pipeline append to it; we filter kind != news + codeable false +
relevance >= 0.6).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ID = "paper-picks"
DISPLAY_NAME = "📚 Papers worth reading"
DESCRIPTION = (
    "High-relevance research papers the LLM marked non-codeable "
    "(theoretical / hardware-specific). Complements the existing "
    "codeable-papers section."
)

_LEDGER = "/app/workspace/proposed_experiments.jsonl"
_MAX_LINES = 3
_MIN_RELEVANCE = 0.6


def _ledger_path() -> Path:
    p = Path(_LEDGER)
    if p.exists():
        return p
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "proposed_experiments.jsonl"


def gather() -> list[str]:
    p = _ledger_path()
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            if r.get("kind") == "news":
                continue
            if r.get("codeable"):
                continue
            try:
                rel = float(r.get("relevance") or 0.0)
            except (TypeError, ValueError):
                rel = 0.0
            if rel < _MIN_RELEVANCE:
                continue
            r["_relevance"] = rel
            rows.append(r)
            if len(rows) >= _MAX_LINES:
                break
    except OSError:
        logger.debug("paper_picks: read failed", exc_info=True)
        return []
    if not rows:
        return []
    out: list[str] = []
    for r in rows:
        title = (r.get("title") or "")[:90]
        out.append(f"  • {title}  (rel {r['_relevance']:.2f})")
        implications = r.get("implications") or []
        if isinstance(implications, list) and implications:
            takeaway = str(implications[0])[:130]
            if takeaway:
                out.append(f"     → {takeaway}")
    return out
