"""Tests for app.refactoring.proposer — Phase 2 of the elegance plan.

The proposer reads Phase 1 artefacts (elegance_history,
architectural_baseline) and stages refactor candidates through
proposal_bridge. We test each detector in isolation against fixtures
and verify run_one_pass composes them correctly.
"""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch):
    """Point the proposer at a clean tmp workspace + bridge directory."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PROPOSAL_BRIDGE_DIR", str(tmp_path / "proposal_bridge"))
    from app.refactoring import proposer as prop_mod
    monkeypatch.setattr(prop_mod, "_workspace_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def enabled(monkeypatch):
    """Force the proposer's _enabled() to True for the duration of a test."""
    from app.refactoring import proposer as prop_mod
    monkeypatch.setattr(prop_mod, "_enabled", lambda: True)


def _seed_elegance_history(root: Path, entries: dict[str, list[dict]]) -> None:
    p = root / "code_quality" / "elegance_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries))


def _seed_architectural_baseline(
    root: Path,
    *,
    cycles: list[list[str]] | None = None,
    capability_owners: dict[str, list[str]] | None = None,
    reverse_degree: dict[str, int] | None = None,
) -> None:
    p = root / "code_quality" / "architectural_baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "cycles": cycles or [],
        "capability_owners": capability_owners or {},
        "reverse_degree": reverse_degree or {},
    }))


# ── detect_complexity_hotspots ──────────────────────────────────────────


def test_complexity_hotspot_requires_low_composite_and_low_complexity(
    isolated_workspace, monkeypatch, tmp_path
):
    """A file with low composite from missing docstrings (but fine
    complexity) MUST NOT surface as a complexity hotspot."""
    # Write a real file we'll re-score live.
    target_rel = "app/example_module.py"
    target_abs = tmp_path / target_rel
    target_abs.parent.mkdir(parents=True, exist_ok=True)
    # Tidy + simple: composite ~0.9, complexity_score ~1.0 → not a hotspot.
    target_abs.write_text(
        '"""Tidy module."""\n\n'
        'def add(a: int, b: int) -> int:\n'
        '    """Add two ints."""\n'
        '    return a + b\n'
    )
    _seed_elegance_history(isolated_workspace, {target_rel: [{"ts": "x", "composite": 0.95}]})
    monkeypatch.chdir(tmp_path)

    from app.refactoring.proposer import detect_complexity_hotspots
    assert detect_complexity_hotspots() == []


def _high_complexity_source() -> str:
    """A single function with McCabe ~25 — well below the proposer's
    complexity_score threshold (0.40) and the composite threshold (0.65).

    code_quality's complexity_score normalises against COMPLEXITY_TARGET
    (McCabe 10) → COMPLEXITY_HARD_LIMIT (McCabe 25). 22 sequential
    branches puts this comfortably in the bottom band.
    """
    cases = "\n".join(f"    if x == {i}: return {i}" for i in range(22))
    return f"def f(x):\n{cases}\n    return -1\n"


def test_complexity_hotspot_surfaces_branchy_file(isolated_workspace, monkeypatch, tmp_path):
    target_rel = "app/branchy.py"
    target_abs = tmp_path / target_rel
    target_abs.parent.mkdir(parents=True, exist_ok=True)
    target_abs.write_text(_high_complexity_source())
    _seed_elegance_history(isolated_workspace, {target_rel: [{"ts": "x", "composite": 0.30}]})
    monkeypatch.chdir(tmp_path)

    from app.refactoring.proposer import detect_complexity_hotspots
    out = detect_complexity_hotspots()
    assert len(out) == 1
    assert out[0].detector == "complexity_hotspot"
    assert target_rel in out[0].body_markdown
    spec = out[0].coding_session_spec
    assert spec["files"] == [target_rel]
    assert "complexity" in spec["intent"].lower()


def test_complexity_hotspot_signature_is_stable(isolated_workspace, monkeypatch, tmp_path):
    """Re-running over the same file content yields the same signature
    so the bridge can deduplicate."""
    target_rel = "app/x.py"
    target_abs = tmp_path / target_rel
    target_abs.parent.mkdir(parents=True, exist_ok=True)
    target_abs.write_text(_high_complexity_source())
    _seed_elegance_history(isolated_workspace, {target_rel: [{"ts": "x", "composite": 0.40}]})
    monkeypatch.chdir(tmp_path)

    from app.refactoring.proposer import detect_complexity_hotspots
    first = detect_complexity_hotspots()
    second = detect_complexity_hotspots()
    assert first[0].signature == second[0].signature


def test_complexity_hotspot_capped_at_three(isolated_workspace, monkeypatch, tmp_path):
    """No matter how many files qualify, only 3 candidates emerge."""
    history: dict = {}
    for i in range(7):
        rel = f"app/h{i}.py"
        abs_p = tmp_path / rel
        abs_p.parent.mkdir(parents=True, exist_ok=True)
        abs_p.write_text(_high_complexity_source())
        history[rel] = [{"ts": "x", "composite": 0.30}]
    _seed_elegance_history(isolated_workspace, history)
    monkeypatch.chdir(tmp_path)

    from app.refactoring.proposer import detect_complexity_hotspots
    out = detect_complexity_hotspots()
    assert len(out) <= 3
    assert len(out) >= 1  # at least some surfaced; cap kicked in correctly


# ── detect_import_cycles ────────────────────────────────────────────────


