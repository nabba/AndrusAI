"""Tests for app.upgrade_lifecycle.ecosystem_snapshot (U6).

PROGRAM §62. Covers:

  1.  Python EOL section computes correct days
  2.  Future versions listed in section
  3.  Package health section from radar state
  4.  Framework health section calls fetcher for each framework
  5.  Vendor concentration section from cost fetcher
  6.  Major-upgrade proposals built from capability backlog
  7.  Major bumps detected only when major version changes
  8.  Snapshot generation is idempotent within a year
  9.  Markdown rendered to wiki path
  10. Master switch OFF returns None
  11. Accept marks row accepted + triggers CR for non-framework
  12. Accept routes framework to Tier-3 amendment
  13. Accept refuses unknown row
  14. Accept refuses double-acceptance
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import ecosystem_snapshot as eco
from app.upgrade_lifecycle.protocol import Capability


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(eco, "_enabled", lambda: True)


def _cap(pkg: str, from_v: str, to_v: str, **kwargs) -> Capability:
    return Capability(
        package=pkg, from_version=from_v, to_version=to_v,
        source="github_releases", extracted_at="2026-05-23T00:00:00+00:00",
        **kwargs,
    )


# ── 1-2: Python EOL section ─────────────────────────────────────────────


def test_python_eol_section_computes_days_until():
    section = eco.compose_python_eol_section(
        now=date(2026, 5, 23), current_minor="3.11",
    )
    assert section["current"] == "3.11"
    assert section["eol_date"] == "2027-10-31"
    # Roughly 525 days from 2026-05-23 to 2027-10-31
    assert 500 < section["days_until_eol"] < 550


def test_python_eol_section_lists_future_versions():
    section = eco.compose_python_eol_section(
        now=date(2026, 5, 23), current_minor="3.11",
    )
    versions = {row["version"] for row in section["future_versions"]}
    assert "3.12" in versions
    assert "3.13" in versions
    assert "3.14" in versions
    # 3.10 should NOT appear — it's older than 3.11
    assert "3.10" not in versions


# ── 3: Package health from radar state ──────────────────────────────────


def test_package_health_uses_dependency_radar_state():
    state = {
        "last_findings_by_severity": {
            "patch": 12, "minor": 4, "major": 2, "cve": 0,
        },
    }
    rows = eco.compose_package_health_section(dependency_radar_state=state)
    by_severity = {r["severity"]: r["count"] for r in rows}
    assert by_severity["patch"] == 12
    assert by_severity["minor"] == 4
    assert by_severity["major"] == 2


def test_package_health_empty_when_no_radar_state():
    rows = eco.compose_package_health_section(dependency_radar_state={})
    assert rows == []


# ── 4: Framework health ─────────────────────────────────────────────────


def test_framework_health_walks_known_frameworks():
    fetched = []

    def _fetcher(pkg):
        fetched.append(pkg)
        return {"latest_version": "9.9.9"}

    rows = eco.compose_framework_health_section(framework_fetcher=_fetcher)
    pkgs = {r["package"] for r in rows}
    for fw in ("crewai", "chromadb", "fastapi", "pydantic", "starlette", "anthropic"):
        assert fw in pkgs
    assert all(r["latest_version"] == "9.9.9" for r in rows)


# ── 5: Vendor concentration ─────────────────────────────────────────────


def test_vendor_concentration_uses_cost_fetcher():
    fetcher = lambda: {"anthropic": 0.6, "openrouter": 0.3, "ollama": 0.1}
    result = eco.compose_vendor_concentration_section(cost_fetcher=fetcher)
    assert result["anthropic"] == 0.6
    assert sum(result.values()) == pytest.approx(1.0)


def test_vendor_concentration_returns_empty_on_fetcher_error():
    def _exploding():
        raise RuntimeError("simulated")
    result = eco.compose_vendor_concentration_section(cost_fetcher=_exploding)
    assert result == {}


# ── 6-7: Major upgrade proposals ────────────────────────────────────────


def test_major_upgrade_proposals_built_from_caps():
    caps = [
        _cap("alpha", "1.0.0", "2.0.0",
             new_features=("foo", "bar"),
             security_fixes=("CVE-2026-XYZ",)),
        _cap("beta", "1.2.0", "1.3.0"),   # MINOR, not major — excluded
        _cap("crewai", "0.1.0", "1.0.0"),   # framework — included with framework=True
        _cap("gamma", "2.0.0", "3.0.0",
             breaking_changes=("removed A",)),
    ]
    proposals = eco.compose_major_upgrade_proposals(
        capability_iterator=lambda: caps,
    )
    packages = [p.package for p in proposals]
    assert "alpha" in packages
    assert "beta" not in packages          # minor bump
    assert "crewai" in packages            # framework major
    assert "gamma" in packages
    # Priority ordering: high (alpha — has CVE) first, then medium (crewai framework), then low (gamma)
    assert proposals[0].package == "alpha"
    assert proposals[0].priority == "high"
    # Capability summary present
    assert "2 new features" in proposals[0].capability_summary


def test_major_upgrade_proposals_dedup_by_pkg_version():
    cap1 = _cap("alpha", "1.0.0", "2.0.0", new_features=("a",))
    cap2 = _cap("alpha", "1.0.0", "2.0.0", new_features=("b",))   # dup
    proposals = eco.compose_major_upgrade_proposals(
        capability_iterator=lambda: [cap1, cap2],
    )
    pkgs = [p.package for p in proposals]
    assert pkgs.count("alpha") == 1


# ── 8-9: Snapshot generation ────────────────────────────────────────────


def test_generate_snapshot_writes_files_and_returns_object(isolated_dir, enabled):
    snapshot = eco.generate_snapshot(
        year=2026,
        now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        current_python_minor="3.11",
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    assert snapshot is not None
    assert snapshot.year == 2026

    # JSON snapshot persisted
    json_path = eco._snapshot_path_for_year(2026)
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["year"] == 2026

    # Markdown rendered
    md_path = eco._markdown_path_for_year(2026)
    assert md_path.exists()
    content = md_path.read_text()
    assert "Ecosystem snapshot — 2026" in content
    assert "Python EOL" in content


def test_generate_snapshot_idempotent_within_year(isolated_dir, enabled):
    snap1 = eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {}, capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    snap2 = eco.generate_snapshot(
        year=2026, now=datetime(2026, 6, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "y"},   # would change
        cost_fetcher=lambda: {}, capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    # Same generated_at — second call returned existing
    assert snap1.generated_at == snap2.generated_at


# ── 10: Master switch ───────────────────────────────────────────────────


def test_master_switch_off_returns_none(isolated_dir, monkeypatch):
    monkeypatch.setattr(eco, "_enabled", lambda: False)
    snap = eco.generate_snapshot(year=2026)
    assert snap is None


# ── 11-12: Accept routing ───────────────────────────────────────────────


def test_accept_routes_non_framework_to_cr(isolated_dir, enabled):
    # Seed a snapshot with one non-framework major
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("alpha", "1.0.0", "2.0.0", new_features=("foo",)),
        ],
        dependency_radar_state={},
    )

    cr_calls: list[dict] = []
    tier3_calls: list[dict] = []

    def _cr_filer(**kw):
        cr_calls.append(kw)
        return "cr-1234"

    def _tier3_proposer(**kw):
        tier3_calls.append(kw)
        return "tier3-xyz"

    result = eco.accept_major_upgrade(
        year=2026, package="alpha", to_version="2.0.0",
        cr_filer=_cr_filer, tier3_proposer=_tier3_proposer,
    )
    assert result["ok"] is True
    assert result["cr_id"] == "cr-1234"
    assert len(cr_calls) == 1
    assert len(tier3_calls) == 0
    # The row in the persisted snapshot is now accepted
    snap = eco._read_snapshot(2026)
    rows = [m for m in snap.major_upgrades if m.package == "alpha"]
    assert rows[0].status == "accepted"
    assert rows[0].cr_id == "cr-1234"


def test_accept_routes_framework_to_tier3(isolated_dir, enabled):
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("crewai", "0.1.0", "1.0.0"),
        ],
        dependency_radar_state={},
    )

    cr_calls: list[dict] = []
    tier3_calls: list[dict] = []

    result = eco.accept_major_upgrade(
        year=2026, package="crewai", to_version="1.0.0",
        cr_filer=lambda **kw: cr_calls.append(kw) or "cr-x",
        tier3_proposer=lambda **kw: tier3_calls.append(kw) or "tier3-y",
    )
    assert result["ok"] is True
    assert len(tier3_calls) == 1
    assert len(cr_calls) == 0
    assert result["cr_id"] == "tier3-y"


def test_accept_unknown_row_returns_error(isolated_dir, enabled):
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {}, capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    result = eco.accept_major_upgrade(
        year=2026, package="nonexistent", to_version="9.9.9",
        cr_filer=lambda **kw: "cr-x",
        tier3_proposer=lambda **kw: "tier3-y",
    )
    assert result["ok"] is False
    assert result["reason"] == "row_not_found"


def test_generate_snapshot_auto_detects_python_minor(isolated_dir, enabled, monkeypatch):
    """When current_python_minor isn't passed, sys.version_info wins."""
    import sys
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    snap = eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        # current_python_minor deliberately omitted
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    assert snap is not None
    assert snap.python_eol["current"] == expected


