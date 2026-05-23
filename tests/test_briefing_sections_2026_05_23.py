"""Tests for new briefing sections shipped 2026-05-23 (PROGRAM §65.6 chip #3).

Two thin sections wrapping observability primitives that started firing
after §65's bug fixes:

  * ``hot1-patterns-by-kind`` — daily Counter over the HOT-1
    meta-affect pattern store (rolling 7-day window).
  * ``linter-rejections`` — Counter over thread-closure linter
    rejections via :func:`app.threads.linter_telemetry.summary`.

Both follow the ``briefing_sections`` contract (ID + DISPLAY_NAME +
DESCRIPTION + ``gather() -> list[str]``); both auto-hide when no data
has landed in the window.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.life_companion.briefing_sections import (
    hot1_patterns_by_kind,
    linter_rejections,
)


# ── Contract surface ────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [hot1_patterns_by_kind, linter_rejections])
def test_section_exposes_briefing_contract(module):
    """ID + DISPLAY_NAME + DESCRIPTION + gather() are present and shaped."""
    assert isinstance(module.ID, str) and module.ID.strip()
    assert isinstance(module.DISPLAY_NAME, str) and module.DISPLAY_NAME.strip()
    assert isinstance(module.DESCRIPTION, str) and module.DESCRIPTION.strip()
    assert callable(module.gather)
    result = module.gather()
    assert isinstance(result, list)
    assert all(isinstance(x, str) for x in result)


# ── Auto-hide invariant (the soft-fail contract) ────────────────────────


def test_hot1_patterns_autohides_when_no_rows(monkeypatch):
    """gather() returns [] when the underlying pattern store is empty."""
    from app.sentience_experiments import hot1_meta_affect
    monkeypatch.setattr(hot1_meta_affect, "list_recent", lambda n=200: [])
    assert hot1_patterns_by_kind.gather() == []


def test_hot1_patterns_autohides_when_all_rows_outside_window(monkeypatch):
    """gather() returns [] when every row is older than the rolling window."""
    from app.sentience_experiments import hot1_meta_affect
    old_ts = "2020-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        hot1_meta_affect, "list_recent",
        lambda n=200: [{"detected_at": old_ts, "pattern_kind": "temporal_cluster"}],
    )
    assert hot1_patterns_by_kind.gather() == []


def test_linter_rejections_autohides_when_lifetime_zero(monkeypatch):
    """gather() returns [] when telemetry reports no lifetime rejections."""
    from app.threads import linter_telemetry
    monkeypatch.setattr(linter_telemetry, "summary", lambda: {"total_rejections": 0})
    assert linter_rejections.gather() == []


# ── Populated path returns formatted lines ──────────────────────────────


def test_hot1_patterns_groups_by_kind(monkeypatch):
    """gather() returns one ``• kind: count`` line per detector kind."""
    from app.sentience_experiments import hot1_meta_affect
    now_iso = datetime.now(timezone.utc).isoformat()
    fake_rows = [
        {"detected_at": now_iso, "pattern_kind": "temporal_cluster"},
        {"detected_at": now_iso, "pattern_kind": "temporal_cluster"},
        {"detected_at": now_iso, "pattern_kind": "baseline_drift"},
    ]
    monkeypatch.setattr(hot1_meta_affect, "list_recent", lambda n=200: fake_rows)

    lines = hot1_patterns_by_kind.gather()
    # One line per kind that landed in window
    assert len(lines) == 2
    assert any("temporal_cluster" in line and "2" in line for line in lines)
    assert any("baseline_drift" in line and "1" in line for line in lines)


def test_linter_rejections_includes_lifetime_and_window_counts(monkeypatch, tmp_path):
    """Populated case: first line carries ``Nd window + lifetime`` summary."""
    from app.threads import linter_telemetry
    from app.life_companion.briefing_sections import linter_rejections as lr_mod

    monkeypatch.setattr(linter_telemetry, "summary", lambda: {"total_rejections": 42})
    monkeypatch.setattr(lr_mod, "_rejection_log_path", lambda: tmp_path / "missing.jsonl")

    lines = lr_mod.gather()
    assert lines  # Non-empty
    assert "42 lifetime" in lines[0]
    assert "0 in last" in lines[0]    # JSONL absent → window=0
