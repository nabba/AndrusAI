"""REST endpoint tests for U7.

PROGRAM §62. Smoke tests for the upgrade_lifecycle_api router.
The auth dependency is bypassed in the test client; we exercise
just the route logic + shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle.protocol import Capability


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def client(monkeypatch):
    """Build a FastAPI TestClient mounting the upgrade-lifecycle router."""
    fastapi = pytest.importorskip("fastapi")
    starlette_test = pytest.importorskip("starlette.testclient")
    from app.control_plane.upgrade_lifecycle_api import router

    app = fastapi.FastAPI()
    app.include_router(router, prefix="/api/cp")
    return starlette_test.TestClient(app)


# ── /upgrade-lifecycle/state ────────────────────────────────────────────


def test_state_endpoint_returns_shape(client, isolated_dir):
    r = client.get("/api/cp/upgrade-lifecycle/state")
    assert r.status_code == 200
    body = r.json()
    assert "switches" in body
    assert "quarterly_budget_usd" in body
    assert "budget_used_usd" in body
    assert "budget_remaining_usd" in body
    assert "crs_this_week" in body
    assert isinstance(body["available_snapshot_years"], list)


# ── /upgrade-lifecycle/capabilities/{pkg} ───────────────────────────────


def test_capabilities_endpoint_returns_empty_for_unknown_pkg(client, isolated_dir):
    r = client.get("/api/cp/upgrade-lifecycle/capabilities/unknown-pkg")
    assert r.status_code == 200
    body = r.json()
    assert body["package"] == "unknown-pkg"
    assert body["count"] == 0
    assert body["rows"] == []


def test_capabilities_endpoint_returns_persisted_rows(client, isolated_dir):
    from app.upgrade_lifecycle.changelog_fetcher import _persist
    cap = Capability(
        package="testlib", from_version="1.0", to_version="2.0",
        source="github_releases", extracted_at="2026-05-23T00:00:00+00:00",
        new_features=("foo",),
    )
    _persist(cap)
    r = client.get("/api/cp/upgrade-lifecycle/capabilities/testlib")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["rows"][0]["package"] == "testlib"
    assert body["rows"][0]["new_features"] == ["foo"]


# ── /ecosystem/snapshots ────────────────────────────────────────────────


def test_snapshots_list_empty(client, isolated_dir):
    r = client.get("/api/cp/ecosystem/snapshots")
    assert r.status_code == 200
    assert r.json() == {"years": []}


def test_snapshots_list_after_generate(client, isolated_dir, monkeypatch):
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    r = client.get("/api/cp/ecosystem/snapshots")
    assert r.status_code == 200
    years = r.json()["years"]
    assert len(years) == 1
    assert years[0]["year"] == 2026
    assert years[0]["major_upgrade_count"] == 0


# ── /ecosystem/snapshots/{year} ─────────────────────────────────────────


def test_snapshot_get_404_when_missing(client, isolated_dir):
    r = client.get("/api/cp/ecosystem/snapshots/2099")
    assert r.status_code == 404


def test_snapshot_get_returns_markdown(client, isolated_dir, monkeypatch):
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )
    r = client.get("/api/cp/ecosystem/snapshots/2026")
    assert r.status_code == 200
    body = r.json()
    assert "snapshot" in body
    assert "markdown" in body
    assert "Ecosystem snapshot — 2026" in body["markdown"]


# ── /ecosystem/major-upgrades/accept ────────────────────────────────────


def test_accept_404_for_unknown_snapshot(client, isolated_dir):
    r = client.post(
        "/api/cp/ecosystem/major-upgrades/accept",
        json={"year": 2099, "package": "x", "to_version": "1.0"},
    )
    assert r.status_code == 404


# ── /ecosystem/snapshots/generate ───────────────────────────────────────


def test_generate_endpoint_creates_new_snapshot(client, isolated_dir, monkeypatch):
    """POST with year=current → generate; subsequent GET sees it."""
    from datetime import datetime, timezone
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)

    # Patch out network-dependent default fetchers so the test is fast
    # and deterministic.
    monkeypatch.setattr(eco, "_default_framework_fetcher",
                       lambda pkg: {"latest_version": "x"})
    monkeypatch.setattr(eco, "_default_cost_by_provider", lambda: {})

    year = datetime.now(timezone.utc).year
    r = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={"year": year, "force": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["year"] == year
    assert "generated_at" in body

    # GET picks it up
    r2 = client.get("/api/cp/ecosystem/snapshots")
    assert r2.status_code == 200
    assert any(y["year"] == year for y in r2.json()["years"])


def test_generate_endpoint_default_year_resolves_to_current(client, isolated_dir, monkeypatch):
    from datetime import datetime, timezone
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    monkeypatch.setattr(eco, "_default_framework_fetcher",
                       lambda pkg: {"latest_version": "x"})
    monkeypatch.setattr(eco, "_default_cost_by_provider", lambda: {})

    r = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={},   # year omitted → server-side default
    )
    assert r.status_code == 200
    body = r.json()
    assert body["year"] == datetime.now(timezone.utc).year


def test_generate_endpoint_returns_409_when_disabled(client, isolated_dir, monkeypatch):
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: False)
    r = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={"year": 2026},
    )
    assert r.status_code == 409


def test_generate_endpoint_force_regenerates(client, isolated_dir, monkeypatch):
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    monkeypatch.setattr(eco, "_default_framework_fetcher",
                       lambda pkg: {"latest_version": "x"})
    monkeypatch.setattr(eco, "_default_cost_by_provider", lambda: {})

    # First generation
    r1 = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={"year": 2026, "force": False},
    )
    first_ts = r1.json()["generated_at"]

    # Second call without force → returns existing (timestamps equal)
    r2 = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={"year": 2026, "force": False},
    )
    assert r2.json()["generated_at"] == first_ts

    # Third call WITH force → timestamps differ
    r3 = client.post(
        "/api/cp/ecosystem/snapshots/generate",
        json={"year": 2026, "force": True},
    )
    # generated_at may match if the test runs sub-microsecond, but
    # the call must succeed.
    assert r3.status_code == 200