def test_force_regenerate_overwrites_but_preserves_acceptance(isolated_dir, enabled):
    """force=True regenerates the snapshot but preserves accepted rows."""
    # 1) First generation with one accepted row.
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("alpha", "1.0.0", "2.0.0", new_features=("foo",)),
        ],
        dependency_radar_state={},
    )
    eco.accept_major_upgrade(
        year=2026, package="alpha", to_version="2.0.0",
        cr_filer=lambda **kw: "cr-1",
        tier3_proposer=lambda **kw: "tier3-1",
    )

    # 2) Force-regenerate with the same iterator + a new beta candidate.
    snap2 = eco.generate_snapshot(
        year=2026, now=datetime(2026, 6, 1, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "y"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("alpha", "1.0.0", "2.0.0", new_features=("foo",)),
            _cap("beta", "1.0.0", "2.0.0"),
        ],
        dependency_radar_state={},
        force=True,
    )
    assert snap2 is not None
    pkgs = {m.package: m for m in snap2.major_upgrades}
    # alpha row preserved acceptance
    assert pkgs["alpha"].status == "accepted"
    assert pkgs["alpha"].cr_id == "cr-1"
    # beta is freshly proposed
    assert pkgs["beta"].status == "proposed"


def test_force_regenerate_with_no_existing_snapshot_still_writes(isolated_dir, enabled):
    """force=True on a missing snapshot just generates normally."""
    snap = eco.generate_snapshot(
        year=2099, now=datetime(2099, 6, 1, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {}, capability_iterator=lambda: [],
        dependency_radar_state={},
        force=True,
    )
    assert snap is not None
    assert eco._snapshot_path_for_year(2099).exists()


# ── P0#4: Python proposal surfaces in snapshot ─────────────────────────


def test_python_proposal_surfaces_when_eol_within_year():
    """3.11 EOL is 2027-10-31 — close enough to today (mocked to 2027-05-01)
    to surface as a row."""
    from datetime import date as _date
    row = eco.compose_python_proposal(
        current_minor="3.11", now=_date(2027, 5, 1),
    )
    assert row is not None
    assert row.package == "python"
    assert row.from_version == "3.11"
    # Next minor in the table
    assert row.to_version == "3.12"
    assert row.priority in ("high", "medium")


def test_python_proposal_returns_none_when_far_from_eol():
    """3.14 EOL is 2030-10-31 — far enough away that no row appears."""
    from datetime import date as _date
    row = eco.compose_python_proposal(
        current_minor="3.14", now=_date(2027, 5, 1),
    )
    assert row is None


def test_python_proposal_returns_none_for_unknown_minor():
    from datetime import date as _date
    row = eco.compose_python_proposal(
        current_minor="3.99", now=_date(2027, 5, 1),
    )
    assert row is None


# ── B2-P2: framework-acceptance side effects ─────────────────────────


def test_framework_accept_fires_signal_alert(isolated_dir, enabled, monkeypatch):
    """Framework row Accept → critical Signal alert + framework_migration_started in result."""
    notified = []
    monkeypatch.setattr(
        eco, "_notify_framework_migration_started",
        lambda **kw: notified.append(kw),
    )
    monkeypatch.setattr(
        eco, "_create_framework_migration_thread",
        lambda **kw: "thread-1",
    )

    # Seed a snapshot with a framework row
    monkeypatch.setattr(
        eco, "compose_python_proposal",
        lambda current_minor, now=None, horizon_days=365: None,
    )
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("crewai", "0.1.0", "1.0.0"),
        ],
        dependency_radar_state={},
    )

    result = eco.accept_major_upgrade(
        year=2026, package="crewai", to_version="1.0.0",
        cr_filer=lambda **kw: "cr-no",
        tier3_proposer=lambda **kw: "tier3-1",
    )
    assert result["ok"] is True
    assert result.get("framework_migration_started") is True
    assert result["thread_id"] == "thread-1"
    assert len(notified) == 1
    assert notified[0]["package"] == "crewai"


