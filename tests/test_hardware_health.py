"""Tests for app.healing.monitors.hardware_health — Gap #11 host-disk
SMART proxy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.healing.monitors import hardware_health as hh  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(hh, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(hh, "_enabled", lambda: True)
    return tmp_path


def _write_jsonl(workspace: Path, rows: list[dict]) -> None:
    path = workspace / "healing" / "host_smart.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_run_returns_skipped_when_no_telemetry(_tmp_workspace: Path) -> None:
    result = hh.run(now=1_000_000.0)
    assert result["skipped"] is True
    assert result["reason"] == "no_host_telemetry"


def test_clean_telemetry_produces_no_alerts(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0",
        "ts": 1_000_000.0,
        "reallocated_sectors": 0,
        "pending_sectors": 0,
        "uncorrectable_errors": 0,
        "temperature_celsius": 40.0,
        "power_on_hours": 1000,
    }])
    result = hh.run(now=1_000_000.0)
    assert result["ran"] is True
    assert result["n_alerts"] == 0


def test_reallocated_jump_alerts(_tmp_workspace: Path) -> None:
    # Establish baseline with 0 realloc.
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0",
        "ts": 1_000_000.0,
        "reallocated_sectors": 0,
        "pending_sectors": 0,
        "uncorrectable_errors": 0,
        "temperature_celsius": 40.0,
    }])
    hh.run(now=1_000_000.0)

    # New reading +10 reallocated.
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0",
        "ts": 1_000_000.0 + 7 * 86400,
        "reallocated_sectors": 10,
        "pending_sectors": 0,
        "uncorrectable_errors": 0,
        "temperature_celsius": 40.0,
    }])
    result = hh.run(now=1_000_000.0 + 7 * 86400)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "reallocated_jump" in kinds


def test_pending_sectors_alert(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0",
        "ts": 1_000_000.0,
        "reallocated_sectors": 0,
        "pending_sectors": 3,
        "uncorrectable_errors": 0,
        "temperature_celsius": 40.0,
    }])
    result = hh.run(now=1_000_000.0)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "pending_present" in kinds


def test_uncorrectable_error_alert_is_critical(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0",
        "ts": 1_000_000.0,
        "reallocated_sectors": 0,
        "pending_sectors": 0,
        "uncorrectable_errors": 1,
        "temperature_celsius": 40.0,
    }])
    result = hh.run(now=1_000_000.0)
    uncor = [a for a in result["alerts"] if a["kind"] == "uncorrectable"]
    assert len(uncor) == 1
    assert uncor[0]["severity"] == "critical"


def test_temperature_alert_needs_two_consecutive_readings(_tmp_workspace: Path) -> None:
    """Single hot reading is not enough; two consecutive must hit the
    threshold."""
    _write_jsonl(_tmp_workspace, [
        {
            "device": "disk0", "ts": 1_000_000.0,
            "reallocated_sectors": 0, "pending_sectors": 0,
            "uncorrectable_errors": 0, "temperature_celsius": 65.0,
        },
    ])
    result = hh.run(now=1_000_000.0)
    assert "temp_sustained" not in {a["kind"] for a in result["alerts"]}

    # Wait past internal cadence + second hot reading.
    _write_jsonl(_tmp_workspace, [
        {
            "device": "disk0", "ts": 1_000_000.0,
            "reallocated_sectors": 0, "pending_sectors": 0,
            "uncorrectable_errors": 0, "temperature_celsius": 65.0,
        },
        {
            "device": "disk0", "ts": 1_000_000.0 + 2 * 86400,
            "reallocated_sectors": 0, "pending_sectors": 0,
            "uncorrectable_errors": 0, "temperature_celsius": 67.0,
        },
    ])
    result = hh.run(now=1_000_000.0 + 2 * 86400)
    assert "temp_sustained" in {a["kind"] for a in result["alerts"]}


def test_stale_telemetry_alert(_tmp_workspace: Path) -> None:
    """If the most-recent reading is >14d old, surface a stale-
    telemetry alert (the host LaunchAgent probably died)."""
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
    }])
    far_future = 1_000_000.0 + 20 * 86400
    result = hh.run(now=far_future)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "stale_telemetry" in kinds


def test_tool_error_row_surfaces(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "*",
        "ts": 1_000_000.0,
        "error": "smartctl_not_installed",
    }])
    result = hh.run(now=1_000_000.0)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "tool_error" in kinds


def test_dedup_window_suppresses_repeats(monkeypatch, _tmp_workspace: Path) -> None:
    """Same alert key within 14d should not re-fire."""
    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 5,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
    }])
    hh.run(now=1_000_000.0)
    # Second pass past internal cadence but within dedup window.
    hh.run(now=1_000_000.0 + 2 * 86400)
    pending_alerts = [s for s in sent if "pending_present" in s.get("topic", "")]
    assert len(pending_alerts) == 1


def test_evaluate_handles_missing_optional_fields() -> None:
    """A reading with no SMART data shouldn't raise."""
    latest = {
        "disk0": hh.DiskReading(
            device="disk0", ts=1_000_000.0,
            reallocated_sectors=None, pending_sectors=None,
            uncorrectable_errors=None, temperature_celsius=None,
            power_on_hours=None, model=None, error=None,
        ),
    }
    alerts = hh.evaluate(latest, {"disk0": [latest["disk0"]]}, {}, now=1_000_000.0)
    assert alerts == []


def test_wear_warning_at_80pct(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "wear_pct": 85,
    }])
    result = hh.run(now=1_000_000.0)
    matching = [a for a in result["alerts"] if a["kind"] == "wear_high"]
    assert len(matching) == 1
    assert matching[0]["severity"] == "warning"


def test_wear_critical_at_100pct(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "wear_pct": 100,
    }])
    result = hh.run(now=1_000_000.0)
    kinds = {a["kind"]: a["severity"] for a in result["alerts"]}
    assert kinds.get("wear_exhausted") == "critical"


def test_wear_below_warning_does_not_alert(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "wear_pct": 50,
    }])
    result = hh.run(now=1_000_000.0)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "wear_high" not in kinds
    assert "wear_exhausted" not in kinds


def test_spare_low_uses_vendor_threshold(_tmp_workspace: Path) -> None:
    """When the drive supplies its own spare threshold, alert when
    spare ≤ that threshold (not the fallback)."""
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "spare_pct": 99, "spare_pct_threshold": 99,
    }])
    result = hh.run(now=1_000_000.0)
    matching = [a for a in result["alerts"] if a["kind"] == "spare_low"]
    assert len(matching) == 1
    assert matching[0]["severity"] == "critical"


def test_spare_low_falls_back_to_default_threshold(_tmp_workspace: Path) -> None:
    """When the drive doesn't supply a threshold, the monitor uses
    the conservative 10% fallback."""
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "spare_pct": 8,
    }])
    result = hh.run(now=1_000_000.0)
    assert "spare_low" in {a["kind"] for a in result["alerts"]}


def test_spare_above_threshold_does_not_alert(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
        "spare_pct": 100, "spare_pct_threshold": 10,
    }])
    result = hh.run(now=1_000_000.0)
    assert "spare_low" not in {a["kind"] for a in result["alerts"]}


def test_internal_cadence_gates_repeated_calls(_tmp_workspace: Path) -> None:
    _write_jsonl(_tmp_workspace, [{
        "device": "disk0", "ts": 1_000_000.0,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "uncorrectable_errors": 0, "temperature_celsius": 40.0,
    }])
    hh.run(now=1_000_000.0)
    second = hh.run(now=1_000_000.0 + 60)
    assert second["ran"] is False
