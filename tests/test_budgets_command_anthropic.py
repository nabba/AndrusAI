"""Tests for the /budgets Signal command's Anthropic-cap section
(Phase D.3 follow-up, 2026-05-22).

Targets :mod:`app.agents.commander.budgets_render` directly — the
render helpers are pure functions over the budget subsystems'
state-snapshots, so the test surface is decoupled from the heavy
commands.py module.

Coverage:
  * Cap disabled → operator-facing "DISABLED" line including rolling
    24h spend hint.
  * Cap enabled → cap / spent / pct / headroom rendered.
  * >75% / >90% emit graded warnings.
  * llm_anthropic_budget unavailable → graceful failure message.
  * The full command composes Anthropic + Connector blocks with a
    blank-line separator.
  * Connector block: master switch off / no spend / with spend.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ── Stubs (lock-step with other host tests) ─────────────────────────


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


# Direct-load the renderer — pure stdlib + typing imports, no
# pydantic_settings / crewai required.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_br_d3", "app/agents/commander/budgets_render.py",
)
assert _spec is not None and _spec.loader is not None
br = importlib.util.module_from_spec(_spec)
sys.modules["_br_d3"] = br
_spec.loader.exec_module(br)


# ── Anthropic block ─────────────────────────────────────────────────


class TestAnthropicBlock:
    def test_disabled_message_includes_spent(self):
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": False,
                "cap_usd": None,
                "spent_usd_24h": 1.23,
                "headroom_usd": None,
            },
        )
        assert "DISABLED" in out
        assert "$1.2300" in out
        assert "/cp/settings" in out

    def test_enabled_message_includes_cap_spent_pct_headroom(self):
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": True,
                "cap_usd": 25.0,
                "spent_usd_24h": 5.0,
                "headroom_usd": 20.0,
            },
        )
        assert "$25.00/day" in out
        assert "$5.0000" in out
        assert "20.0%" in out
        assert "$20.0000" in out
        # No warning under 75%
        assert "⚠" not in out

    def test_warn_at_75_pct(self):
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": True,
                "cap_usd": 100.0,
                "spent_usd_24h": 80.0,
                "headroom_usd": 20.0,
            },
        )
        assert "⚠" in out
        assert ">75%" in out
        # Not yet the "imminent" tier
        assert "imminent" not in out

    def test_warn_at_90_pct(self):
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": True,
                "cap_usd": 100.0,
                "spent_usd_24h": 95.0,
                "headroom_usd": 5.0,
            },
        )
        assert "⚠" in out
        assert ">90%" in out
        assert "refusals imminent" in out

    def test_warn_at_100_pct(self):
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": True,
                "cap_usd": 100.0,
                "spent_usd_24h": 100.0,
                "headroom_usd": 0.0,
            },
        )
        # 100% is still ≥90% — same message tier
        assert ">90%" in out
        assert "refusals imminent" in out

    def test_snap_loader_raises_graceful(self):
        def _broken():
            raise RuntimeError("nope")
        out = br.render_anthropic_budget_block(snap_loader=_broken)
        assert "state read failed" in out
        assert "/cp/settings" in out

    def test_cap_zero_treated_as_disabled_when_snap_says_so(self):
        # If snapshot reports enabled=False (correctly), the cap-pct
        # branch is never reached.
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {
                "enabled": False,
                "cap_usd": 0,
                "spent_usd_24h": 0,
                "headroom_usd": None,
            },
        )
        assert "DISABLED" in out

    def test_missing_fields_default_to_zero(self):
        # Snapshot returns minimal dict — function tolerates missing
        # cap_usd / spent / headroom by treating them as 0.
        out = br.render_anthropic_budget_block(
            snap_loader=lambda: {"enabled": True},
        )
        # cap=0, spent=0, headroom=0 → no warning, $0.00 / $0.0000 etc.
        assert "$0.00/day" in out
        assert "$0.0000" in out


# ── Connector block ─────────────────────────────────────────────────


class TestConnectorBlock:
    def test_master_switch_off(self):
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: False,
        )
        assert "Connector budgets are OFF" in out
        assert "/cp/settings" in out

    def test_master_switch_getter_raises_treated_as_off(self):
        def _broken():
            raise RuntimeError("nope")
        out = br.render_connector_budgets_block(
            enabled_getter=_broken,
        )
        assert "Connector budgets are OFF" in out

    def test_enabled_no_spend(self):
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: True,
            today_getter=lambda: {},
            window_getter=lambda days=7: {},
        )
        assert "no spend recorded" in out

    def test_enabled_with_spend_renders_connectors(self):
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: True,
            today_getter=lambda: {
                "aviationstack": {"usd": 0.0, "calls": 3},
                "openreview_feed": {"usd": 0.0, "calls": 2},
            },
            window_getter=lambda days=7: {
                "aviationstack": {"usd": 0.0, "calls": 15},
                "openreview_feed": {"usd": 0.0, "calls": 10},
            },
        )
        assert "Connector budgets" in out
        assert "aviationstack" in out
        assert "openreview_feed" in out

    def test_sorted_by_descending_window_spend(self):
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: True,
            today_getter=lambda: {},
            window_getter=lambda days=7: {
                "low":  {"usd": 0.1, "calls": 1},
                "high": {"usd": 5.0, "calls": 50},
                "mid":  {"usd": 1.0, "calls": 10},
            },
        )
        # The 'high' connector must appear before 'mid' before 'low'
        assert out.index("high") < out.index("mid") < out.index("low")

    def test_truncates_after_10(self):
        many = {
            f"conn{i:02d}": {"usd": float(i) * 0.01, "calls": i}
            for i in range(15)
        }
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: True,
            today_getter=lambda: {},
            window_getter=lambda days=7: many,
        )
        assert "and 5 more" in out

    def test_today_getter_raises_treated_as_failure(self):
        def _broken_today():
            raise RuntimeError("disk full")
        out = br.render_connector_budgets_block(
            enabled_getter=lambda: True,
            today_getter=_broken_today,
            window_getter=lambda days=7: {},
        )
        assert "read failed" in out
        assert "disk full" in out


# ── Full command composition ────────────────────────────────────────


class TestFullCommandComposition:
    def test_both_blocks_separated_by_blank_line(self, monkeypatch):
        # Patch the renderers in br to bypass the import-resolving defaults
        monkeypatch.setattr(
            br, "render_anthropic_budget_block",
            lambda: "🤖 Anthropic cap: …",
        )
        monkeypatch.setattr(
            br, "render_connector_budgets_block",
            lambda: "💸 Connector budgets — …",
        )
        out = br.render_budgets_command()
        assert "🤖" in out
        assert "💸" in out
        assert "\n\n" in out
        # Anthropic first
        assert out.index("🤖") < out.index("💸")

    def test_both_blocks_present_in_disabled_state(self, monkeypatch):
        # Real flow: anthropic disabled + connector disabled
        monkeypatch.setattr(
            br, "render_anthropic_budget_block",
            lambda: "🤖 Anthropic cap: DISABLED (no vendor ceiling).",
        )
        monkeypatch.setattr(
            br, "render_connector_budgets_block",
            lambda: "💸 Connector budgets are OFF",
        )
        out = br.render_budgets_command()
        assert "DISABLED" in out
        assert "OFF" in out

    def test_neither_block_produces_fallback(self, monkeypatch):
        # Pathological edge: both helpers return empty strings.
        # The composer must produce SOMETHING — never an empty reply.
        monkeypatch.setattr(
            br, "render_anthropic_budget_block", lambda: "",
        )
        monkeypatch.setattr(
            br, "render_connector_budgets_block", lambda: "",
        )
        out = br.render_budgets_command()
        assert out == "💸 Budgets: no data available."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
