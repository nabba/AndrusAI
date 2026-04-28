"""Tests for the 2026-04-28 budget default-limit fix.

Bug — auto-created budget rows from ``reconcile_actual_spend`` used
``limit_usd = 0``. The enforcer interprets 0 as "no budget", so the
very first observed spend (even half a cent) tripped is_paused and
locked the agent role out. The user hit this on the ``pim`` crew:
the email-routing fix shipped earlier the same day got the question
to the right specialist, but PIM was paused with $0 limit and
$0.0065 spent → "Budget exceeded for pim in 2026-04".

Fix — pull the default limit from ``settings.default_budget_per_agent_usd``
(50 USD) at insert time so newly-tracked roles start with breathing
room rather than instant lockout.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# Static check — reconcile_actual_spend doesn't hardcode limit=0
# ══════════════════════════════════════════════════════════════════════

class TestDefaultLimitFromSettings:
    """The auto-created budget row MUST use the settings default
    instead of a hardcoded 0. Static analysis is enough — this is
    a regression-pin, not a behavioral test."""

    def test_no_hardcoded_zero_in_insert(self):
        text = (REPO / "app" / "control_plane" / "budgets.py").read_text()
        # The buggy version had `VALUES (%s, %s, %s, 0, %s, %s)` — pin
        # that exact pattern against return.
        assert re.search(
            r"VALUES\s*\(\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*0\s*,",
            text,
        ) is None, (
            "reconcile_actual_spend must NOT hardcode limit_usd=0 in the "
            "auto-create INSERT — that immediately triggers is_paused on "
            "any first spend (the 2026-04-28 PIM lockout). Use "
            "settings.default_budget_per_agent_usd instead."
        )

    def test_uses_settings_default(self):
        text = (REPO / "app" / "control_plane" / "budgets.py").read_text()
        assert "default_budget_per_agent_usd" in text, (
            "reconcile_actual_spend must read the project-wide default "
            "from settings.default_budget_per_agent_usd."
        )

    def test_fallback_when_settings_unavailable(self):
        """Defensive: tests run with shimmed settings; the function
        must still return a sensible default if get_settings raises."""
        text = (REPO / "app" / "control_plane" / "budgets.py").read_text()
        # We accept either an explicit fallback constant or a literal
        # 50.0 in an except clause — both lock the safety property in.
        has_fallback = (
            "default_limit = 50.0" in text
            or "or 50.0" in text
        )
        assert has_fallback, (
            "reconcile_actual_spend must have a try/except around "
            "settings access with a 50.0 fallback so missing settings "
            "don't reintroduce the limit=0 trap."
        )


# ══════════════════════════════════════════════════════════════════════
# Behavioral check — calling reconcile_actual_spend on a fresh role
# inserts a row with limit > 0 (mocked execute)
# ══════════════════════════════════════════════════════════════════════

class TestRecordObservedSpendBehavior:

    def test_insert_uses_default_limit_not_zero(self, monkeypatch):
        from tests._v2_shim import install_settings_shim
        install_settings_shim(default_budget_per_agent_usd=50.0)

        from app.control_plane import budgets as _b

        captured = {}

        def fake_execute(query, params, *args, **kw):
            captured["query"] = query
            captured["params"] = params

        monkeypatch.setattr(_b, "execute", fake_execute)
        _b.reconcile_actual_spend(
            project_id="proj-x",
            agent_role="pim",
            cost_usd=0.005,
            tokens=100,
        )
        # params order: (project_id, role, period, limit, spent, tokens)
        assert captured.get("params") is not None, "execute must be called"
        params = captured["params"]
        # 4th param is limit_usd
        limit_passed = params[3]
        assert float(limit_passed) > 0, (
            f"insert passed limit_usd={limit_passed} — must be > 0 "
            f"(2026-04-28 regression: pim locked out at first spend)"
        )
        assert float(limit_passed) == 50.0, (
            f"expected limit_usd=50.0 from settings default, got {limit_passed}"
        )

    def test_no_op_when_cost_is_zero(self, monkeypatch):
        """Sanity — reconcile_actual_spend short-circuits on cost=0
        so we don't pollute the table with empty rows."""
        from tests._v2_shim import install_settings_shim
        install_settings_shim()
        from app.control_plane import budgets as _b

        called = []
        monkeypatch.setattr(_b, "execute", lambda *a, **kw: called.append(1))
        _b.reconcile_actual_spend(
            project_id="proj-x", agent_role="pim", cost_usd=0.0, tokens=0,
        )
        assert called == []