def test_non_framework_accept_does_not_fire_framework_signal(
    isolated_dir, enabled, monkeypatch,
):
    """Non-framework row Accept → no framework-migration side effects."""
    notified = []
    monkeypatch.setattr(
        eco, "_notify_framework_migration_started",
        lambda **kw: notified.append(kw),
    )

    monkeypatch.setattr(
        eco, "compose_python_proposal",
        lambda current_minor, now=None, horizon_days=365: None,
    )
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("alpha", "1.0.0", "2.0.0"),
        ],
        dependency_radar_state={},
    )
    result = eco.accept_major_upgrade(
        year=2026, package="alpha", to_version="2.0.0",
        cr_filer=lambda **kw: "cr-1",
    )
    assert result["ok"] is True
    assert "framework_migration_started" not in result
    assert notified == []


def test_framework_accept_survives_thread_creation_failure(
    isolated_dir, enabled, monkeypatch,
):
    """Thread auto-creation failure does NOT block the acceptance."""
    monkeypatch.setattr(eco, "_notify_framework_migration_started", lambda **kw: None)
    monkeypatch.setattr(
        eco, "_create_framework_migration_thread",
        lambda **kw: None,    # thread module unavailable
    )
    monkeypatch.setattr(
        eco, "compose_python_proposal",
        lambda current_minor, now=None, horizon_days=365: None,
    )
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("crewai", "0.1.0", "1.0.0"),
        ],
        dependency_radar_state={},
    )
    result = eco.accept_major_upgrade(
        year=2026, package="crewai", to_version="1.0.0",
        cr_filer=lambda **kw: "cr-x",
        tier3_proposer=lambda **kw: "tier3-1",
    )
    assert result["ok"] is True
    assert result["framework_migration_started"] is True
    assert result["thread_id"] is None    # gracefully None


