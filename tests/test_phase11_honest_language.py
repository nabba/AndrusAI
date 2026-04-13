"""Phase 11 — honest-language regression tests.

Asserts:
  1. Neutral aliases exist for phenomenal-adjacent legacy keys
     (`task_failure_pressure`, `exploration_bonus`, `resource_budget`)
     and stay in sync with the legacy keys (`frustration`, `curiosity`,
     `cognitive_energy`).
  2. README documenting the ABSENT-by-declaration indicators is present
     and lists each indicator the architecture cannot mechanize.
  3. The Phase 11 docstring disclaimer is in place on the homeostasis
     state module so future readers cannot mistake the floats for
     phenomenal experience.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_neutral_aliases_defined():
    from app.subia.homeostasis import state as homeo_state

    assert homeo_state.NEUTRAL_ALIASES == {
        "frustration": "task_failure_pressure",
        "curiosity": "exploration_bonus",
        "cognitive_energy": "resource_budget",
    }


def test_sync_aliases_legacy_to_neutral():
    from app.subia.homeostasis.state import _sync_aliases

    s = {"frustration": 0.42, "curiosity": 0.3, "cognitive_energy": 0.6}
    out = _sync_aliases(s)
    assert out["task_failure_pressure"] == 0.42
    assert out["exploration_bonus"] == 0.3
    assert out["resource_budget"] == 0.6


def test_sync_aliases_neutral_to_legacy():
    from app.subia.homeostasis.state import _sync_aliases

    s = {"task_failure_pressure": 0.55, "exploration_bonus": 0.2, "resource_budget": 0.8}
    out = _sync_aliases(s)
    assert out["frustration"] == 0.55
    assert out["curiosity"] == 0.2
    assert out["cognitive_energy"] == 0.8


def test_sync_aliases_legacy_wins_when_both_present():
    from app.subia.homeostasis.state import _sync_aliases

    s = {"frustration": 0.9, "task_failure_pressure": 0.1}
    out = _sync_aliases(s)
    # Legacy wins for back-compat
    assert out["frustration"] == 0.9
    assert out["task_failure_pressure"] == 0.9


def test_load_returns_aliased_state(tmp_path, monkeypatch):
    import json
    from app.subia.homeostasis import state as homeo_state

    p = tmp_path / "homeo.json"
    p.write_text(json.dumps({
        "frustration": 0.33,
        "curiosity": 0.44,
        "cognitive_energy": 0.55,
        "confidence": 0.5,
    }))
    monkeypatch.setattr(homeo_state, "_STATE_PATH", p)
    s = homeo_state._load()
    assert s["task_failure_pressure"] == 0.33
    assert s["exploration_bonus"] == 0.44
    assert s["resource_budget"] == 0.55


def test_subia_readme_exists_and_lists_absent_indicators():
    readme = REPO_ROOT / "app" / "subia" / "README.md"
    assert readme.exists(), "Phase 11 README missing"
    text = readme.read_text()
    for indicator in ("RPT-1", "HOT-1", "HOT-4", "AE-2", "Metzinger"):
        assert indicator in text, f"README must list {indicator} as ABSENT"
    # Must explicitly disclaim phenomenal experience
    assert "does not claim" in text.lower() or "no single number" in text.lower()


def test_homeostasis_module_has_disclaimer():
    from app.subia.homeostasis import state as homeo_state

    doc = homeo_state.__doc__ or ""
    assert "NOT" in doc and "subjective feelings" in doc.lower(), (
        "homeostasis state module must disclaim phenomenal experience"
    )


def test_scorecard_is_canonical_evaluation():
    """The retired 9.5/10 verdict must remain only as historical artefact;
    the canonical evaluation is the auto-generated SCORECARD.md."""
    scorecard = REPO_ROOT / "app" / "subia" / "probes" / "SCORECARD.md"
    assert scorecard.exists(), "Phase 9 scorecard must exist"
    # No prose '9.5/10' style verdict in the canonical scorecard
    text = scorecard.read_text()
    # The phrase may appear ONLY as a reference to the retired verdict.
    for line in text.splitlines():
        if "9.5/10" in line or "9.5 / 10" in line:
            assert "retired" in line.lower(), (
                f"9.5/10 must only appear in 'retired' context, got: {line}"
            )
