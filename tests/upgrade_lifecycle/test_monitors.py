"""Tests for the two U8 healing monitors.

PROGRAM §62. Covers:

  1. upgrade_lifecycle_health: master switch + 3 alerts
  2. python_eol_proximity: threshold firing + dedup state
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# The healing package imports app.config, which depends on
# pydantic_settings. Skip the whole module when that isn't installed
# (host venv without full deps); container env has it.
pytest.importorskip("pydantic_settings")

from app.healing.monitors import (  # noqa: E402
    python_eol_proximity as eol_mon,
    upgrade_lifecycle_health as health_mon,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Redirect both monitors' state files into tmp_path."""
    state_dir = tmp_path / "healing"
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(health_mon, "_state_path",
                       lambda: state_dir / "health_state.json")
    monkeypatch.setattr(eol_mon, "_state_path",
                       lambda: state_dir / "eol_state.json")
    return state_dir


# ── upgrade_lifecycle_health ────────────────────────────────────────────


def test_health_monitor_master_switch_off(isolated_workspace, monkeypatch):
    monkeypatch.setattr(health_mon, "_enabled", lambda: False)
    notified: list = []
    monkeypatch.setattr(health_mon, "_notify",
                       lambda *args, **kw: notified.append(kw))
    health_mon.run()
    assert notified == []


def test_health_monitor_alerts_on_stale_capabilities(isolated_workspace, monkeypatch):
    """Aged capability file → backlog alert fires."""
    cap_dir = isolated_workspace / "capabilities"
    cap_dir.mkdir()
    old_file = cap_dir / "starlette.jsonl"
    old_file.write_text("")
    # Set mtime to 40 days ago
    import os
    old_ts = datetime.now(timezone.utc).timestamp() - 40 * 86400
    os.utime(str(old_file), (old_ts, old_ts))

    monkeypatch.setattr(health_mon, "_enabled", lambda: True)
    from app.upgrade_lifecycle import changelog_fetcher
    monkeypatch.setattr(changelog_fetcher, "_capabilities_dir", lambda: cap_dir)

    notified: list = []
    monkeypatch.setattr(
        health_mon, "_notify",
        lambda *args, **kw: notified.append({"args": args, "kw": kw}),
    )
    health_mon.run()
    bodies = [n["kw"].get("body", "") for n in notified]
    assert any("backlog stale" in b for b in bodies)


def test_health_monitor_alerts_on_unread_snapshot(isolated_workspace, monkeypatch):
    """A6-P1: snapshot with rows pending + 0 accepted for > 90d → alert."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(health_mon, "_enabled", lambda: True)
    monkeypatch.setattr(
        health_mon, "_check_capability_backlog_stale", lambda now: None,
    )
    monkeypatch.setattr(
        health_mon, "_check_repeated_trial_failure", lambda now: [],
    )
    monkeypatch.setattr(health_mon, "_check_budget_burn", lambda now: None)

    # Stub a snapshot with 3 rows, all proposed, generated 100 days ago.
    class _Row:
        status = "proposed"
    class _Snap:
        year = 2026
        generated_at = (
            datetime.now(timezone.utc) - timedelta(days=100)
        ).isoformat()
        major_upgrades = [_Row(), _Row(), _Row()]

    monkeypatch.setattr(
        "app.upgrade_lifecycle.ecosystem_snapshot._read_snapshot",
        lambda year: _Snap(),
    )

    notified = []
    monkeypatch.setattr(
        health_mon, "_notify",
        lambda *args, **kw: notified.append(kw),
    )
    health_mon.run()
    bodies = [n.get("body", "") for n in notified]
    assert any("unread for" in b for b in bodies)


def test_health_monitor_silent_when_snapshot_recent(isolated_workspace, monkeypatch):
    """Snapshot generated within 90 days → no alert even if unread."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(health_mon, "_enabled", lambda: True)
    monkeypatch.setattr(
        health_mon, "_check_capability_backlog_stale", lambda now: None,
    )
    monkeypatch.setattr(
        health_mon, "_check_repeated_trial_failure", lambda now: [],
    )
    monkeypatch.setattr(health_mon, "_check_budget_burn", lambda now: None)

    class _Row:
        status = "proposed"
    class _Snap:
        year = 2026
        generated_at = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        major_upgrades = [_Row()]

    monkeypatch.setattr(
        "app.upgrade_lifecycle.ecosystem_snapshot._read_snapshot",
        lambda year: _Snap(),
    )
    notified = []
    monkeypatch.setattr(
        health_mon, "_notify", lambda *args, **kw: notified.append(kw),
    )
    health_mon.run()
    assert notified == []


