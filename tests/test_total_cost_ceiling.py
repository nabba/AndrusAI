"""Tests for app.healing.monitors.total_cost_ceiling — Gap #2 monthly
spend brake."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.healing.monitors import total_cost_ceiling as tc  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(tc, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(tc, "_enabled", lambda: True)
    return tmp_path


def test_evaluate_below_warning_returns_ok() -> None:
    out = tc.evaluate(
        spend_usd=50.0, cap_usd=200.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=False,
    )
    assert out["level"] == "ok"
    assert out["brake_target"] is False
    assert out["alert_warning"] is False
    assert out["alert_critical"] is False
    assert out["alert_release"] is False


def test_evaluate_warning_zone() -> None:
    out = tc.evaluate(
        spend_usd=170.0, cap_usd=200.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=False,
    )
    assert out["level"] == "warn"
    assert out["brake_target"] is False
    assert out["alert_warning"] is True
    assert out["alert_critical"] is False


def test_evaluate_brake_zone_engages() -> None:
    out = tc.evaluate(
        spend_usd=195.0, cap_usd=200.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=False,
    )
    assert out["level"] == "brake"
    assert out["brake_target"] is True
    assert out["alert_critical"] is True


def test_evaluate_hysteresis_stays_braked_between_70_and_95(
) -> None:
    """In the hysteresis band (70-95%), if the brake is currently
    engaged it stays engaged. Releases only once spend drops below 70%."""
    # At 85% with brake on: stays braked.
    out = tc.evaluate(
        spend_usd=170.0, cap_usd=200.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=True,
    )
    assert out["brake_target"] is True
    assert out["alert_release"] is False

    # At 75% with brake on: stays braked (hysteresis).
    out = tc.evaluate(
        spend_usd=150.0, cap_usd=200.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=True,
    )
    assert out["brake_target"] is True


def test_evaluate_releases_below_70pct() -> None:
    out = tc.evaluate(
        spend_usd=120.0, cap_usd=200.0,  # 60%
        day_of_month=20, days_in_month=30,
        brake_currently_engaged=True,
    )
    assert out["brake_target"] is False
    assert out["alert_release"] is True


def test_projected_end_of_month_extrapolates_linearly() -> None:
    out = tc.evaluate(
        spend_usd=50.0, cap_usd=200.0,
        day_of_month=10, days_in_month=30,
        brake_currently_engaged=False,
    )
    assert out["projected_end_of_month"] == pytest.approx(150.0)


def test_evaluate_handles_zero_cap_gracefully() -> None:
    out = tc.evaluate(
        spend_usd=10.0, cap_usd=0.0,
        day_of_month=15, days_in_month=30,
        brake_currently_engaged=False,
    )
    assert out["pct"] == 0.0


def test_evaluate_handles_zero_day_progress() -> None:
    out = tc.evaluate(
        spend_usd=10.0, cap_usd=200.0,
        day_of_month=0, days_in_month=30,
        brake_currently_engaged=False,
    )
    # Falls back to current spend rather than dividing by zero.
    assert out["projected_end_of_month"] == 10.0


def test_run_persists_state(monkeypatch, _tmp_workspace: Path) -> None:
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 50.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: False)
    monkeypatch.setattr(tc, "_set_brake", lambda v: None)
    monkeypatch.setattr("app.notify.notify", lambda **kw: None)

    result = tc.run(now=1_000_000.0)
    assert result["ran"] is True
    assert result["level"] == "ok"
    assert result["alerts_sent"] == []

    state = json.loads(
        (_tmp_workspace / "healing" / "total_cost_ceiling_state.json").read_text()
    )
    assert state["last_run_at"] == 1_000_000.0


def test_run_skips_when_db_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: None)
    result = tc.run(now=1_000_000.0)
    assert result == {
        "ran": True, "skipped": True, "reason": "db_unreachable"
    }


def test_run_alerts_at_warning_threshold(monkeypatch, _tmp_workspace: Path) -> None:
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 170.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: False)
    monkeypatch.setattr(tc, "_set_brake", lambda v: None)
    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))

    result = tc.run(now=1_000_000.0)
    assert "warning" in result["alerts_sent"]
    assert len(sent) == 1
    assert sent[0]["critical"] is False


def test_run_engages_brake_at_95pct(monkeypatch, _tmp_workspace: Path) -> None:
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 195.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: False)
    brake_calls: list[bool] = []
    monkeypatch.setattr(tc, "_set_brake", lambda v: brake_calls.append(v))
    monkeypatch.setattr("app.notify.notify", lambda **kw: None)

    result = tc.run(now=1_000_000.0)
    assert result["level"] == "brake"
    assert result["brake_engaged_after"] is True
    assert brake_calls == [True]


def test_run_monthly_dedup_for_warning(monkeypatch, _tmp_workspace: Path) -> None:
    """Two passes in the same calendar month at the warning threshold
    should produce exactly one warning alert."""
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 170.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: False)
    monkeypatch.setattr(tc, "_set_brake", lambda v: None)
    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))

    base = 1_700_000_000.0  # arbitrary ts in mid-month
    tc.run(now=base)
    # Second pass a day later, same month — must NOT re-alert.
    tc.run(now=base + 26 * 3600)
    assert len(sent) == 1


def test_run_releases_brake_with_alert(monkeypatch, _tmp_workspace: Path) -> None:
    """Brake on, spend drops to 60% → brake released + release alert."""
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 120.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: True)
    brake_calls: list[bool] = []
    monkeypatch.setattr(tc, "_set_brake", lambda v: brake_calls.append(v))
    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))

    result = tc.run(now=1_000_000.0)
    assert result["brake_engaged_after"] is False
    assert brake_calls == [False]
    assert "release" in result["alerts_sent"]


def test_run_internal_cadence_gates_repeated_calls(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_query_mtd_total_cost", lambda now: 50.0)
    monkeypatch.setattr(tc, "_monthly_cap_usd", lambda: 200.0)
    monkeypatch.setattr(tc, "_brake_is_engaged", lambda: False)
    monkeypatch.setattr(tc, "_set_brake", lambda v: None)
    monkeypatch.setattr("app.notify.notify", lambda **kw: None)

    tc.run(now=1_000_000.0)
    second = tc.run(now=1_000_000.0 + 60)  # 60s later
    assert second["ran"] is False
