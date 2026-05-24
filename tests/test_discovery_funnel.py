"""Tests for app.observability.discovery_funnel — Gap #6 observation →
adoption telemetry."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.observability import discovery_funnel as df  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(df, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(df, "_enabled", lambda: True)
    return tmp_path


def _write_proposal(workspace: Path, source: str, *, ts_iso: str, sig: str) -> None:
    folder = workspace / "proposal_bridge" / source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sig}.json").write_text(
        json.dumps({"created_at": ts_iso, "signature": sig})
    )


def _write_cr(
    workspace: Path,
    cr_id: str,
    *,
    requestor: str,
    status: str,
    ts_iso: str,
) -> None:
    folder = workspace / "change_requests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{cr_id}.json").write_text(
        json.dumps({
            "id": cr_id,
            "requestor": requestor,
            "status": status,
            "created_at": ts_iso,
        })
    )


def test_empty_workspace_returns_zero_counts(_tmp_workspace: Path) -> None:
    out = df.compute(window_days=90)
    assert out["sources"] == []
    assert out["totals"]["staged"] == 0
    assert out["totals"]["cr_filed"] == 0
    assert out["stagnant_sources"] == []


def test_counts_proposals_within_window(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    _write_proposal(_tmp_workspace, "library_radar", ts_iso=(now - timedelta(days=10)).isoformat(), sig="a")
    _write_proposal(_tmp_workspace, "library_radar", ts_iso=(now - timedelta(days=20)).isoformat(), sig="b")
    _write_proposal(_tmp_workspace, "capability_gap", ts_iso=(now - timedelta(days=5)).isoformat(), sig="c")

    out = df.compute(window_days=90, now=now.timestamp())
    rows_by_source = {r["source"]: r for r in out["sources"]}
    assert rows_by_source["library_radar"]["staged"] == 2
    assert rows_by_source["capability_gap"]["staged"] == 1


def test_old_proposals_outside_window_excluded(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    _write_proposal(_tmp_workspace, "library_radar", ts_iso=(now - timedelta(days=200)).isoformat(), sig="ancient")
    _write_proposal(_tmp_workspace, "library_radar", ts_iso=(now - timedelta(days=10)).isoformat(), sig="recent")

    out = df.compute(window_days=90, now=now.timestamp())
    rows = {r["source"]: r["staged"] for r in out["sources"]}
    assert rows["library_radar"] == 1


def test_cr_counts_by_status(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=10)).isoformat()
    _write_cr(_tmp_workspace, "a", requestor="library_radar", status="applied", ts_iso=base_iso)
    _write_cr(_tmp_workspace, "b", requestor="library_radar", status="rejected", ts_iso=base_iso)
    _write_cr(_tmp_workspace, "c", requestor="library_radar", status="rolled_back", ts_iso=base_iso)
    _write_cr(_tmp_workspace, "d", requestor="library_radar", status="pending", ts_iso=base_iso)

    out = df.compute(window_days=90, now=now.timestamp())
    row = next(r for r in out["sources"] if r["source"] == "library_radar")
    assert row["cr_filed"] == 4
    assert row["cr_applied"] == 1
    assert row["cr_rejected"] == 1
    assert row["cr_rolled_back"] == 1
    assert row["cr_pending"] == 1


def test_stagnant_detection_fires_at_threshold(_tmp_workspace: Path) -> None:
    """5+ stagings with 0 applied is stagnant. 4 stagings is not yet."""
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=30)).isoformat()
    for i in range(6):
        _write_proposal(_tmp_workspace, "paper_pipeline", ts_iso=base_iso, sig=f"p{i}")

    out = df.compute(window_days=90, now=now.timestamp())
    assert "paper_pipeline" in out["stagnant_sources"]


def test_stagnant_not_triggered_when_some_applied(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=30)).isoformat()
    for i in range(6):
        _write_proposal(_tmp_workspace, "paper_pipeline", ts_iso=base_iso, sig=f"p{i}")
    _write_cr(_tmp_workspace, "applied-cr", requestor="paper_pipeline", status="applied", ts_iso=base_iso)

    out = df.compute(window_days=90, now=now.timestamp())
    assert "paper_pipeline" not in out["stagnant_sources"]


def test_library_radar_trial_state_jsonl_is_merged(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    trial_path = _tmp_workspace / "library_radar" / "trial_state.jsonl"
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_text("\n".join([
        json.dumps({"ts": (now - timedelta(days=5)).isoformat(), "slug": "x"}),
        json.dumps({"ts": (now - timedelta(days=10)).isoformat(), "slug": "y"}),
    ]) + "\n")
    out = df.compute(window_days=90, now=now.timestamp())
    row = next(r for r in out["sources"] if r["source"] == "library_radar")
    assert row["staged"] == 2


def test_unknown_requestor_grouped_separately(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=10)).isoformat()
    _write_cr(_tmp_workspace, "a", requestor="error_diagnosis", status="applied", ts_iso=base_iso)
    _write_cr(_tmp_workspace, "b", requestor="dependency_radar", status="applied", ts_iso=base_iso)

    out = df.compute(window_days=90, now=now.timestamp())
    sources = {r["source"] for r in out["sources"]}
    assert "error_diagnosis" in sources
    assert "dependency_radar" in sources


def test_ratios_computed_correctly(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=10)).isoformat()
    for i in range(10):
        _write_proposal(_tmp_workspace, "src", ts_iso=base_iso, sig=f"p{i}")
    for i in range(4):
        _write_cr(_tmp_workspace, f"cr{i}", requestor="src", status="applied", ts_iso=base_iso)
    for i in range(4, 6):
        _write_cr(_tmp_workspace, f"cr{i}", requestor="src", status="rejected", ts_iso=base_iso)

    out = df.compute(window_days=90, now=now.timestamp())
    row = next(r for r in out["sources"] if r["source"] == "src")
    assert row["staged"] == 10
    assert row["cr_filed"] == 6
    assert row["cr_applied"] == 4
    assert row["filed_ratio"] == pytest.approx(0.6)
    assert row["applied_ratio"] == pytest.approx(4 / 6)


def test_briefing_section_renders(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    base_iso = (now - timedelta(days=10)).isoformat()
    _write_proposal(_tmp_workspace, "library_radar", ts_iso=base_iso, sig="a")
    _write_cr(_tmp_workspace, "x", requestor="library_radar", status="applied", ts_iso=base_iso)

    section = df.briefing_section(window_days=90)
    assert "Discovery → adoption" in section
    assert "library_radar" in section or "applied" in section


def test_briefing_section_empty_when_no_data(_tmp_workspace: Path) -> None:
    assert df.briefing_section() == ""


def test_run_once_persists_snapshot(_tmp_workspace: Path) -> None:
    result = df.run_once()
    assert result["ran"] is True
    snapshot_path = _tmp_workspace / "observability" / "discovery_funnel.json"
    assert snapshot_path.exists()


def test_run_once_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(df, "_enabled", lambda: False)
    assert df.run_once() == {"ran": False, "skipped": True}


def test_corrupt_json_does_not_crash(_tmp_workspace: Path) -> None:
    folder = _tmp_workspace / "proposal_bridge" / "src"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "good.json").write_text(json.dumps({
        "created_at": datetime(2026, 5, 24, tzinfo=timezone.utc).isoformat(),
        "signature": "g",
    }))
    (folder / "bad.json").write_text("{ not json")

    out = df.compute(window_days=90)
    # The good row still counts.
    assert any(r["source"] == "src" and r["staged"] >= 1 for r in out["sources"])
