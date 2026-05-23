"""Tests for the Anthropic per-day USD cap (Phase D.3, 2026-05-22).

Pins the primitive in :mod:`app.llm_anthropic_budget`:

  * Default disabled — ``get_cap()`` returns None when no operator
    setting is present.
  * Pre-check is a no-op when disabled — no audit-log read, no raise.
  * Pre-check raises when projected spend > cap.
  * Spend reader tolerates malformed audit-log rows.
  * State snapshot matches what the React Settings card will render.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Stubs (lock-step with other host tests) ──────────────────────────


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ab = _load_module(
    "_anthropic_budget_d3_test", "app/llm_anthropic_budget.py",
)


# ── Exception contract ──────────────────────────────────────────────


class TestException:
    def test_attributes_carry_through(self):
        exc = _ab.AnthropicDailyCapExceeded(
            today_spent_usd=14.50,
            daily_cap_usd=25.00,
            estimated_cost_usd=15.00,
        )
        assert exc.today_spent_usd == 14.50
        assert exc.daily_cap_usd == 25.00
        assert exc.estimated_cost_usd == 15.00

    def test_message_includes_numbers(self):
        exc = _ab.AnthropicDailyCapExceeded(
            today_spent_usd=14.50, daily_cap_usd=25.00,
            estimated_cost_usd=15.00,
        )
        s = str(exc)
        assert "25.00" in s
        assert "14.5" in s or "14.50" in s
        assert "15.0" in s or "15.00" in s


# ── Cap reader ──────────────────────────────────────────────────────


class TestGetCap:
    def test_disabled_when_runtime_settings_unavailable(
        self, monkeypatch,
    ):
        # Inject a runtime_settings stub that always raises — gate
        # must degrade to None.
        fake = MagicMock()
        fake.get_anthropic_daily_cap_usd.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "app", MagicMock())
        with patch.object(_ab, "_get_runtime_settings_module", create=True):
            # Approach the patch via dynamic — simplest is to replace
            # the import target lazily.
            pass
        # Easier: directly monkeypatch the lookup path used by get_cap.
        # We do this by injecting a sys.modules entry for app.runtime_settings.
        fake_rs = MagicMock()
        fake_rs.get_anthropic_daily_cap_usd.side_effect = RuntimeError("nope")
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)
        assert _ab.get_cap() is None

    def test_negative_value_treated_as_disabled(self, monkeypatch):
        fake_rs = MagicMock()
        fake_rs.get_anthropic_daily_cap_usd.return_value = -5
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)
        assert _ab.get_cap() is None

    def test_zero_treated_as_disabled(self, monkeypatch):
        fake_rs = MagicMock()
        fake_rs.get_anthropic_daily_cap_usd.return_value = 0
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)
        assert _ab.get_cap() is None

    def test_string_coerced_to_float(self, monkeypatch):
        fake_rs = MagicMock()
        fake_rs.get_anthropic_daily_cap_usd.return_value = "12.50"
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)
        assert _ab.get_cap() == 12.50

    def test_non_numeric_treated_as_disabled(self, monkeypatch):
        fake_rs = MagicMock()
        fake_rs.get_anthropic_daily_cap_usd.return_value = "not-a-number"
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)
        assert _ab.get_cap() is None


# ── Pre-check contract ──────────────────────────────────────────────


class TestPreCheck:
    def test_noop_when_cap_disabled(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: None)
        # Even a huge estimate must NOT raise when no cap set.
        _ab.pre_check(estimated_cost_usd=1_000_000.0)

    def test_under_cap_passes(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 10.0)
        # 10 + 5 = 15, well under 25
        _ab.pre_check(estimated_cost_usd=5.0)

    def test_over_cap_raises(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 20.0)
        # 20 + 10 = 30 > 25
        with pytest.raises(_ab.AnthropicDailyCapExceeded) as excinfo:
            _ab.pre_check(estimated_cost_usd=10.0)
        assert excinfo.value.today_spent_usd == 20.0
        assert excinfo.value.daily_cap_usd == 25.0
        assert excinfo.value.estimated_cost_usd == 10.0

    def test_exactly_at_cap_is_ok(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 20.0)
        # 20 + 5 = 25 exactly. Spec says "would exceed" — exact-
        # equality passes.
        _ab.pre_check(estimated_cost_usd=5.0)

    def test_estimate_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 24.99)
        # Negative estimate must be coerced to 0 — call passes.
        _ab.pre_check(estimated_cost_usd=-100.0)

    def test_estimate_non_numeric_clamped_to_zero(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 24.99)
        _ab.pre_check(estimated_cost_usd="not-a-number")  # type: ignore

    def test_default_estimate_zero(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 24.99)
        # No estimate supplied -> 0.0 -> 24.99 + 0 = 24.99 ≤ 25 -> OK
        _ab.pre_check()


# ── Audit-log reader ────────────────────────────────────────────────


class TestAuditLogReader:
    def test_returns_zero_when_path_missing(self, monkeypatch, tmp_path):
        # Audit-log module reports a path that doesn't exist
        fake_al = MagicMock()
        fake_al._audit_log_path.return_value = tmp_path / "nonexistent.jsonl"
        monkeypatch.setitem(sys.modules, "app.audit_log", fake_al)
        assert _ab.today_spent_usd() == 0.0

    def test_sums_anthropic_rows_in_window(self, monkeypatch, tmp_path):
        log = tmp_path / "audit.jsonl"
        now = datetime.now(timezone.utc)
        rows = [
            {
                "ts": (now - timedelta(hours=2)).isoformat(),
                "model": "claude-sonnet-4.5",
                "cost_usd": 5.50,
            },
            {
                "ts": (now - timedelta(hours=10)).isoformat(),
                "model": "claude-haiku-4.5",
                "cost_usd": 2.25,
            },
            # Out of window — must NOT count
            {
                "ts": (now - timedelta(hours=30)).isoformat(),
                "model": "claude-opus-4.5",
                "cost_usd": 100.0,
            },
            # Not anthropic — must NOT count
            {
                "ts": (now - timedelta(hours=1)).isoformat(),
                "model": "gpt-4o",
                "cost_usd": 7.50,
            },
            # anthropic/ prefix shape
            {
                "ts": (now - timedelta(hours=1)).isoformat(),
                "model": "anthropic/claude-3-5-sonnet-20241022",
                "cost_usd": 1.25,
            },
        ]
        with log.open("w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")

        fake_al = MagicMock()
        fake_al._audit_log_path.return_value = log
        monkeypatch.setitem(sys.modules, "app.audit_log", fake_al)

        total = _ab.today_spent_usd()
        # 5.50 + 2.25 + 1.25 = 9.00 ; opus row outside window;
        # gpt-4o not anthropic
        assert total == pytest.approx(9.00, abs=1e-6)

    def test_malformed_rows_skipped(self, monkeypatch, tmp_path):
        log = tmp_path / "audit.jsonl"
        with log.open("w", encoding="utf-8") as fp:
            fp.write("not valid json\n")
            fp.write("\n")  # empty line
            fp.write(json.dumps({"ts": "garbage", "model": "claude-sonnet",
                                 "cost_usd": 1.0}) + "\n")
            now = datetime.now(timezone.utc)
            fp.write(json.dumps({
                "ts": now.isoformat(),
                "model": "claude-sonnet",
                "cost_usd": "not-a-float",
            }) + "\n")
            fp.write(json.dumps({
                "ts": now.isoformat(),
                "model": "claude-sonnet",
                "cost_usd": 3.50,
            }) + "\n")

        fake_al = MagicMock()
        fake_al._audit_log_path.return_value = log
        monkeypatch.setitem(sys.modules, "app.audit_log", fake_al)

        # Only the last row survives; total = 3.50
        assert _ab.today_spent_usd() == pytest.approx(3.50, abs=1e-6)

    def test_audit_log_unavailable_returns_zero(self, monkeypatch):
        # If the audit_log module itself can't be imported, return
        # 0.0 rather than blocking.
        if "app.audit_log" in sys.modules:
            monkeypatch.delitem(sys.modules, "app.audit_log")
        fake_app = MagicMock()
        # Break the import chain
        monkeypatch.setitem(sys.modules, "app.audit_log",
                           type("M", (), {"__path__": []})())
        # The actual error is "audit_log has no _audit_log_path" or
        # similar — falls back to 0.0
        assert _ab.today_spent_usd() == 0.0


# ── State snapshot ──────────────────────────────────────────────────


class TestStateSnapshot:
    def test_disabled_snapshot(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: None)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 0.0)
        s = _ab.state_snapshot()
        assert s == {
            "enabled": False,
            "cap_usd": None,
            "spent_usd_24h": 0.0,
            "headroom_usd": None,
        }

    def test_enabled_snapshot_with_headroom(self, monkeypatch):
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 10.0)
        s = _ab.state_snapshot()
        assert s["enabled"] is True
        assert s["cap_usd"] == 25.0
        assert s["spent_usd_24h"] == 10.0
        assert s["headroom_usd"] == 15.0

    def test_overspend_clamps_headroom_to_zero(self, monkeypatch):
        # If we somehow exceeded the cap (e.g. cap was just lowered),
        # headroom must clamp to 0 rather than show a negative.
        monkeypatch.setattr(_ab, "get_cap", lambda: 25.0)
        monkeypatch.setattr(_ab, "today_spent_usd", lambda: 40.0)
        s = _ab.state_snapshot()
        assert s["headroom_usd"] == 0.0


# ── Anthropic row matcher ───────────────────────────────────────────


class TestRowIsAnthropic:
    @pytest.mark.parametrize("model", [
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "claude-opus-4",
        "CLAUDE-SONNET",   # case-insensitive
        "anthropic/claude-3-5-sonnet-20241022",
        "openrouter/anthropic/claude-3.5-sonnet",
    ])
    def test_matches_anthropic_variants(self, model):
        assert _ab._row_is_anthropic({"model": model}) is True

    @pytest.mark.parametrize("model", [
        "gpt-4o", "gemini-pro", "deepseek-chat", "llama3", "",
    ])
    def test_rejects_non_anthropic(self, model):
        assert _ab._row_is_anthropic({"model": model}) is False

    def test_rejects_missing_model_field(self):
        assert _ab._row_is_anthropic({}) is False

    def test_rejects_non_string_model(self):
        assert _ab._row_is_anthropic({"model": 42}) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
