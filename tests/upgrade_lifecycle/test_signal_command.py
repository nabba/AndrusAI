"""Tests for the /upgrade Signal slash command (F4).

PROGRAM §63 follow-up. Covers the handler's branches:

  1. Bare /upgrade returns status summary
  2. /upgrade help shows the help block
  3. /upgrade budget reports quarterly figures
  4. /upgrade capabilities <pkg> reads from the ledger
  5. /upgrade trial <pkg> <from> <to> enqueues a trial request
  6. /upgrade snapshot reports the active year's summary
  7. /upgrade snapshot for an unknown year reports nicely
  8. Unknown subcommand falls through to help
  9. Non-/upgrade input returns None (delegates to next handler)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Skip when the commander module can't import (host venv missing pydantic_settings).
pytest.importorskip("pydantic_settings")

from app.agents.commander.commands import _handle_upgrade_command  # noqa: E402


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


def test_non_upgrade_input_returns_none(isolated_dir):
    assert _handle_upgrade_command("/hello") is None


def test_bare_upgrade_returns_status_summary(isolated_dir):
    out = _handle_upgrade_command("/upgrade")
    assert out is not None
    assert "Upgrade lifecycle" in out or "Budget Q-spend" in out


def test_upgrade_help_block(isolated_dir):
    out = _handle_upgrade_command("/upgrade help")
    assert out is not None
    assert "capabilities" in out
    assert "trial" in out
    assert "snapshot" in out


def test_upgrade_budget_includes_dollar_figures(isolated_dir):
    out = _handle_upgrade_command("/upgrade budget")
    assert out is not None
    assert "$" in out


def test_upgrade_capabilities_needs_pkg(isolated_dir):
    out = _handle_upgrade_command("/upgrade capabilities")
    assert out == "Usage: /upgrade capabilities <package>"


def test_upgrade_capabilities_reports_empty(isolated_dir):
    out = _handle_upgrade_command("/upgrade capabilities ghost-pkg")
    assert "No capability rows" in out


def test_upgrade_capabilities_reads_persisted(isolated_dir):
    """Persist a capability row and confirm the command reads it."""
    from app.upgrade_lifecycle.changelog_fetcher import _persist
    from app.upgrade_lifecycle.protocol import Capability
    _persist(Capability(
        package="alpha", from_version="1.0", to_version="2.0",
        source="github_releases", extracted_at="2026-05-23T00:00:00+00:00",
        new_features=("a", "b"),
    ))
    out = _handle_upgrade_command("/upgrade capabilities alpha")
    assert "alpha" in out
    assert "1.0 → 2.0" in out
    assert "2nf" in out      # 2 new_features


def test_upgrade_trial_needs_three_args(isolated_dir):
    assert "Usage" in _handle_upgrade_command("/upgrade trial")
    assert "Usage" in _handle_upgrade_command("/upgrade trial alpha")
    assert "Usage" in _handle_upgrade_command("/upgrade trial alpha 1.0")


def test_upgrade_trial_enqueues(isolated_dir):
    out = _handle_upgrade_command("/upgrade trial alpha 1.0 2.0")
    assert "queued" in out.lower()
    # Pending file written
    pending = isolated_dir / "trials" / "_pending.jsonl"
    assert pending.exists()
    row = json.loads(pending.read_text().splitlines()[0])
    assert row["package"] == "alpha"
    assert row["to_version"] == "2.0"


def test_upgrade_snapshot_unknown_year(isolated_dir):
    out = _handle_upgrade_command("/upgrade snapshot 2099")
    assert "No snapshot" in out


def test_upgrade_snapshot_reports_summary(isolated_dir, monkeypatch):
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    out = _handle_upgrade_command("/upgrade snapshot 2026")
    assert "Ecosystem snapshot 2026" in out
    assert "Majors:" in out


def test_unknown_subcommand_falls_through_to_help(isolated_dir):
    out = _handle_upgrade_command("/upgrade bogus")
    assert "capabilities" in out and "trial" in out
