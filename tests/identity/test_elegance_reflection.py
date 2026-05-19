"""Tests for app.identity.elegance_reflection.

Verifies the deterministic composer over fixture workspaces — no LLM,
no live continuity ledger, no real codebase.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.identity import elegance_reflection as er


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(er, "_workspace_root", lambda: tmp_path)
    return tmp_path


def _seed_elegance_history(root: Path, samples_per_file: dict[str, list[dict]]) -> None:
    p = root / "code_quality" / "elegance_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(samples_per_file))


def _seed_architectural_baseline(
    root: Path, *,
    cycles: list[list[str]] | None = None,
    owners: dict[str, list[str]] | None = None,
    reverse: dict[str, int] | None = None,
) -> None:
    p = root / "code_quality" / "architectural_baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "cycles": cycles or [],
        "capability_owners": owners or {},
        "reverse_degree": reverse or {},
    }))


def _seed_inventory(root: Path, *, n_modules: int = 100, total_loc: int = 10000) -> None:
    p = root / "system_inventory" / "snapshot.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "app_root": "app",
        "n_modules": n_modules,
        "n_packages": 10,
        "total_loc": total_loc,
        "modules": [],
    }))


# ── Composite trajectory ────────────────────────────────────────────────


def test_composite_trajectory_filters_to_year(isolated_workspace):
    _seed_elegance_history(isolated_workspace, {
        "app/a.py": [
            {"ts": "2026-01-15T00:00:00", "composite": 0.90},
            {"ts": "2026-06-30T00:00:00", "composite": 0.85},
            {"ts": "2025-12-31T00:00:00", "composite": 0.50},  # filtered
        ],
        "app/b.py": [{"ts": "2026-03-01T00:00:00", "composite": 0.95}],
    })
    out = er._composite_trajectory(2026)
    assert out["n_samples"] == 3
    assert out["min"] == 0.85
    assert out["max"] == 0.95


def test_composite_trajectory_empty_when_no_history(isolated_workspace):
    out = er._composite_trajectory(2026)
    assert out == {"n_samples": 0}


# ── Architectural shape ────────────────────────────────────────────────


def test_architectural_shape_splits_actionable_vs_systemic(isolated_workspace):
    big = [f"app/big{i}.py" for i in range(30)]
    small = ["app/a.py", "app/b.py"]
    _seed_architectural_baseline(
        isolated_workspace,
        cycles=[small, big],
        owners={"cap-a": ["app/x.py", "app/y.py", "app/z.py"]},
        reverse={"app/config.py": 50},
    )
    out = er._architectural_shape()
    assert out["baseline_present"] is True
    assert out["n_actionable_cycles"] == 1
    assert out["n_systemic_sccs"] == 1
    assert out["largest_systemic_size"] == 30
    assert out["n_parallel_capabilities"] == 1
    assert out["top_centrality"][0] == ("app/config.py", 50)


def test_architectural_shape_handles_missing_baseline(isolated_workspace):
    assert er._architectural_shape() == {"baseline_present": False}


# ── Refactor proposals ─────────────────────────────────────────────────


def test_refactor_proposals_count_by_status_within_year(isolated_workspace):
    bridge_dir = isolated_workspace / "proposal_bridge" / "refactor_proposer"
    bridge_dir.mkdir(parents=True)
    (bridge_dir / "a.json").write_text(json.dumps({"staged_at": "2026-02-01", "status": "staged"}))
    (bridge_dir / "b.json").write_text(json.dumps({"staged_at": "2026-03-01", "status": "applied"}))
    (bridge_dir / "c.json").write_text(json.dumps({"staged_at": "2025-12-01", "status": "applied"}))
    out = er._refactor_proposals_year(2026)
    assert out["total"] == 2
    assert out["staged"] == 1
    assert out["applied"] == 1


# ── Verdict ────────────────────────────────────────────────────────────


def test_verdict_shedding_when_all_signals_align():
    composite = {"avg": 0.92}
    shape = {"n_actionable_cycles": 3}
    proposals = {"applied": 5, "rejected": 2}
    drift = {"total": 10}
    assert er._verdict(composite, shape, proposals, drift) == "shedding"


def test_verdict_stable_when_composite_ok_and_drift_seen():
    composite = {"avg": 0.86}
    shape = {"n_actionable_cycles": 8}  # more than shedding allows
    proposals = {"applied": 0, "rejected": 0}
    drift = {"total": 5}
    assert er._verdict(composite, shape, proposals, drift) == "stable"


def test_verdict_growing_otherwise():
    composite = {"avg": 0.70}
    shape = {"n_actionable_cycles": 20}
    proposals = {"applied": 0, "rejected": 0}
    drift = {"total": 0}
    assert er._verdict(composite, shape, proposals, drift) == "growing"


# ── End-to-end ─────────────────────────────────────────────────────────


def test_run_one_pass_writes_file(isolated_workspace, tmp_path, monkeypatch):
    _seed_elegance_history(isolated_workspace, {
        "app/a.py": [{"ts": "2026-05-01", "composite": 0.91}],
    })
    _seed_architectural_baseline(
        isolated_workspace,
        cycles=[["app/x.py", "app/y.py"]],
        owners={"reg": ["app/o1.py", "app/o2.py", "app/o3.py"]},
        reverse={"app/config.py": 100},
    )
    _seed_inventory(isolated_workspace, n_modules=1000, total_loc=200_000)

    out_dir = tmp_path / "reflections"
    # Make the landmark emission a no-op so we don't write to live ledger.
    monkeypatch.setattr(er, "_emit_landmark", lambda *a, **k: None)

    result = er.run_one_pass(
        year=2026, reflections_dir=out_dir,
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert result.status == "wrote"
    assert result.year == 2026
    target = out_dir / "2026.md"
    assert target.exists()
    body = target.read_text()
    assert "Code elegance reflection — 2026" in body
    assert "verdict:" in body
    assert "shedding" in body or "stable" in body or "growing" in body


def test_skipped_disabled(isolated_workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(er, "_enabled", lambda: False)
    result = er.run_one_pass(reflections_dir=tmp_path)
    assert result.status == "skipped_disabled"


def test_skipped_recent(isolated_workspace, monkeypatch, tmp_path):
    out_dir = tmp_path / "reflections"
    out_dir.mkdir()
    (out_dir / "2026.md").write_text("# old reflection\n")
    monkeypatch.setattr(er, "_emit_landmark", lambda *a, **k: None)

    result = er.run_one_pass(
        year=2026, reflections_dir=out_dir, min_interval_days=350,
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert result.status == "skipped_recent"


def test_due_when_file_is_stale(tmp_path):
    """If the target file is older than min_interval_days, _is_due is True."""
    out_dir = tmp_path / "reflections"
    out_dir.mkdir()
    target = out_dir / "2026.md"
    target.write_text("# old\n")
    # Backdate.
    import os
    old = target.stat().st_mtime - 400 * 86400
    os.utime(target, (old, old))
    assert er._is_due(out_dir, 2026, min_interval_days=350) is True