def test_import_cycle_picks_small_cycles_only(isolated_workspace):
    small = ["app/a.py", "app/b.py"]
    medium = ["app/m1.py", "app/m2.py", "app/m3.py"]
    systemic = [f"app/big{i}.py" for i in range(30)]  # > _MAX_ALERTABLE_CYCLE_SIZE
    _seed_architectural_baseline(isolated_workspace, cycles=[small, medium, systemic])

    from app.refactoring.proposer import detect_import_cycles
    out = detect_import_cycles()
    sigs = [c.signature for c in out]
    # The systemic SCC must not appear.
    sizes = [int(s.split("__")[1]) for s in sigs if s.startswith("cyc__")]
    assert all(sz <= 20 for sz in sizes)
    # Both small + medium present.
    assert any("__2__" in s for s in sigs)
    assert any("__3__" in s for s in sigs)


def test_import_cycle_body_lists_members(isolated_workspace):
    cycle = ["app/foo.py", "app/bar.py"]
    _seed_architectural_baseline(isolated_workspace, cycles=[cycle])

    from app.refactoring.proposer import detect_import_cycles
    out = detect_import_cycles()
    assert len(out) == 1
    body = out[0].body_markdown
    assert "app/foo.py" in body
    assert "app/bar.py" in body
    assert "dependency inversion" in body.lower()


def test_import_cycle_empty_when_no_baseline(isolated_workspace):
    from app.refactoring.proposer import detect_import_cycles
    assert detect_import_cycles() == []


# ── detect_parallel_capabilities ────────────────────────────────────────


def test_parallel_capability_requires_three_or_more_owners(isolated_workspace):
    _seed_architectural_baseline(isolated_workspace, capability_owners={
        "two-owners": ["app/a.py", "app/b.py"],     # filtered
        "three-owners": ["app/c.py", "app/d.py", "app/e.py"],
        "four-owners": ["app/f.py", "app/g.py", "app/h.py", "app/i.py"],
    })

    from app.refactoring.proposer import detect_parallel_capabilities
    out = detect_parallel_capabilities()
    titles = " ".join(c.title for c in out)
    assert "two-owners" not in titles
    assert "three-owners" in titles
    assert "four-owners" in titles


def test_parallel_capability_signature_includes_owner_set(isolated_workspace):
    """A different owner set yields a different signature so adding a
    new owner re-stages."""
    _seed_architectural_baseline(isolated_workspace, capability_owners={
        "cap-x": ["app/a.py", "app/b.py", "app/c.py"],
    })
    from app.refactoring.proposer import detect_parallel_capabilities
    first = detect_parallel_capabilities()
    first_sig = first[0].signature

    _seed_architectural_baseline(isolated_workspace, capability_owners={
        "cap-x": ["app/a.py", "app/b.py", "app/c.py", "app/d.py"],
    })
    second = detect_parallel_capabilities()
    second_sig = second[0].signature
    assert first_sig != second_sig


# ── run_one_pass ────────────────────────────────────────────────────────


def test_run_one_pass_disabled_short_circuits(isolated_workspace, monkeypatch):
    from app.refactoring import proposer as prop_mod
    monkeypatch.setattr(prop_mod, "_enabled", lambda: False)
    result = prop_mod.run_one_pass()
    assert result["disabled"] is True
    assert result["checked"] is False
    assert result["staged"] == 0


def test_run_one_pass_stages_via_bridge(isolated_workspace, enabled, tmp_path):
    cycle = ["app/x.py", "app/y.py"]
    _seed_architectural_baseline(isolated_workspace, cycles=[cycle])

    from app.refactoring import proposer as prop_mod
    result = prop_mod.run_one_pass()

    assert result["checked"] is True
    assert result["staged"] >= 1
    assert result["by_detector"]["import_cycle"]["staged"] >= 1

    # The bridge wrote a body file under PROPOSAL_BRIDGE_DIR.
    bridge_dir = tmp_path / "proposal_bridge" / "refactor_proposer"
    bodies = list(bridge_dir.glob("*.md"))
    assert bodies, "expected at least one staged proposal body"


def test_run_one_pass_idempotent_on_identical_signal(
    isolated_workspace, enabled, tmp_path
):
    """Two passes over identical baseline state stage the SAME proposals,
    second pass marks them skipped (already staged)."""
    cycle = ["app/x.py", "app/y.py"]
    _seed_architectural_baseline(isolated_workspace, cycles=[cycle])

    from app.refactoring import proposer as prop_mod
    first = prop_mod.run_one_pass()
    second = prop_mod.run_one_pass()

    assert first["staged"] >= 1
    assert second["staged"] == 0
    assert second["skipped"] >= 1


def test_target_paths_pass_change_request_validator(isolated_workspace):
    """Every candidate's target_path must satisfy the CR validator.
    proposal_bridge.stage() rejects bad target_paths at stage time;
    we pre-empt that with a unit check so detector authors get fast
    feedback."""
    _seed_architectural_baseline(isolated_workspace, cycles=[["app/a.py", "app/b.py"]])
    from app.change_requests.validator import validate
    from app.refactoring.proposer import (
        detect_import_cycles,
        detect_parallel_capabilities,
    )
    _seed_architectural_baseline(
        isolated_workspace,
        cycles=[["app/a.py", "app/b.py"]],
        capability_owners={"some-cap": ["app/x.py", "app/y.py", "app/z.py"]},
    )
    candidates = detect_import_cycles() + detect_parallel_capabilities()
    assert candidates, "fixture should yield at least one candidate"
    for c in candidates:
        r = validate(path=c.target_path, new_content=c.body_markdown)
        assert r.ok, f"target_path rejected: {c.target_path!r} — {r.reason}"
