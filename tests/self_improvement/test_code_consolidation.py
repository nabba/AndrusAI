"""Tests for app.self_improvement.code_consolidation.

Verifies the deterministic quarterly digest over fixture inputs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.self_improvement import code_consolidation as cc


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(cc, "_workspace_root", lambda: tmp_path)
    return tmp_path


def _seed_inventory(root: Path, modules: list[dict]) -> None:
    p = root / "system_inventory" / "snapshot.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "generated_at": "2026-01-01T00:00:00+00:00",
        "app_root": "app",
        "n_modules": len(modules),
        "n_packages": 0,
        "total_loc": sum(int(m.get("loc", 0)) for m in modules),
        "modules": modules,
    }))


def _seed_baseline(
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


# ── _quarter_for ──────────────────────────────────────────────────────


def test_quarter_for_jan_is_q1():
    assert cc._quarter_for(datetime(2026, 1, 15, tzinfo=timezone.utc)) == (2026, 1)


def test_quarter_for_dec_is_q4():
    assert cc._quarter_for(datetime(2026, 12, 1, tzinfo=timezone.utc)) == (2026, 4)


# ── shed-candidate detector ───────────────────────────────────────────


def test_shed_candidate_requires_all_signals(isolated_workspace):
    modules = [
        {  # Real shed candidate: small, untested, no importers, non-hub
            "path": "app/abandoned.py", "kind": "module", "loc": 30,
            "public_symbols": ["old_helper"], "summary": "abandoned helper",
            "has_tests": False,
        },
        {  # Has tests — excluded (softer signal)
            "path": "app/tested.py", "kind": "module", "loc": 30,
            "public_symbols": [], "summary": "", "has_tests": True,
        },
        {  # Too big — excluded
            "path": "app/big.py", "kind": "module", "loc": 500,
            "public_symbols": [], "summary": "", "has_tests": False,
        },
        {  # Many importers — excluded (foundational)
            "path": "app/popular.py", "kind": "module", "loc": 30,
            "public_symbols": [], "summary": "", "has_tests": False,
        },
        {  # Hub-pattern exclusion: config.py
            "path": "app/config.py", "kind": "module", "loc": 30,
            "public_symbols": [], "summary": "", "has_tests": False,
        },
        {  # __init__.py — kind=package — excluded
            "path": "app/pkg/__init__.py", "kind": "package", "loc": 5,
            "public_symbols": [], "summary": "", "has_tests": False,
        },
    ]
    _seed_inventory(isolated_workspace, modules)
    _seed_baseline(isolated_workspace, reverse={"app/popular.py": 10})

    out = cc._detect_shed_candidates(
        cc._read_json(isolated_workspace / "system_inventory" / "snapshot.json"),
        cc._read_json(isolated_workspace / "code_quality" / "architectural_baseline.json"),
    )
    paths = [c["path"] for c in out]
    assert paths == ["app/abandoned.py"]


def test_shed_candidates_capped(isolated_workspace):
    modules = [
        {
            "path": f"app/shed_{i}.py", "kind": "module", "loc": 10 + i,
            "public_symbols": [], "summary": "", "has_tests": False,
        }
        for i in range(50)
    ]
    _seed_inventory(isolated_workspace, modules)
    _seed_baseline(isolated_workspace)
    out = cc._detect_shed_candidates(
        cc._read_json(isolated_workspace / "system_inventory" / "snapshot.json"),
        cc._read_json(isolated_workspace / "code_quality" / "architectural_baseline.json"),
    )
    assert len(out) == cc._MAX_SHED_CANDIDATES
    # Sorted by LOC ascending — smallest first.
    assert out[0]["loc"] == 10
    assert out[1]["loc"] == 11


def test_no_candidates_without_inventory(isolated_workspace):
    assert cc._detect_shed_candidates(None, None) == []


# ── parallel-cluster detector ─────────────────────────────────────────


def test_parallel_clusters_requires_three_owners(isolated_workspace):
    _seed_baseline(isolated_workspace, owners={
        "two-owners": ["a.py", "b.py"],
        "three-owners": ["a.py", "b.py", "c.py"],
        "five-owners": ["a.py", "b.py", "c.py", "d.py", "e.py"],
    })
    out = cc._detect_parallel_clusters(
        cc._read_json(isolated_workspace / "code_quality" / "architectural_baseline.json"),
    )
    caps = [p["capability"] for p in out]
    assert "two-owners" not in caps
    assert "three-owners" in caps
    assert "five-owners" in caps
    # Sorted: most-owners first.
    assert caps[0] == "five-owners"


# ── stable-cycles detector ────────────────────────────────────────────


def test_stable_cycles_excludes_systemic(isolated_workspace):
    big = [f"app/big{i}.py" for i in range(30)]
    small = ["app/a.py", "app/b.py"]
    _seed_baseline(isolated_workspace, cycles=[big, small])
    out = cc._detect_stable_cycles(
        cc._read_json(isolated_workspace / "code_quality" / "architectural_baseline.json"),
    )
    assert out == [small]


# ── end-to-end ─────────────────────────────────────────────────────────


def test_run_one_pass_writes_digest(isolated_workspace, tmp_path, monkeypatch):
    _seed_inventory(isolated_workspace, [
        {"path": "app/abandoned.py", "kind": "module", "loc": 30,
         "public_symbols": [], "summary": "", "has_tests": False},
    ])
    _seed_baseline(
        isolated_workspace,
        cycles=[["app/a.py", "app/b.py"]],
        owners={"cap-x": ["1.py", "2.py", "3.py"]},
    )
    monkeypatch.setattr(cc, "_emit_landmark", lambda *a, **k: None)

    out_dir = tmp_path / "digests"
    result = cc.run_one_pass(
        digests_dir=out_dir, now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert result.status == "wrote"
    assert result.year == 2026
    assert result.quarter == 2
    assert result.n_shed_candidates == 1
    assert result.n_parallel_clusters == 1
    assert result.n_cycles == 1

    target = out_dir / "2026_q2.md"
    body = target.read_text()
    assert "Code-consolidation digest — 2026 Q2" in body
    assert "app/abandoned.py" in body


def test_skipped_disabled(isolated_workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "_enabled", lambda: False)
    result = cc.run_one_pass(digests_dir=tmp_path)
    assert result.status == "skipped_disabled"


def test_skipped_recent(isolated_workspace, monkeypatch, tmp_path):
    out_dir = tmp_path / "digests"
    out_dir.mkdir()
    (out_dir / "2026_q2.md").write_text("# old\n")
    monkeypatch.setattr(cc, "_emit_landmark", lambda *a, **k: None)
    result = cc.run_one_pass(
        digests_dir=out_dir, min_interval_days=85,
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert result.status == "skipped_recent"


def test_due_when_file_is_old(tmp_path):
    out_dir = tmp_path / "digests"
    out_dir.mkdir()
    target = out_dir / "2026_q1.md"
    target.write_text("# old\n")
    old = target.stat().st_mtime - 100 * 86400
    os.utime(target, (old, old))
    assert cc._is_due(out_dir, 2026, 1, min_interval_days=85) is True
