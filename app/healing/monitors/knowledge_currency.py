"""knowledge_currency — Surfaces stagnant knowledge bases.

Gap #10 (2026-05-24): the seven KBs (memory / episteme / experiential
/ philosophy / aesthetics / tensions / knowledge) accumulate over
time. There's no audit of *whether they keep growing*. A KB whose
median row is 4 years old AND whose last addition was 8 months ago is
dead weight — the system still queries it but the corpus is frozen at
a snapshot that may no longer reflect reality.

What we measure
===============

For each KB, from its source_ledger (``workspace/<kb>/.source_ledger.jsonl``):

  * **n_rows** — total ledger rows.
  * **median_age_days** — median age of rows.
  * **p10 / p90** — distribution shape.
  * **last_add_age_days** — days since most recent row.

Stagnation criterion (operator-tunable):

  * ``n_rows ≥ 10`` AND
  * ``median_age_days > 365`` AND
  * ``last_add_age_days > 180``

All three required. n=10 floor avoids alerting on a never-populated
KB; the median + last-add jointly require the corpus to be old AND
not freshening.

Why source_ledger not chromadb
==============================

ChromaDB collection metadata is heterogeneous across KBs (some include
``timestamp``, some don't). The source_ledger (§56) standardises this:
every row has a ``ts`` field by construction. Same approach as the
existing ``bit_rot_scan`` and ``embedding_drift`` monitors.

What this is NOT
================

  * Not a quality scorer. Stagnation = absence of new content, not
    incorrect content. The ``kb_contradiction`` monitor handles the
    latter.
  * Not a deletion proposer. Stagnant KBs are surfaced to the operator
    who decides whether to retire / repopulate / leave alone.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


NAME = "knowledge_currency"
CADENCE_SECONDS = 24 * 3600
MASTER_SWITCH_KEY = "knowledge_currency_monitor_enabled"

_INTERNAL_CADENCE_S = 7 * 24 * 3600
_DEDUP_WINDOW_S = 28 * 86400
_STATE_FILE_NAME = "knowledge_currency_state.json"

_KBS = (
    "memory",
    "episteme",
    "experiential",
    "philosophy",
    "aesthetics",
    "tensions",
    "knowledge",
)

_MIN_ROWS_FOR_STAGNATION = 10
_MEDIAN_AGE_DAYS_STAGNATION = 365
_LAST_ADD_DAYS_STAGNATION = 180


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_knowledge_currency_monitor_enabled
        return get_knowledge_currency_monitor_enabled()
    except Exception:
        return os.getenv(
            "KNOWLEDGE_CURRENCY_MONITOR_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _state_path() -> Path:
    return _workspace() / "healing" / _STATE_FILE_NAME


def _ledger_path(kb: str) -> Path:
    return _workspace() / kb / ".source_ledger.jsonl"


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"last_run_at": 0.0, "last_alert_at": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": 0.0, "last_alert_at": {}}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8",
        )
    except Exception:
        logger.debug("knowledge_currency: state write failed", exc_info=True)


# ── Data shape ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KbCurrency:
    kb: str
    n_rows: int
    last_add_age_days: Optional[float]
    median_age_days: Optional[float]
    p10_age_days: Optional[float]
    p90_age_days: Optional[float]
    is_stagnant: bool


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    n = len(sorted_values)
    k = (n - 1) * pct
    f = int(k)
    c = min(f + 1, n - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _read_ledger_timestamps(kb: str) -> list[float]:
    path = _ledger_path(kb)
    if not path.exists():
        return []
    out: list[float] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = row.get("ts")
                if isinstance(ts, (int, float)):
                    out.append(float(ts))
    except OSError:
        return out
    return out


def _compute_currency(kb: str, *, now: float) -> KbCurrency:
    tss = _read_ledger_timestamps(kb)
    if not tss:
        return KbCurrency(
            kb=kb,
            n_rows=0,
            last_add_age_days=None,
            median_age_days=None,
            p10_age_days=None,
            p90_age_days=None,
            is_stagnant=False,
        )
    ages_days = sorted((now - ts) / 86400.0 for ts in tss)
    n = len(ages_days)
    last_add_age = (now - max(tss)) / 86400.0
    median = _percentile(ages_days, 0.5)
    p10 = _percentile(ages_days, 0.1)
    p90 = _percentile(ages_days, 0.9)

    stagnant = (
        n >= _MIN_ROWS_FOR_STAGNATION
        and median is not None
        and median > _MEDIAN_AGE_DAYS_STAGNATION
        and last_add_age > _LAST_ADD_DAYS_STAGNATION
    )

    return KbCurrency(
        kb=kb,
        n_rows=n,
        last_add_age_days=round(last_add_age, 2),
        median_age_days=round(median, 2) if median is not None else None,
        p10_age_days=round(p10, 2) if p10 is not None else None,
        p90_age_days=round(p90, 2) if p90 is not None else None,
        is_stagnant=stagnant,
    )


def compute(*, now: Optional[float] = None) -> dict[str, Any]:
    cur = float(now) if now is not None else time.time()
    items = [_compute_currency(kb, now=cur) for kb in _KBS]
    return {
        "as_of": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
        "kbs": [asdict(c) for c in items],
        "stagnant_kbs": [c.kb for c in items if c.is_stagnant],
    }


def briefing_section() -> str:
    """One-line summary for the weekly briefing composer. Empty string
    when nothing actionable."""
    result = compute()
    if not result["stagnant_kbs"]:
        return ""
    return (
        "📚 **Stagnant knowledge bases**: "
        + ", ".join(f"`{kb}`" for kb in result["stagnant_kbs"])
        + " — median row >1yr old + no addition in 6+ months. "
        "Consider repopulating or retiring."
    )


def _emit_alert(state: dict[str, Any], result: dict[str, Any], *, now: float) -> bool:
    stagnant = result["stagnant_kbs"]
    if not stagnant:
        return False
    last_alerts = state.setdefault("last_alert_at", {})
    if not isinstance(last_alerts, dict):
        last_alerts = {}
        state["last_alert_at"] = last_alerts
    key = ",".join(sorted(stagnant))
    last = float(last_alerts.get(key, 0))
    if now - last < _DEDUP_WINDOW_S:
        return False
    last_alerts[key] = now
    body = (
        f"📚 {len(stagnant)} stagnant knowledge base(s): "
        + ", ".join(f"`{kb}`" for kb in stagnant)
        + ".\n\n"
        "Each has ≥10 rows, median age > 1 year, and no additions in "
        "6+ months. Options: repopulate (re-run the relevant ingest "
        "pipeline), retire (drop the KB if it's no longer relevant), "
        "or accept (some KBs are reference corpora that don't grow)."
    )
    try:
        from app.notify import notify
        notify(
            title=f"📚 Stagnant KBs: {len(stagnant)}",
            body=body,
            url="/cp/monitor",
            topic=f"knowledge_currency:{key[:64]}",
            critical=False,
            arbitrate=True,
        )
        return True
    except Exception:
        logger.debug("knowledge_currency: notify failed", exc_info=True)
        return False


def run(*, now: Optional[float] = None) -> dict[str, Any]:
    if not _enabled():
        return {"ran": False, "skipped": True}

    cur = float(now) if now is not None else time.time()
    state = _read_state()
    last = float(state.get("last_run_at", 0))
    if last > 0 and cur - last < _INTERNAL_CADENCE_S:
        return {"ran": False}

    state["last_run_at"] = cur

    result = compute(now=cur)
    alert_sent = _emit_alert(state, result, now=cur)
    _write_state(state)

    return {
        "ran": True,
        "as_of": result["as_of"],
        "kbs": result["kbs"],
        "stagnant_kbs": result["stagnant_kbs"],
        "alert_sent": alert_sent,
    }


__all__ = ["run", "compute", "briefing_section", "KbCurrency"]