def test_health_monitor_silent_when_at_least_one_accepted(isolated_workspace, monkeypatch):
    """Snapshot has ≥1 accepted row → no alert (operator IS engaged)."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(health_mon, "_enabled", lambda: True)
    monkeypatch.setattr(
        health_mon, "_check_capability_backlog_stale", lambda now: None,
    )
    monkeypatch.setattr(
        health_mon, "_check_repeated_trial_failure", lambda now: [],
    )
    monkeypatch.setattr(health_mon, "_check_budget_burn", lambda now: None)

    class _Proposed:
        status = "proposed"
    class _Accepted:
        status = "accepted"
    class _Snap:
        year = 2026
        generated_at = (
            datetime.now(timezone.utc) - timedelta(days=200)
        ).isoformat()
        major_upgrades = [_Proposed(), _Accepted()]

    monkeypatch.setattr(
        "app.upgrade_lifecycle.ecosystem_snapshot._read_snapshot",
        lambda year: _Snap(),
    )
    notified = []
    monkeypatch.setattr(
        health_mon, "_notify", lambda *args, **kw: notified.append(kw),
    )
    health_mon.run()
    assert notified == []


def test_health_monitor_dedups_within_week(isolated_workspace, monkeypatch):
    """Two runs back-to-back: second is suppressed by INTERNAL_WEEKLY_S."""
    monkeypatch.setattr(health_mon, "_enabled", lambda: True)
    monkeypatch.setattr(health_mon, "_check_capability_backlog_stale", lambda now: None)
    monkeypatch.setattr(health_mon, "_check_repeated_trial_failure", lambda now: [])
    monkeypatch.setattr(health_mon, "_check_budget_burn", lambda now: None)

    notified: list = []
    monkeypatch.setattr(
        health_mon, "_notify", lambda *args, **kw: notified.append(kw),
    )
    health_mon.run()
    notified_first = len(notified)
    health_mon.run()
    # Same as before — second run guarded.
    assert len(notified) == notified_first


# ── python_eol_proximity ────────────────────────────────────────────────


def test_eol_monitor_master_switch_off(isolated_workspace, monkeypatch):
    monkeypatch.setattr(eol_mon, "_enabled", lambda: False)
    notified: list = []
    monkeypatch.setattr(eol_mon, "_notify", lambda *args, **kw: notified.append(kw))
    eol_mon.run()
    assert notified == []


def test_eol_monitor_alerts_at_12month_threshold(isolated_workspace, monkeypatch):
    """When EOL is in 350 days, the 12-month threshold (365d) fires."""
    monkeypatch.setattr(eol_mon, "_enabled", lambda: True)
    monkeypatch.setattr(eol_mon, "_current_python_minor", lambda: "3.11")
    # EOL is 350 days from today
    eol = date.today().replace() + (date(date.today().year, 12, 31) - date.today())
    eol_350_days_away = date.today()
    # Build a concrete future date
    import datetime as _dt
    eol_target = _dt.date.today() + _dt.timedelta(days=350)
    monkeypatch.setattr(eol_mon, "_eol_date_for", lambda v: eol_target)

    notified: list = []
    monkeypatch.setattr(
        eol_mon, "_notify",
        lambda *args, **kw: notified.append({"args": args, "kw": kw}),
    )
    eol_mon.run()
    assert len(notified) >= 1
    body = notified[0]["kw"].get("body", "")
    assert "350" in body or "Python 3.11" in body


def test_eol_monitor_dedups_threshold_after_fire(isolated_workspace, monkeypatch):
    """Second run on same day shouldn't re-fire same threshold."""
    monkeypatch.setattr(eol_mon, "_enabled", lambda: True)
    monkeypatch.setattr(eol_mon, "_current_python_minor", lambda: "3.11")
    import datetime as _dt
    eol_target = _dt.date.today() + _dt.timedelta(days=350)
    monkeypatch.setattr(eol_mon, "_eol_date_for", lambda v: eol_target)

    notified: list = []
    monkeypatch.setattr(
        eol_mon, "_notify",
        lambda *args, **kw: notified.append(kw),
    )
    eol_mon.run()
    after_first = len(notified)
    eol_mon.run()
    # State persisted — second run sees the threshold already fired.
    assert len(notified) == after_first


def test_eol_monitor_unknown_version_alerts_once(isolated_workspace, monkeypatch):
    """Unknown Python version → single alert + state file marks fired."""
    monkeypatch.setattr(eol_mon, "_enabled", lambda: True)
    monkeypatch.setattr(eol_mon, "_current_python_minor", lambda: "3.42")
    monkeypatch.setattr(eol_mon, "_eol_date_for", lambda v: None)

    notified: list = []
    monkeypatch.setattr(
        eol_mon, "_notify",
        lambda *args, **kw: notified.append(kw),
    )
    eol_mon.run()
    eol_mon.run()
    # Only one alert
    assert len(notified) == 1
    body = notified[0].get("body", "")
    assert "Python 3.42" in body
