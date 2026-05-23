"""JSONL spend ledger for the connector-budget primitive.

Persistence at ``workspace/connector_budget/spend.jsonl`` — append-only,
one row per recorded call. Rows look like::

    {"connector": "clearbit", "ts": "2026-05-22T12:00:00+00:00",
     "usd": 0.05, "estimated": false}

``today_spend(name)`` reads the file tail and sums rows whose
``ts`` falls in the current UTC day. The implementation does a linear
scan; for a single-host gateway shipping maybe a few hundred external
calls per day the file stays small (rotation is left as future work).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Single per-process lock guards append + read; the ledger is single-
# host so a coarse threading lock is fine. (A future Postgres-backed
# implementation would swap this for atomic row-level locking.)
_WRITE_LOCK = threading.Lock()

# Phase B.3 cleanup (2026-05-22) — internal arithmetic uses Decimal
# at 6-decimal-place quantization. Public API still accepts floats;
# we promote to Decimal for comparisons + sums so cap-at-boundary
# behavior is exact (no more 19 * 0.005 != 0.095 FP drift). The
# 6-decimal precision matches the round(..., 6) already in the
# /state endpoint response.
_USD_QUANT = Decimal("0.000001")


def _to_decimal(value) -> Decimal:
    """Stable float → Decimal conversion via repr (rounds-trips
    through the shortest decimal representation rather than the
    full binary expansion). Quantized to 6 decimal places.

    Examples:
        _to_decimal(0.005) == Decimal("0.005000")  # not 0.005000000...20816...
        _to_decimal(0.10) == Decimal("0.100000")
    """
    return Decimal(repr(float(value))).quantize(_USD_QUANT, ROUND_HALF_UP)


def _ledger_dir() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        p = WORKSPACE_ROOT / "connector_budget"
    except Exception:
        p = Path("workspace") / "connector_budget"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ledger_path() -> Path:
    return _ledger_dir() / "spend.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _today_utc_date() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def record_spend(
    connector: str,
    usd: float,
    *,
    estimated: bool = False,
) -> None:
    """Append a spend row. Failure-isolated.

    ``estimated=True`` flags rows where the actual cost wasn't
    extractable from the call result and the decorator fell back to
    the pre-declared estimate. Operators can use this flag in audits
    to spot connectors whose cost should be metered more precisely.
    """
    row = {
        "connector": connector,
        "ts": _now_iso(),
        "usd": float(usd),
        "estimated": bool(estimated),
    }
    try:
        with _WRITE_LOCK:
            with _ledger_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        logger.debug(
            "connector_budget: record_spend failed", exc_info=True,
        )


def today_spend(connector: str) -> float:
    """Sum of recorded USD for ``connector`` in the current UTC day.

    Linear scan of the ledger; returns 0.0 when the file is absent or
    unreadable.
    """
    path = _ledger_path()
    if not path.exists():
        return 0.0
    today = _today_utc_date()
    # Decimal accumulation — see Phase B.3 docstring at top of file.
    total = Decimal("0")
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("connector") != connector:
                    continue
                ts = str(row.get("ts", ""))
                if not ts.startswith(today):
                    continue
                try:
                    total += _to_decimal(row.get("usd", 0.0))
                except (TypeError, ValueError):
                    continue
    except Exception:
        logger.debug(
            "connector_budget: today_spend read failed", exc_info=True,
        )
        return 0.0
    # Return float at the public boundary — callers haven't seen
    # Decimal change. The 6-decimal quantization preserved through
    # the sum survives the float() cast for any reasonable cap value.
    return float(total)


def today_calls(connector: str) -> int:
    """Count rows recorded for ``connector`` in the current UTC day.

    Phase B.4 (2026-05-22) — companion to ``today_spend`` for the
    call-count-cap mode of ``@with_connector_budget``. Linear scan,
    failure-isolated. Returns 0 when the file is absent or unreadable.
    """
    path = _ledger_path()
    if not path.exists():
        return 0
    today = _today_utc_date()
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("connector") != connector:
                    continue
                ts = str(row.get("ts", ""))
                if not ts.startswith(today):
                    continue
                count += 1
    except Exception:
        logger.debug(
            "connector_budget: today_calls read failed", exc_info=True,
        )
        return 0
    return count


def today_spend_all_connectors() -> dict[str, dict]:
    """Aggregate today's spend grouped by connector.

    Returns ``{connector: {usd: float, calls: int, estimated_calls: int}}``.
    Empty dict when the ledger is missing or unreadable. Linear scan
    of the file once — the per-connector ``today_spend`` would cost
    one scan per connector if called in a loop.
    """
    path = _ledger_path()
    if not path.exists():
        return {}
    today = _today_utc_date()
    out: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = str(row.get("ts", ""))
                if not ts.startswith(today):
                    continue
                name = str(row.get("connector", ""))
                if not name:
                    continue
                try:
                    usd = float(row.get("usd", 0.0))
                except (TypeError, ValueError):
                    continue
                bucket = out.setdefault(
                    name,
                    {"usd": 0.0, "calls": 0, "estimated_calls": 0},
                )
                bucket["usd"] += usd
                bucket["calls"] += 1
                if bool(row.get("estimated", False)):
                    bucket["estimated_calls"] += 1
    except Exception:
        logger.debug(
            "connector_budget: today_spend_all_connectors read failed",
            exc_info=True,
        )
        return {}
    return out


def window_spend_by_connector(days: int = 7) -> dict[str, dict]:
    """Aggregate spend grouped by connector over the trailing N UTC days.

    Returns ``{connector: {usd, calls, estimated_calls}}``. The window
    is inclusive — today counts. Empty dict when the ledger is missing
    or unreadable. Linear scan of the file once.

    ``days`` must be >= 1; values < 1 are clamped to 1. Larger values
    work but performance degrades linearly with file size.
    """
    if days < 1:
        days = 1
    path = _ledger_path()
    if not path.exists():
        return {}
    # Build the set of acceptable date prefixes
    today_dt = _dt.datetime.now(_dt.timezone.utc).date()
    window: set[str] = {
        (today_dt - _dt.timedelta(days=i)).isoformat()
        for i in range(days)
    }
    out: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = str(row.get("ts", ""))
                # Date prefix is the first 10 chars of an ISO timestamp
                if len(ts) < 10 or ts[:10] not in window:
                    continue
                name = str(row.get("connector", ""))
                if not name:
                    continue
                try:
                    usd = float(row.get("usd", 0.0))
                except (TypeError, ValueError):
                    continue
                bucket = out.setdefault(
                    name,
                    {"usd": 0.0, "calls": 0, "estimated_calls": 0},
                )
                bucket["usd"] += usd
                bucket["calls"] += 1
                if bool(row.get("estimated", False)):
                    bucket["estimated_calls"] += 1
    except Exception:
        logger.debug(
            "connector_budget: window_spend_by_connector read failed",
            exc_info=True,
        )
        return {}
    return out


def _alerts_path() -> Path:
    return _ledger_dir() / "alerts.json"


def should_alert_budget_exceeded(connector: str) -> bool:
    """Per-connector once-per-day dedup for ConnectorBudgetExceeded
    Signal alerts. Returns True the first time it's called for a
    given (connector, day) pair, False thereafter for the same day.

    The state file is small (one entry per connector). Updates are
    eager — calling this method MARKS the alert as fired for today.
    Failure-isolated: a sick FS returns True (fail-open — operator
    gets the alert even if dedup state can't be persisted).
    """
    path = _alerts_path()
    today = _today_utc_date()
    state: dict = {}
    try:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        logger.debug(
            "connector_budget: alerts state read failed", exc_info=True,
        )
        state = {}

    last_alerted = state.get(connector, "")
    if last_alerted == today:
        return False
    state[connector] = today
    try:
        with _WRITE_LOCK:
            path.write_text(
                json.dumps(state, sort_keys=True), encoding="utf-8",
            )
    except Exception:
        # Fail-open: even if we can't persist, return True so the
        # alert fires. Operators see it once; next call same day
        # might re-fire (acceptable).
        logger.debug(
            "connector_budget: alerts state write failed", exc_info=True,
        )
    return True


def reset_for_tests(workspace_root: Optional[Path] = None) -> None:
    """Tests use this to point the ledger at a tmp dir.

    Pass None to restore the default workspace path. Re-reads
    ``app.paths.WORKSPACE_ROOT`` on next call.
    """
    global _ledger_dir
    if workspace_root is None:
        # Restore default by re-binding to the original closure
        from app.connector_budget import store as _self
        _self._ledger_dir = _default_ledger_dir
        return

    target = Path(workspace_root) / "connector_budget"
    target.mkdir(parents=True, exist_ok=True)

    def _override() -> Path:
        return target

    from app.connector_budget import store as _self
    _self._ledger_dir = _override


_default_ledger_dir = _ledger_dir
