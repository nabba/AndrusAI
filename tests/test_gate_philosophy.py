"""Gap 3 — gate_philosophy evaluator tests.

Covers:
  * Master switch off → evaluator returns (None, "")
  * Chat zone → evaluator skips (only autonomous/financial activate)
  * Too-short proposal → evaluator skips
  * Panel returns skipped_reason → no escalation
  * Panel returns < 2 unresolved tensions → no escalation
  * Panel returns ≥ 2 unresolved tensions → peer_review escalation +
    tension + thread filing
  * Goodhart guard: evaluator never returns 'ship' or 'verify' to
    DOWNGRADE another evaluator's decision (escalation-only)
  * Integration with verification_extension chain (escalation lands
    in final verdict)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pydantic")


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    from app import paths as _paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", workspace)
    return workspace


@pytest.fixture
def long_proposal():
    return (
        "Should we proceed with relaxing the SAFETY_FLOOR threshold "
        "in governance.py from 0.85 to 0.80 to allow lower-tier "
        "amendments to land more frequently? This question implicates "
        "the operator's stated preference for slow, audited change."
    )


def _make_verdict(action="ship"):
    """Minimal CalibrationVerdict stub for the evaluator contract."""
    return SimpleNamespace(suggested_action=action)


def test_master_switch_off_returns_none(monkeypatch, long_proposal):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: False)
    action, note = gate_philosophy.evaluate(
        proposal_text=long_proposal,
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action is None
    assert note == ""


def test_chat_zone_skips(monkeypatch, long_proposal):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: True)
    monkeypatch.setattr(gate_philosophy, "_zone_for_task", lambda t: "chat")
    action, note = gate_philosophy.evaluate(
        proposal_text=long_proposal,
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action is None
    assert note == ""


def test_too_short_proposal_skips(monkeypatch):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: True)
    monkeypatch.setattr(gate_philosophy, "_zone_for_task", lambda t: "autonomous")
    action, note = gate_philosophy.evaluate(
        proposal_text="too short",
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action is None
    assert note == ""


def test_panel_skipped_returns_diagnostic_note(monkeypatch, long_proposal):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: True)
    monkeypatch.setattr(gate_philosophy, "_zone_for_task", lambda t: "autonomous")
    fake_panel = SimpleNamespace(
        skipped_reason="kb_empty",
        unresolved_tensions=[],
        perspectives=[],
    )
    monkeypatch.setattr(
        "app.philosophy.dialectics.consult_panel",
        lambda q, **kw: fake_panel,
    )
    action, note = gate_philosophy.evaluate(
        proposal_text=long_proposal,
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action is None
    assert "kb_empty" in note


def test_panel_no_tension_returns_clear_note(monkeypatch, long_proposal):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: True)
    monkeypatch.setattr(gate_philosophy, "_zone_for_task", lambda t: "autonomous")
    fake_panel = SimpleNamespace(
        skipped_reason=None,
        unresolved_tensions=["a single noise tension"],
        perspectives=[
            SimpleNamespace(tradition="Stoicism", claim="some claim"),
        ],
    )
    monkeypatch.setattr(
        "app.philosophy.dialectics.consult_panel",
        lambda q, **kw: fake_panel,
    )
    action, note = gate_philosophy.evaluate(
        proposal_text=long_proposal,
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action is None
    assert "panel_clear" in note


def test_panel_tension_escalates_to_peer_review(monkeypatch, long_proposal):
    from app.epistemic import gate_philosophy

    monkeypatch.setattr(gate_philosophy, "_enabled", lambda: True)
    monkeypatch.setattr(gate_philosophy, "_zone_for_task", lambda t: "autonomous")
    fake_panel = SimpleNamespace(
        skipped_reason=None,
        unresolved_tensions=[
            "tradition A vs tradition B on means/ends",
            "tradition C vs tradition D on consent boundary",
            "tradition E vs tradition F on autonomy",
        ],
        perspectives=[
            SimpleNamespace(tradition="Stoicism", claim="virtue is sufficient"),
            SimpleNamespace(tradition="Utilitarianism", claim="maximize welfare"),
        ],
    )
    monkeypatch.setattr(
        "app.philosophy.dialectics.consult_panel",
        lambda q, **kw: fake_panel,
    )
    # Stub tension + thread filing to avoid touching real stores
    filed = {"tension": None, "thread": None}

    def _fake_file_tension(q, perspectives, unresolved):
        filed["tension"] = "tension-abc123"
        return "tension-abc123"

    def _fake_file_thread(q, tension_id):
        filed["thread"] = "thread-xyz789"
        return "thread-xyz789"

    monkeypatch.setattr(gate_philosophy, "_file_tension", _fake_file_tension)
    monkeypatch.setattr(gate_philosophy, "_file_thread", _fake_file_thread)

    action, note = gate_philosophy.evaluate(
        proposal_text=long_proposal,
        task_id="task-1",
        verdict=_make_verdict(),
    )
    assert action == "peer_review"
    assert filed["tension"] == "tension-abc123"
    assert filed["thread"] == "thread-xyz789"
    assert "3 unresolved" in note
    assert "tension=tension-abc123" in note
    assert "thread=thread-xyz789" in note


def test_evaluator_never_returns_ship_or_verify():
    """Goodhart guard: gate_philosophy is ESCALATION-ONLY.

    The evaluator's contract says it can only return ('peer_review', ...)
    or (None, ...). It must NEVER return 'ship' / 'hedge' / 'verify'
    because that would let it OVERRIDE another evaluator that already
    decided to escalate.
    """
    from app.epistemic import gate_philosophy

    # Grep the source so a future edit can't silently break the
    # escalation-only invariant.
    src = open(gate_philosophy.__file__).read()
    # The only literal action strings should be peer_review / None.
    # Allow the strings inside the docstring + activation reason but
    # forbid them as return values.
    forbidden = ('return "ship"', 'return "hedge"', 'return "verify"')
    for needle in forbidden:
        assert needle not in src, (
            f"gate_philosophy.evaluate must never {needle} — "
            f"escalation-only is load-bearing"
        )


def test_file_tension_creates_real_tension(monkeypatch, isolated_workspace):
    """Smoke: with the live tensions store, the evaluator's file path
    actually produces a tension record."""
    from app.companion import tensions as tensions_mod
    from app.epistemic import gate_philosophy

    # Redirect tensions base dir to the isolated workspace
    monkeypatch.setattr(
        tensions_mod,
        "_default_tensions_dir",
        lambda: isolated_workspace / "companion" / "tensions",
    )
    perspectives = [
        SimpleNamespace(tradition="Stoicism", claim="virtue is sufficient"),
    ]
    unresolved = ["tradition A vs B", "tradition C vs D"]
    tid = gate_philosophy._file_tension(
        question="Should we relax SAFETY_FLOOR?",
        perspectives=perspectives,
        unresolved=unresolved,
    )
    assert tid is not None
    rec = tensions_mod._load_tension(tid)
    assert rec is not None
    assert "Philosophy-flagged" in rec.question
    assert any("Stoicism" in s.snippet for s in rec.sources)


def test_master_switch_default_off():
    from app import runtime_settings

    assert runtime_settings.get_gate_philosophy_enabled() is False


def test_verification_extension_includes_gate_philosophy(monkeypatch, long_proposal):
    """Integration smoke: when the extension chain is on AND
    gate_philosophy is on AND the panel finds tension, the final
    verdict's suggested_action is peer_review.
    """
    from app.epistemic import verification_extension as ve
    from app.epistemic.calibration import CalibrationVerdict

    monkeypatch.setattr(
        "app.runtime_settings.get_verification_extension_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.runtime_settings.get_verification_threshold", lambda zone: 0.6
    )
    monkeypatch.setattr(
        "app.runtime_settings.get_verification_retrieval_budget_per_task",
        lambda: 0,
    )
    monkeypatch.setattr(ve, "_resolve_zone", lambda t: "autonomous")
    monkeypatch.setattr(
        "app.epistemic.gate_philosophy._enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.epistemic.gate_philosophy._zone_for_task", lambda t: "autonomous"
    )
    fake_panel = SimpleNamespace(
        skipped_reason=None,
        unresolved_tensions=["t1", "t2"],
        perspectives=[SimpleNamespace(tradition="Stoic", claim="x")],
    )
    monkeypatch.setattr(
        "app.philosophy.dialectics.consult_panel",
        lambda q, **kw: fake_panel,
    )
    monkeypatch.setattr(
        "app.epistemic.gate_philosophy._file_tension",
        lambda *a, **kw: "tension-xyz",
    )
    monkeypatch.setattr(
        "app.epistemic.gate_philosophy._file_thread",
        lambda *a, **kw: "thread-xyz",
    )
    # Stub extract_claims so the claim-source evaluator doesn't fire
    monkeypatch.setattr(
        "app.epistemic.verification_extension.extract_claims",
        lambda text: [],
    )
    verdict = CalibrationVerdict(
        suggested_action="ship",
        biases_detected=[],
        forced_verifier_claim_ids=[],
        note_for_post_mortem="",
    )
    extended, notes = ve.apply_verification_extension(
        verdict=verdict,
        proposal_text=long_proposal,
        task_id="task-1",
    )
    assert extended.suggested_action == "peer_review"
    assert any("philosophy" in n for n in notes)
