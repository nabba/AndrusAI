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