def test_accept_python_row_uses_bump_python_action(isolated_dir, enabled, monkeypatch):
    """Accepting a python row builds a CR body with action=bump_python."""
    # Force the snapshot to include a Python row by pinning the date.
    from datetime import date as _date
    monkeypatch.setattr(
        eco, "compose_python_proposal",
        lambda current_minor, now=None, horizon_days=365: eco.MajorUpgradeProposal(
            package="python", from_version="3.13", to_version="3.14",
            priority="high", is_framework=False,
            capability_summary="EOL test",
        ),
    )
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    cr_calls: list[dict] = []
    eco.accept_major_upgrade(
        year=2026, package="python", to_version="3.14",
        cr_filer=lambda **kw: cr_calls.append(kw) or "cr-py-1",
        tier3_proposer=lambda **kw: "tier3-x",
    )
    assert len(cr_calls) == 1
    body = cr_calls[0]["new_content"]
    assert "action: bump_python" in body
    assert "package: python" in body
    assert "from_version: 3.13" in body
    assert "to_version: 3.14" in body
    # Operator-facing markdown explains the SHA pin drop
    assert "drop" in body.lower() and "SHA" in body


def test_accept_double_acceptance_blocks(isolated_dir, enabled):
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [
            _cap("alpha", "1.0.0", "2.0.0"),
        ],
        dependency_radar_state={},
    )
    eco.accept_major_upgrade(
        year=2026, package="alpha", to_version="2.0.0",
        cr_filer=lambda **kw: "cr-1",
        tier3_proposer=lambda **kw: "tier3-1",
    )
    # Re-accept
    result = eco.accept_major_upgrade(
        year=2026, package="alpha", to_version="2.0.0",
        cr_filer=lambda **kw: "cr-2",
        tier3_proposer=lambda **kw: "tier3-2",
    )
    assert result["ok"] is False
    assert result["reason"] == "already_accepted"
    assert result["cr_id"] == "cr-1"   # original preserved
