"""Render helpers for the ``/budgets`` Signal command (Phase D.3
follow-up, 2026-05-22).

Lives in its own module so:
  * The full commands.py module (heavy: 200+ imports) doesn't have
    to load for the rendering to be unit-testable.
  * The two helpers stay pure: input is "what the budget subsystems
    report", output is operator-facing markdown text. No I/O, no
    side effects.
  * Future surfaces (e.g. a /api/cp/budgets/text endpoint that
    returns the same text the operator sees in Signal) can call
    these helpers without going through the slash-command dispatcher.

The dispatcher in commands.py composes both blocks into the final
slash-command reply.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def render_anthropic_budget_block(
    snap_loader: Optional[Any] = None,
) -> str:
    """Render the Anthropic vendor-level cap section.

    Parameters
    ----------
    snap_loader
        Optional callable returning the state-snapshot dict. Defaults
        to ``app.llm_anthropic_budget.state_snapshot``. Injectable
        for tests + alternative call sites.

    Returns
    -------
    str
        Operator-facing single-line summary. Never raises — failures
        produce a "state read failed" message that points the operator
        at the fix path.
    """
    if snap_loader is None:
        def _default():
            from app import llm_anthropic_budget
            return llm_anthropic_budget.state_snapshot()
        snap_loader = _default

    try:
        snap = snap_loader()
    except Exception:
        return (
            "🤖 Anthropic cap: state read failed — flip in "
            "/cp/settings → Anthropic per-day cap."
        )

    if not snap.get("enabled"):
        spent = float(snap.get("spent_usd_24h") or 0.0)
        return (
            "🤖 Anthropic cap: DISABLED (no vendor-level ceiling). "
            f"Rolling 24h spend ${spent:.4f}.\n"
            "  Set a cap in /cp/settings → Anthropic per-day cap "
            "or POST /api/cp/anthropic-budget/cap."
        )

    cap = float(snap.get("cap_usd") or 0.0)
    spent = float(snap.get("spent_usd_24h") or 0.0)
    headroom = float(snap.get("headroom_usd") or 0.0)
    pct = (spent / cap * 100.0) if cap > 0 else 0.0
    warn = ""
    if pct >= 90.0:
        warn = " ⚠️  >90% — refusals imminent."
    elif pct >= 75.0:
        warn = " ⚠️  >75%."
    return (
        f"🤖 Anthropic cap: ${cap:.2f}/day · spent ${spent:.4f} "
        f"({pct:.1f}%) · headroom ${headroom:.4f}.{warn}"
    )


def render_connector_budgets_block(
    *,
    enabled_getter: Optional[Any] = None,
    today_getter: Optional[Any] = None,
    window_getter: Optional[Any] = None,
) -> str:
    """Render the per-connector breakdown section.

    Parameters
    ----------
    enabled_getter
        Zero-arg callable returning whether connector budgets are
        master-switched ON. Defaults to
        ``app.runtime_settings.get_connector_budgets_enabled``.
    today_getter
        Zero-arg callable returning today's per-connector spend dict.
        Defaults to ``app.connector_budget.today_spend_all_connectors``.
    window_getter
        One-arg (``days=7``) callable returning rolling-window spend.
        Defaults to ``app.connector_budget.window_spend_by_connector``.

    Returns
    -------
    str
        Multi-line operator-facing summary.
    """
    if enabled_getter is None:
        def _default_enabled():
            from app import runtime_settings
            return runtime_settings.get_connector_budgets_enabled()
        enabled_getter = _default_enabled

    try:
        enabled = bool(enabled_getter())
    except Exception:
        enabled = False
    if not enabled:
        return (
            "💸 Connector budgets are OFF — decorator is a "
            "pass-through (no spend recorded, no caps enforced).\n"
            "  Flip on in /cp/settings → Connector budgets."
        )

    if today_getter is None:
        def _default_today():
            from app.connector_budget import (
                today_spend_all_connectors,
            )
            return today_spend_all_connectors()
        today_getter = _default_today

    if window_getter is None:
        def _default_window(days: int = 7):
            from app.connector_budget import window_spend_by_connector
            return window_spend_by_connector(days=days)
        window_getter = _default_window

    try:
        today = today_getter()
        window = window_getter(days=7)
    except Exception as exc:
        return f"💸 Connector budgets: read failed — {exc}"

    if not today and not window:
        return (
            "💸 Connector budgets: no spend recorded.\n"
            "  When a wrapped connector fires its first call, "
            "spending will appear here."
        )

    # Sort by descending recent spend (window > today since window is
    # cumulative)
    all_names = set(today) | set(window)
    sorted_names = sorted(
        all_names,
        key=lambda n: -float(window.get(n, {}).get("usd", 0.0)),
    )

    today_total = sum(float(b.get("usd", 0.0)) for b in today.values())
    window_total = sum(float(b.get("usd", 0.0)) for b in window.values())

    lines = [
        f"💸 Connector budgets — today ${today_total:.4f} · "
        f"7d ${window_total:.4f}",
    ]
    for name in sorted_names[:10]:
        t = today.get(name, {})
        w = window.get(name, {})
        t_usd = float(t.get("usd", 0.0))
        w_usd = float(w.get("usd", 0.0))
        t_calls = int(t.get("calls", 0))
        w_calls = int(w.get("calls", 0))
        if w_usd > t_usd:
            lines.append(
                f"  {name}: today ${t_usd:.4f} ({t_calls}) · "
                f"7d ${w_usd:.4f} ({w_calls})"
            )
        else:
            lines.append(
                f"  {name}: today ${t_usd:.4f} ({t_calls} call"
                f"{'s' if t_calls != 1 else ''})"
            )
    if len(sorted_names) > 10:
        lines.append(f"  … and {len(sorted_names) - 10} more")
    return "\n".join(lines)


def render_budgets_command() -> str:
    """Compose both blocks into the final ``/budgets`` reply.

    The Anthropic vendor cap goes first (highest-volume vendor /
    biggest blast radius), then the per-connector breakdown.
    """
    parts: list[str] = []
    a = render_anthropic_budget_block()
    if a:
        parts.append(a)
    c = render_connector_budgets_block()
    if c:
        parts.append(c)
    if not parts:
        return "💸 Budgets: no data available."
    return "\n\n".join(parts)


__all__ = [
    "render_anthropic_budget_block",
    "render_budgets_command",
    "render_connector_budgets_block",
]
