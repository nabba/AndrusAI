"""Per-day fusion spend cap.

A small UTC-day-keyed JSON counter at ``workspace/fusion/daily_spend.json``.
Fusion is a 4–5× cost multiplier, so this is a dedicated guardrail *under* the
monthly total-cost ceiling, not a billing ledger — approximate (estimate-based,
counted at plan time) accounting is deliberate and conservative (a failed call
still counts, which can only make the cap fire sooner).

Persisted on disk (not in-process) so the cap survives the frequent gateway
restarts this deployment sees.
"""

from __future__ import annotations

import datetime
import json
import threading

from app.paths import WORKSPACE_ROOT

_LOCK = threading.Lock()
_FILE = WORKSPACE_ROOT / "fusion" / "daily_spend.json"


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _read() -> dict:
    try:
        data = json.loads(_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def spent_today() -> float:
    """USD estimated to have been spent on fusion so far today (UTC)."""
    data = _read()
    if data.get("date") != _today():
        return 0.0
    try:
        return float(data.get("spent_usd", 0.0))
    except (TypeError, ValueError):
        return 0.0


def record_spend(est_usd: float) -> None:
    """Add *est_usd* to today's running total (rolls over at UTC midnight)."""
    try:
        amount = float(est_usd)
    except (TypeError, ValueError):
        return
    if amount <= 0:
        return
    with _LOCK:
        data = _read()
        if data.get("date") != _today():
            data = {"date": _today(), "spent_usd": 0.0}
        try:
            data["spent_usd"] = float(data.get("spent_usd", 0.0)) + amount
        except (TypeError, ValueError):
            data["spent_usd"] = amount
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(_FILE)
        except Exception:
            pass


def under_cap(cap_usd: float) -> bool:
    """True when today's fusion spend is below *cap_usd*.

    A cap of 0 (or negative) means "no fusion-specific cap" → always True;
    other ceilings still apply.
    """
    try:
        if cap_usd is None or float(cap_usd) <= 0:
            return True
        return spent_today() < float(cap_usd)
    except Exception:
        return True
