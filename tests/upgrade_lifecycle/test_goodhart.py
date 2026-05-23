"""Tests for app.upgrade_lifecycle.goodhart (U9).

PROGRAM §62. Covers the three Goodhart-resistance behaviors:

  1. Rejection-rate computation from audit log
  2. MAJOR auto-CR window widens at high rejection rate
  3. MAJOR auto-CR window restores at low rejection rate
  4. Tiny sample doesn't trigger throttle
  5. Capability-adoption pause triggers above threshold
  6. Capability-adoption pause is read by U5 gate
  7. Per-package rollback cooldown detection
  8. Rollback cooldown expires after 90 days
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import goodhart


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    path = tmp_path / "audit.jsonl"
    return path


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point throttle/pause state files into tmp_path."""
    monkeypatch.setattr(
        goodhart, "_major_state_path",
        lambda: tmp_path / "major_throttle.json",
    )
    monkeypatch.setattr(
        goodhart, "_adoption_state_path",
        lambda: tmp_path / "adoption_pause.json",
    )
    return tmp_path


def _write_audit(path: Path, rows: list[dict]) -> None:
    """Write a tiny JSONL audit log fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _audit_row(*, status: str, ts: str, requestor: str = "dependency_radar",
              title: str = "test cr", new_content: str = "") -> dict:
    return {
        "ts": ts, "status": status, "requestor": requestor,
        "title": title, "new_content": new_content,
    }


# ── 1: Rejection-rate computation ───────────────────────────────────────


def test_rejection_rate_computes_correctly(audit_path):
    """3 applied + 1 rejected + 1 rolled_back → rate 2/5 = 0.4."""
    _write_audit(audit_path, [
        _audit_row(status="applied", ts="2026-05-01T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-02T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-03T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-04T00:00:00+00:00"),
        _audit_row(status="rolled_back", ts="2026-05-05T00:00:00+00:00"),
    ])
    rate, sample = goodhart.compute_rejection_rate(
        requestor="dependency_radar",
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert sample == 5
    assert rate == pytest.approx(0.4)


def test_rejection_rate_respects_window(audit_path):
    """Rows outside the window aren't counted."""
    _write_audit(audit_path, [
        # 200 days ago — outside 90d window
        _audit_row(status="rejected", ts="2025-11-01T00:00:00+00:00"),
        # Inside window
        _audit_row(status="applied", ts="2026-05-01T00:00:00+00:00"),
    ])
    rate, sample = goodhart.compute_rejection_rate(
        requestor="dependency_radar",
        window_days=90,
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert sample == 1
    assert rate == 0.0


# ── 2-4: MAJOR auto-CR window throttle ──────────────────────────────────


def test_major_window_widens_on_high_rejection(isolated_state, audit_path):
    """Rejection rate > 40 % with sample >= 5 → 30d → 60d."""
    rows = [
        _audit_row(status="rejected", ts="2026-05-01T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-02T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-03T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-04T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-05T00:00:00+00:00"),
    ]
    _write_audit(audit_path, rows)
    window = goodhart.evaluate_major_window(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert window == 60   # widened
    # Persisted
    assert goodhart.current_major_window() == 60


def test_major_window_restores_on_low_rejection(isolated_state, audit_path):
    """After widening, low rejection → restores to 30d."""
    # First widen the window
    _write_audit(audit_path, [
        _audit_row(status="rejected", ts="2026-05-01T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-02T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-03T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-04T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-05-05T00:00:00+00:00"),
    ])
    goodhart.evaluate_major_window(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert goodhart.current_major_window() == 60

    # Now flip to low-rejection
    _write_audit(audit_path, [
        _audit_row(status="applied", ts="2026-06-01T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-06-02T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-06-03T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-06-04T00:00:00+00:00"),
        _audit_row(status="applied", ts="2026-06-05T00:00:00+00:00"),
    ])
    window = goodhart.evaluate_major_window(
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert window == 30


def test_major_window_doesnt_widen_on_small_sample(isolated_state, audit_path):
    """3 rejections / 3 total — sample too small to throttle."""
    _write_audit(audit_path, [
        _audit_row(status="rejected", ts="2026-05-01T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-02T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-03T00:00:00+00:00"),
    ])
    window = goodhart.evaluate_major_window(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert window == 30   # default — sample too small to throttle


# ── 5-6: Capability-adoption pause ──────────────────────────────────────


def test_adoption_pause_triggers_above_threshold(isolated_state, audit_path):
    """6 rejected / 8 total → pause."""
    rows = []
    for i in range(6):
        rows.append(_audit_row(
            status="rejected", ts=f"2026-05-0{i+1}T00:00:00+00:00",
        ))
    rows.append(_audit_row(status="applied", ts="2026-05-07T00:00:00+00:00"))
    rows.append(_audit_row(status="applied", ts="2026-05-08T00:00:00+00:00"))
    _write_audit(audit_path, rows)
    paused = goodhart.evaluate_adoption_pause(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert paused is not None
    # Pause registered → is_adoption_paused reads True
    assert goodhart.is_adoption_paused(
        now=datetime(2026, 5, 24, tzinfo=timezone.utc),
    ) is True


def test_adoption_pause_skips_with_small_sample(isolated_state, audit_path):
    _write_audit(audit_path, [
        _audit_row(status="rejected", ts="2026-05-01T00:00:00+00:00"),
        _audit_row(status="rejected", ts="2026-05-02T00:00:00+00:00"),
    ])
    paused = goodhart.evaluate_adoption_pause(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert paused is None


def test_is_adoption_paused_default_false(isolated_state):
    """No state file → not paused."""
    assert goodhart.is_adoption_paused() is False


def test_adoption_pause_expires_after_window(isolated_state, audit_path):
    """Pause auto-expires after the 30d window."""
    # Trigger pause
    rows = [_audit_row(status="rejected", ts=f"2026-05-0{i+1}T00:00:00+00:00")
           for i in range(6)]
    rows.extend([_audit_row(status="applied", ts=f"2026-05-{i+10}T00:00:00+00:00")
                for i in range(2)])
    _write_audit(audit_path, rows)
    goodhart.evaluate_adoption_pause(
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert goodhart.is_adoption_paused(
        now=datetime(2026, 6, 5, tzinfo=timezone.utc),
    ) is True
    # 40 days later — pause expired
    assert goodhart.is_adoption_paused(
        now=datetime(2026, 7, 5, tzinfo=timezone.utc),
    ) is False


# ── 7-8: Per-package rollback cooldown ──────────────────────────────────


def test_rollback_cooldown_detected(audit_path):
    _write_audit(audit_path, [
        _audit_row(
            status="rolled_back", ts="2026-05-01T00:00:00+00:00",
            title="Upgrade starlette 0.52.1 → 1.0.1",
            new_content="starlette==1.0.1",
        ),
    ])
    in_cooldown = goodhart.is_package_in_rollback_cooldown(
        "starlette", "1.0.1",
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert in_cooldown is True


def test_rollback_cooldown_expires_after_90_days(audit_path):
    _write_audit(audit_path, [
        _audit_row(
            status="rolled_back", ts="2026-01-01T00:00:00+00:00",
            title="Upgrade starlette 0.52.1 → 1.0.1",
            new_content="starlette==1.0.1",
        ),
    ])
    in_cooldown = goodhart.is_package_in_rollback_cooldown(
        "starlette", "1.0.1",
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert in_cooldown is False   # > 90 days


def test_rollback_cooldown_doesnt_affect_other_versions(audit_path):
    _write_audit(audit_path, [
        _audit_row(
            status="rolled_back", ts="2026-05-01T00:00:00+00:00",
            title="Upgrade starlette 0.52.1 → 1.0.1",
            new_content="starlette==1.0.1",
        ),
    ])
    in_cooldown = goodhart.is_package_in_rollback_cooldown(
        "starlette", "1.0.2",
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
        audit_path=audit_path,
    )
    assert in_cooldown is False
