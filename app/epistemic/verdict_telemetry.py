"""Verdict telemetry for the epistemic gate.

Records every :class:`app.epistemic.orchestrator_hook.GateResult` to a
hash-stamped JSONL so the advisory report (Stage B) can aggregate the
"what would have blocked" picture before the operator promotes to
enforcing mode.

Design:

* **Append-only JSONL** at ``workspace/epistemic/gate_verdicts.jsonl``.
  No DB schema migration needed; one file per gateway. Mirrors the
  ``workspace/healing/structured_diagnosis_telemetry.jsonl`` pattern
  from PROGRAM §39.3.2 — same operational ergonomics, same dashboards.

* **Bounded growth.** Hard cap at 50,000 rows via the existing
  :func:`app.safe_io.append_with_cap`. At ~200 replies/day that's a
  rolling 8-month window — long enough for any meaningful advisory soak.

* **Failure-isolated.** record_verdict() never raises. A defect here
  must never disturb the reply path that already has the gate's own
  fallback discipline.

* **One row per gate fire, not one per call.** The "skip" path (trivial
  pattern routing) is excluded so the report's denominators reflect
  real evaluations, not the local-route bypass.

Schema of each row::

    {
      "ts": <epoch_seconds>,
      "task_id": "<short id>",
      "action": "ship" | "revise" | "block",
      "blocking_mode": true | false,
      "user_visible_reason": "<...>",   # populated on revise/block
      "diagnostic_note": "<...>",       # populated on internal failure
      "verdict": { ... CalibrationVerdict.as_jsonable() ... } | null,
      "zone": "chat" | "autonomous" | "financial" | null,
      "ledger_size": <int>,             # how many claims the gate saw
    }

Operator surfaces:
  * ``python -m app.observability.epistemic_advisory_report``  (CLI)
  * ``aai advisory epistemic``                                  (CLI alias)
  * ``GET /api/cp/epistemic/advisory-report``                  (REST/React)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.epistemic.orchestrator_hook import GateResult

logger = logging.getLogger(__name__)

_MAX_ROWS = 50_000


def _ledger_path() -> Path:
    """``workspace/epistemic/gate_verdicts.jsonl`` (auto-mkdir on first write)."""
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "epistemic" / "gate_verdicts.jsonl"


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("verdict_telemetry: mkdir failed", exc_info=True)


def _coerce(obj: Any) -> Any:
    """Coerce dataclasses / enums / dicts to JSON-safe shapes."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if hasattr(obj, "as_jsonable"):
        try:
            return obj.as_jsonable()
        except Exception:
            return str(obj)
    if hasattr(obj, "value"):  # StrEnum
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_coerce(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _coerce(v) for k, v in obj.items()}
    return str(obj)


def record_verdict(
    result: "GateResult",
    *,
    task_id: str = "",
    zone: Optional[str] = None,
    ledger_size: Optional[int] = None,
) -> None:
    """Append one row to ``gate_verdicts.jsonl``. Never raises.

    The ``"skip"`` action is excluded because it's the trivial-pattern
    routing shortcut, not a real evaluation — including it would inflate
    the denominator and hide the real gate behaviour."""
    try:
        # Skip the trivial-pattern shortcut. Detect via the diagnostic note
        # the gate emits when ``is_skip_set()`` returns True.
        if result.action == "ship" and (result.diagnostic_note or "").startswith(
            "skip_verification flag set"
        ):
            return

        row: dict[str, Any] = {
            "ts": time.time(),
            "task_id": (task_id or "")[:64],
            "action": result.action,
            "blocking_mode": bool(result.blocking_mode),
            "user_visible_reason": (result.user_visible_reason or "")[:500],
            "diagnostic_note": (result.diagnostic_note or "")[:500],
            "verdict": _coerce(result.verdict) if result.verdict else None,
            "zone": zone,
            "ledger_size": ledger_size,
        }

        path = _ledger_path()
        _ensure_parent(path)
        # Use the existing capped-append helper — guarantees bounded growth
        # and shares a lock with other safe_io writers (cooperates with the
        # lock_contention monitor Q14.6).
        try:
            from app.safe_io import append_with_cap
            append_with_cap(path, json.dumps(row, default=str) + "\n", _MAX_ROWS)
        except Exception:
            # Fallback to a vanilla append if safe_io is unavailable (e.g.
            # in early bootstrap or stripped-down test contexts).
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        logger.debug("verdict_telemetry: record failed", exc_info=True)


def latest_verdict_for_task(task_id: str) -> dict[str, Any] | None:
    """Return the most-recent persisted verdict for a given task_id, or
    ``None`` if not found. Used by the reaction bridge to resolve the
    true ``gate_action`` (ship / revise / block) of a reply at reaction
    time, without coupling main.py to gate-internal state.

    Scans the JSONL backwards. Reads at most the trailing 200 KB —
    sufficient for thousands of recent verdicts; bounded so a giant
    file doesn't make a reaction-handler call expensive."""
    if not task_id:
        return None
    path = _ledger_path()
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read().decode("utf-8", errors="ignore")
        # Walk lines newest-last → scan reversed.
        for line in reversed(tail.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("task_id") == task_id:
                return row
    except Exception:
        logger.debug("verdict_telemetry: latest lookup failed", exc_info=True)
    return None


def read_rows_since(since_ts: float) -> list[dict[str, Any]]:
    """Return verdict rows with ``ts >= since_ts``. Bounded by file cap.

    Streams the JSONL forward; tolerates partial / mangled lines without
    raising. Used by the advisory report aggregator."""
    path = _ledger_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = row.get("ts")
                if isinstance(ts, (int, float)) and ts >= since_ts:
                    rows.append(row)
    except Exception:
        logger.debug("verdict_telemetry: read failed", exc_info=True)
    return rows
