"""Anthropic per-day USD spend cap (Phase D.3, 2026-05-22).

Vendor-level cost ceiling on top of the existing per-model breakers
and connector budgets. Sits next to ``app/connector_budget/`` rather
than inside it because the cap-mechanism is structurally identical
but the *scope* is different:

  * ``connector_budget`` — per-external-service quota (Aviationstack,
    OSV.dev, GitHub API, …). One ledger row per *call* per *connector*.
  * ``llm_anthropic_budget`` — vendor-wide rolling USD across every
    Anthropic call regardless of which subsystem made it (Mem0
    extractor, brainstorm crew, coder agent, dossier composer, …).

When a runaway loop / accidentally-cheaper-model-rotation / new
high-volume subsystem starts burning Anthropic credit, this cap is
the back-stop that fires before the bill arrives. The existing
``circuit_breaker["anthropic_credits"]`` (in ``llm_factory.py``) is
reactive — it only trips once Anthropic itself returns a 402 /
"insufficient credit" error. This cap is proactive — it refuses the
*next* call when projected spend would exceed the operator-set
ceiling.

Design constraints
──────────────────

  * Default OFF (``anthropic_daily_cap_usd = None``). Operators flip
    it on once they've watched cost shape for a few weeks and know
    a sane ceiling.
  * Pure-function pre-check: ``pre_check(estimated_usd) -> None``
    raises ``AnthropicDailyCapExceeded`` when the cap would be
    breached, OR is a no-op when disabled.
  * Spend accounting reuses ``app.audit_log`` rolling-24h aggregation
    — same source of truth the React Cost dashboard reads, so the
    cap matches what operators see.
  * Failure-isolated: any error reading the cap / today's spend
    degrades to "no cap" rather than blocking a real call.

Caller contract
───────────────

The high-volume direct callers (Mem0 extractor, structured-diagnosis,
brainstorm seed/react rounds) opt into the gate by calling
``pre_check`` BEFORE invoking ``anthropic.Anthropic().messages.create``.
The factory-routed calls (CrewAI cascade) get it for free once the
factory's chokepoint is wired (separate ship — keeping this module
itself decoupled from llm_factory so the primitive is testable in
isolation).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Exception ───────────────────────────────────────────────────────


class AnthropicDailyCapExceeded(Exception):
    """Raised by :func:`pre_check` when the next Anthropic call would
    push the rolling-24h spend over the configured cap.

    Attributes
    ----------
    today_spent_usd
        Anthropic spend in the rolling 24h window prior to this call.
    daily_cap_usd
        The configured ceiling.
    estimated_cost_usd
        The next call's estimate that triggered the refusal.
    """

    def __init__(
        self,
        today_spent_usd: float,
        daily_cap_usd: float,
        estimated_cost_usd: float,
    ) -> None:
        self.today_spent_usd = today_spent_usd
        self.daily_cap_usd = daily_cap_usd
        self.estimated_cost_usd = estimated_cost_usd
        super().__init__(
            f"Anthropic daily cap ${daily_cap_usd:.2f} would be "
            f"exceeded — already spent ${today_spent_usd:.4f} in "
            f"rolling 24h; next call estimated ${estimated_cost_usd:.4f}"
        )


# ── Cap reader ──────────────────────────────────────────────────────


def get_cap() -> Optional[float]:
    """Read the operator-set cap. Returns ``None`` when disabled
    (no cap in effect). Failure-isolated — any error reading
    runtime_settings degrades to None.

    The runtime_settings module is imported via ``sys.modules`` so
    tests can swap the implementation. ``from app import runtime_settings``
    would bind the attribute on the ``app`` package on first call,
    making test-time substitution awkward; the ``importlib.import_module``
    path resolves fresh each call.
    """
    try:
        import importlib
        rs = importlib.import_module("app.runtime_settings")
        cap = rs.get_anthropic_daily_cap_usd()
    except Exception:
        return None
    if cap is None:
        return None
    try:
        v = float(cap)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


# ── Spend reader ────────────────────────────────────────────────────


def today_spent_usd() -> float:
    """Return rolling-24h Anthropic spend from the audit log.

    Reads the same source the React Cost dashboard reads — keeps the
    gate's "you spent X" number consistent with what operators see.
    Failure-isolated: returns 0.0 on any error so the gate defaults
    to "no spend recorded" rather than blocking legitimate calls.
    """
    try:
        return _read_audit_log_anthropic_spend(window_hours=24)
    except Exception:
        logger.debug(
            "llm_anthropic_budget: audit-log read failed", exc_info=True,
        )
        return 0.0


def _read_audit_log_anthropic_spend(*, window_hours: int) -> float:
    """Sum the ``cost_usd`` field across audit-log rows from the
    rolling window where the model is Anthropic.

    The audit-log shape mirrors what ``app.audit_log.append_with_cost``
    writes — a JSONL file with rows containing ``ts``, ``model``,
    ``cost_usd``. Tolerates malformed rows (skipped) and a missing
    log file (returns 0.0).
    """
    try:
        from pathlib import Path
        import importlib
        import json
        al = importlib.import_module("app.audit_log")
        path = al._audit_log_path()
    except Exception:
        # Fall back to a well-known relative path. If the audit-log
        # module isn't importable in this environment, treat spend
        # as unknown rather than blocking.
        return 0.0

    if not isinstance(path, Path) or not path.exists():
        return 0.0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    total = 0.0
    try:
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if not _row_is_anthropic(row):
                    continue
                ts_str = row.get("ts") or row.get("timestamp")
                if not isinstance(ts_str, str):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                cost = row.get("cost_usd") or row.get("cost") or 0.0
                try:
                    total += float(cost)
                except (TypeError, ValueError):
                    continue
    except OSError:
        return 0.0
    return total


def _row_is_anthropic(row: dict) -> bool:
    """Return True if a row's ``model`` field looks like an Anthropic
    Claude model. Matches the live taxonomy: claude-opus / claude-sonnet
    / claude-haiku / anthropic/claude*."""
    model = row.get("model") or row.get("model_id") or ""
    if not isinstance(model, str):
        return False
    lower = model.lower()
    return (
        "claude-opus" in lower
        or "claude-sonnet" in lower
        or "claude-haiku" in lower
        or lower.startswith("anthropic/")
        or "/claude-" in lower
    )


# ── Gate ────────────────────────────────────────────────────────────


def pre_check(estimated_cost_usd: float = 0.0) -> None:
    """Refuse with :class:`AnthropicDailyCapExceeded` when the next
    Anthropic call would push the rolling-24h spend past the cap.

    No-op when the cap is disabled (default), the spend cannot be
    read (failure-isolated), or the estimate would still fit under
    the cap.

    Call this RIGHT BEFORE invoking ``anthropic.Anthropic().messages.create``
    in any high-volume code path. Single-shot calls don't strictly need
    it (the cumulative ceiling already covers them), but it's cheap
    enough that protecting them too costs nothing.
    """
    cap = get_cap()
    if cap is None:
        return  # disabled — no-op
    spent = today_spent_usd()
    try:
        est = float(estimated_cost_usd)
    except (TypeError, ValueError):
        est = 0.0
    if est < 0:
        est = 0.0
    if spent + est > cap:
        raise AnthropicDailyCapExceeded(
            today_spent_usd=spent,
            daily_cap_usd=cap,
            estimated_cost_usd=est,
        )


def call_or_skip(
    estimated_cost_usd: float = 0.0,
    *,
    source: str = "",
) -> bool:
    """Convenience wrapper around :func:`pre_check` for the common
    call-site pattern.

    Returns
    -------
    bool
        ``True`` when the call should proceed (cap not breached OR
        gate disabled OR pre_check itself failed).
        ``False`` when :class:`AnthropicDailyCapExceeded` was raised
        — caller should return its empty-result sentinel.

    Posture
    -------
    Failure-OPEN: anything other than the specific cap-exceeded
    exception is treated as "cap doesn't know what's going on, let
    the call through." Gate bugs must never block legitimate calls.

    Usage
    -----

    ::

        if not llm_anthropic_budget.call_or_skip(
            estimated_cost_usd=0.005, source="brainstorm:idea_evolve",
        ):
            return ""  # caller picks the skip sentinel

        # proceed with the Anthropic call
        client = anthropic.Anthropic()
        ...

    The ``source`` parameter is purely informational — surfaces in
    the skip log line so operators can spot which subsystem hit the
    ceiling.
    """
    try:
        pre_check(estimated_cost_usd=estimated_cost_usd)
        return True
    except AnthropicDailyCapExceeded as exc:
        if source:
            logger.info(
                "Anthropic call skipped (%s): %s", source, exc,
            )
        else:
            logger.info("Anthropic call skipped: %s", exc)
        return False
    except Exception:
        # Failure-OPEN — see docstring posture note.
        logger.debug(
            "llm_anthropic_budget.call_or_skip: pre_check unexpected "
            "error; letting call proceed", exc_info=True,
        )
        return True


def state_snapshot() -> dict:
    """Return ``{cap, spent, headroom, enabled}`` for the operator
    surface (REST endpoint + React Settings card)."""
    cap = get_cap()
    spent = today_spent_usd()
    if cap is None:
        return {
            "enabled": False,
            "cap_usd": None,
            "spent_usd_24h": round(spent, 6),
            "headroom_usd": None,
        }
    return {
        "enabled": True,
        "cap_usd": cap,
        "spent_usd_24h": round(spent, 6),
        "headroom_usd": round(max(0.0, cap - spent), 6),
    }


__all__ = [
    "AnthropicDailyCapExceeded",
    "call_or_skip",
    "get_cap",
    "pre_check",
    "state_snapshot",
    "today_spent_usd",
]
