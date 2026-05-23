"""Tests for app.healing.monitors.dockerfile_pin_staleness (A3-P1)."""
from __future__ import annotations

import pytest

# Healing module pulls app.config → pydantic_settings.
pytest.importorskip("pydantic_settings")

from app.healing.monitors import dockerfile_pin_staleness as dps   # noqa: E402


# ── Pure check function ────────────────────────────────────────────────


def test_check_no_alert_when_pinned():
    """All FROM lines have @sha256: → no alert regardless of TODO marker."""
    text = (
        "FROM python:3.13-slim@sha256:" + "a" * 64 + "\n"
        "WORKDIR /app\n"
    )
    should, total, unpinned = dps.check_dockerfile(text)
    assert should is False
    assert total == 1
    assert unpinned == 0


def test_check_no_alert_when_unpinned_without_todo_marker():
    """Unpinned but no TODO marker → operator deliberately didn't pin."""
    text = "FROM python:3.13-slim\nWORKDIR /app\n"
    should, total, unpinned = dps.check_dockerfile(text)
    assert should is False
    assert total == 1
    assert unpinned == 1


def test_check_alerts_when_todo_marker_plus_unpinned():
    text = (
        "FROM python:3.14-slim\n"
        "# TODO P0#4: re-pin Dockerfile SHA digest. Previous "
        "line carried 'sha256:abc...' which would have anchored "
        "to the OLD 3.13 image.\n"
        "WORKDIR /app\n"
    )
    should, total, unpinned = dps.check_dockerfile(text)
    assert should is True
    assert total == 1
    assert unpinned == 1


def test_check_multi_stage_one_pinned_one_not_with_marker_alerts():
    """Mixed-state multi-stage with marker → alert (one line still unpinned)."""
    text = (
        "FROM python:3.14-slim@sha256:" + "a" * 64 + " AS builder\n"
        "RUN pip wheel\n"
        "FROM python:3.14-slim AS runtime\n"
        "# TODO P0#4: re-pin\n"
    )
    should, total, unpinned = dps.check_dockerfile(text)
    assert should is True
    assert total == 2
    assert unpinned == 1


# ── run() integration ─────────────────────────────────────────────────


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "healing"
    state_dir.mkdir()
    monkeypatch.setattr(
        dps, "_state_path", lambda: state_dir / "pin_state.json",
    )
    return state_dir


def test_run_no_alert_when_clean_pinned(isolated_state, tmp_path, monkeypatch):
    """Clean pinned Dockerfile → run() silent + no state change."""
    monkeypatch.setattr(dps, "_enabled", lambda: True)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-slim@sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(dps, "_dockerfile_path", lambda: dockerfile)
    notified = []
    monkeypatch.setattr(dps, "_notify", lambda body: notified.append(body))
    dps.run()
    assert notified == []


def test_run_alerts_when_unpinned_with_marker(isolated_state, tmp_path, monkeypatch):
    monkeypatch.setattr(dps, "_enabled", lambda: True)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.14-slim\n"
        "# TODO P0#4: re-pin the digest\n"
    )
    monkeypatch.setattr(dps, "_dockerfile_path", lambda: dockerfile)
    notified = []
    monkeypatch.setattr(dps, "_notify", lambda body: notified.append(body))
    dps.run()
    assert len(notified) == 1
    assert "FROM python:" in notified[0]
    assert "TODO P0#4: re-pin" in notified[0]


def test_run_dedups_within_week(isolated_state, tmp_path, monkeypatch):
    """Two runs back-to-back → only one alert fires."""
    monkeypatch.setattr(dps, "_enabled", lambda: True)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.14-slim\n"
        "# TODO P0#4: re-pin\n"
    )
    monkeypatch.setattr(dps, "_dockerfile_path", lambda: dockerfile)
    notified = []
    monkeypatch.setattr(dps, "_notify", lambda body: notified.append(body))
    dps.run()
    dps.run()
    assert len(notified) == 1


def test_run_master_switch_off(isolated_state, tmp_path, monkeypatch):
    monkeypatch.setattr(dps, "_enabled", lambda: False)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.14-slim\n# TODO P0#4: re-pin\n")
    monkeypatch.setattr(dps, "_dockerfile_path", lambda: dockerfile)
    notified = []
    monkeypatch.setattr(dps, "_notify", lambda body: notified.append(body))
    dps.run()
    assert notified == []
