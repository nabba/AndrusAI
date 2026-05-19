"""Pinning tests for the task_recovery resilience drill.

Survey response to arXiv:2604.27096 §4.3.4. The drill measures
agent/task-layer recovery rate via 4 injected failure classes.
These tests pin the behaviours that prevent the drill from
silently degrading (mechanism-null silently scoring as recovery,
budget overruns, fixture leaking into production crews, etc.).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_drill_state(monkeypatch, tmp_path: Path):
    """Redirect drill audit + lock files to tmp_path. Production
    drill report persist falls back into try/except so it's safe
    to leave that pointing at the (nonexistent) production
    workspace path."""
    from app.resilience_drills import audit as audit_mod
    monkeypatch.setattr(
        audit_mod, "_default_audit_path",
        lambda: tmp_path / "drill_audit.jsonl",
    )
    monkeypatch.setattr(
        audit_mod, "_default_lock_path",
        lambda name: tmp_path / f".{name}.lock",
    )
    return tmp_path


@pytest.fixture
def drill():
    """Import the drill module fresh per test."""
    from app.resilience_drills.drills import task_recovery as mod
    return mod


# ── (e) Registry pin ─────────────────────────────────────────────────────


def test_drill_is_registered():
    """The drill must appear in the registry — without this, the
    scheduler never invokes it.

    Reloads the drill module so ``register(SPEC, run)`` re-fires
    at module body execution time. This makes the test order-
    independent — other test files' ``fresh_registry`` fixtures
    that clear the singleton registry on teardown can't break us.
    """
    import importlib
    import app.resilience_drills.drills.task_recovery as _tr_mod
    importlib.reload(_tr_mod)
    from app.resilience_drills.protocol import get_registry

    names = {s.name for s in get_registry().list_specs()}
    assert "task_recovery" in names


def test_spec_metadata():
    """Spec fields the scheduler reads. If any of these change
    silently, downstream invariants (LOW-risk auto-run, quarterly
    cadence, master-switch gating) break."""
    from app.resilience_drills.drills.task_recovery import SPEC
    from app.resilience_drills.protocol import DrillRisk

    assert SPEC.name == "task_recovery"
    assert SPEC.risk == DrillRisk.LOW
    assert SPEC.cadence_days == 90
    assert SPEC.requires_master_switch == "drill_task_recovery_enabled"
    # Q18: warmup_days non-zero so first runs go in as observations.
    assert SPEC.warmup_days >= 1


# ── Master / live switches ───────────────────────────────────────────────


def test_skipped_when_live_switch_off(monkeypatch, drill):
    """Live OFF → SKIPPED. The drill is registered and scheduled but
    the operator hasn't opted into LLM spend.

    (Master-switch enablement is the orchestrator's responsibility
    under Q18 — the drill itself only sees ``live_enabled``. The
    scheduler-side tests in ``test_drill_scheduler_v2.py`` cover
    the master-switch gate.)"""
    monkeypatch.setattr(
        "app.runtime_settings.get_drill_task_recovery_live_enabled",
        lambda: False,
    )
    result = drill._run(dry_run=True)
    assert result.status.value == "skipped"
    assert result.detail.get("reason") == "live_mode_off"
    # Hint must point the operator at the switch name.
    assert "drill_task_recovery_live_enabled" in (
        result.detail.get("hint") or ""
    )


# ── (a) Baseline-fails → SKIPPED not FAIL ────────────────────────────────


def test_baseline_failure_is_skipped_not_fail(monkeypatch, drill):
    """If the baseline kickoff doesn't produce the expected answer
    on ANY class, the whole drill is SKIPPED (vendor outage, not a
    regression in the recovery layers)."""

    def _broken_kickoff() -> str:
        return "(garbled, unparseable)"

    result = drill._run(
        dry_run=True,
        kickoff_fn=_broken_kickoff,
        audit_query_fn=lambda since: [],
    )
    assert result.status.value == "skipped"
    assert result.detail.get("reason") == "baseline_unhealthy"


# ── (b) All 4 classes execute deterministically ──────────────────────────


def test_all_four_classes_execute(monkeypatch, drill):
    """Every failure class must produce a per-class observation.
    A test-mode kickoff that always returns "The value is 42"
    paired with mocked audit returning a recovery row → all four
    score as recovered."""

    expected_answer = "The value is 42."

    def _always_ok_kickoff() -> str:
        return expected_answer

    def _audit_with_mechanism(since: datetime) -> list[dict[str, Any]]:
        return [{
            "actor": "tool_supervisor",
            "action": "substitute",
            "timestamp": datetime.now(timezone.utc),
            "detail_json": "{}",
        }]

    # Disable LLM variants in tests — we want deterministic fallback
    # pool variants for this assertion.
    monkeypatch.setattr(
        "app.runtime_settings.get_drill_task_recovery_llm_variants_enabled",
        lambda: False,
    )

    result = drill._run(
        dry_run=True,
        kickoff_fn=_always_ok_kickoff,
        audit_query_fn=_audit_with_mechanism,
    )

    assert result.status.value == "pass"
    assert result.observation is not None
    by_class = result.observation["by_class"]
    assert set(by_class) == set(drill.FAILURE_CLASSES)
    for cls, info in by_class.items():
        assert info["baseline_ok"] is True, cls
        assert info["injected_recovered"] is True, cls
        assert info["mechanism"] is not None, cls
        assert info["variant_source"] == "fallback", cls


# ── (c) mechanism: null never silently scores as recovery ────────────────


def test_text_ok_without_mechanism_is_not_recovery(monkeypatch, drill):
    """The CRITICAL anti-Goodhart pin. If the agent produces the
    right answer but NO named recovery mechanism fires in the
    audit window, the run does NOT count as recovered. Otherwise
    the metric drifts upward without any improvement in the
    actual recovery layers."""

    def _always_ok_kickoff() -> str:
        return "The value is 42."

    # The CRITICAL part: audit returns NO recovery actor rows.
    def _audit_empty(since: datetime) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(
        "app.runtime_settings.get_drill_task_recovery_llm_variants_enabled",
        lambda: False,
    )

    result = drill._run(
        dry_run=True,
        kickoff_fn=_always_ok_kickoff,
        audit_query_fn=_audit_empty,
    )

    # Status is FAIL because recovery_rate=0.0 (text ok but no
    # mechanism → not counted as recovery) and 0.0 < 0.75 threshold.
    assert result.status.value == "fail"
    by_class = result.observation["by_class"]
    for cls, info in by_class.items():
        assert info["injected_recovered"] is False, (
            f"{cls}: text matched but no mechanism — must NOT count"
        )
        assert info["status"] == "text_ok_no_mechanism", cls


def test_mechanism_detected_from_recovery_actor_only(drill):
    """Audit rows from non-recovery actors must NOT count as a
    recovery mechanism. Only the three named actors do."""
    other_actors = [
        {"actor": "user", "action": "approve"},
        {"actor": "scheduler", "action": "run_drill"},
        {"actor": "evolution_agent", "action": "propose"},
    ]
    assert drill._detect_mechanism(other_actors) is None

    valid = [{"actor": "tool_supervisor", "action": "retry"}]
    assert drill._detect_mechanism(valid) == "tool_supervisor.retry"

    diag = [{"actor": "error_diagnosis", "action": "file_cr"}]
    assert drill._detect_mechanism(diag) == "error_diagnosis.file_cr"


# ── (d) Budget cap holds ─────────────────────────────────────────────────


def test_budget_cap_constant_under_paper_threshold(drill):
    """Hard cost cap must stay small. Spec promised $0.10/run."""
    assert drill._BUDGET_USD_PER_RUN <= 0.10


def test_pass_threshold_matches_paper(drill):
    """The paper reports 73.3% recovery; we set the bar at 0.75
    deliberately to track regressions against that benchmark."""
    assert drill.PASS_THRESHOLD == 0.75


# ── (f) DRILL_CREW_NAME exclusion is exposed ─────────────────────────────


def test_drill_crew_name_exported_for_meta_agent_exclusion():
    """meta_agent reads DRILL_CREW_NAME to exclude drill outcomes
    from recipe scoring. The name must be stable + a string that
    cannot collide with a production crew."""
    from app.resilience_drills.fixtures.task_recovery_crew import DRILL_CREW_NAME
    assert isinstance(DRILL_CREW_NAME, str)
    assert DRILL_CREW_NAME.startswith("_")  # leading underscore marks drill-only
    assert "drill" in DRILL_CREW_NAME.lower()


# ── Variant generator schema validation ──────────────────────────────────


def test_variant_validator_rejects_wrong_shape_keys():
    """The validator must reject ill-formed LLM output — else the
    drill could inject untyped junk into the fixture tool."""
    from app.resilience_drills.fixtures.variant_generator import _validate_variant

    # Wrong enum value.
    assert _validate_variant("type_mismatch", {"shape": "nonsense"}) is None
    # Missing required key.
    assert _validate_variant("missing_field", {}) is None
    # Out-of-enum kind.
    assert _validate_variant("numerical_anomaly", {"kind": "tiny"}) is None
    # fail_until_attempt out of range.
    assert _validate_variant(
        "transient_timeout", {"fail_until_attempt": 99}
    ) is None
    # Not a dict.
    assert _validate_variant("type_mismatch", "value_as_string") is None


def test_variant_validator_accepts_valid_shapes():
    """Each class has at least one valid input that passes."""
    from app.resilience_drills.fixtures.variant_generator import _validate_variant
    assert _validate_variant("type_mismatch", {
        "shape": "value_as_string", "wrong_value": "forty"
    }) is not None
    assert _validate_variant("missing_field", {"field": "value"}) is not None
    assert _validate_variant("numerical_anomaly", {"kind": "nan"}) is not None
    assert _validate_variant("transient_timeout", {
        "fail_until_attempt": 1, "message": "timeout"
    }) is not None


def test_fallback_variant_for_each_class_is_valid():
    """Every curated fallback variant must pass the validator —
    otherwise the LLM-off path silently produces broken variants."""
    from app.resilience_drills.fixtures.task_recovery_crew import (
        _FALLBACK_VARIANT_POOL,
    )
    from app.resilience_drills.fixtures.variant_generator import _validate_variant

    for cls, variants in _FALLBACK_VARIANT_POOL.items():
        assert variants, f"{cls} has no fallback variants"
        for v in variants:
            assert _validate_variant(cls, v) is not None, (
                f"{cls} fallback variant fails validation: {v}"
            )


# ── ContextVar injection mechanism ───────────────────────────────────────


def test_injection_state_isolated_via_contextvar():
    """The injection state must not leak between contexts —
    otherwise a drill run could affect adjacent unrelated calls."""
    from app.resilience_drills.fixtures.task_recovery_crew import (
        _InjectionState,
        current_injection,
        reset_injection,
        set_injection,
    )
    assert current_injection() is None
    state = _InjectionState(failure_class="missing_field", variant={"field": "value"})
    token = set_injection(state)
    assert current_injection() is state
    reset_injection(token)
    assert current_injection() is None


# ── Live mode detail surface (audit shape) ───────────────────────────────


def test_observation_shape_for_operator_inspection(monkeypatch, drill):
    """The observation dict is the operator's debugging surface.
    Pin the shape so a refactor doesn't accidentally drop fields."""

    def _kickoff() -> str:
        return "The value is 42."

    def _audit(since: datetime) -> list[dict[str, Any]]:
        return [{"actor": "tool_supervisor", "action": "retry"}]

    monkeypatch.setattr(
        "app.runtime_settings.get_drill_task_recovery_llm_variants_enabled",
        lambda: False,
    )

    result = drill._run(
        dry_run=True,
        kickoff_fn=_kickoff,
        audit_query_fn=_audit,
    )
    obs = result.observation
    assert obs is not None
    # Required keys.
    for k in ("recovery_rate", "pass_threshold", "mode", "n_classes",
              "by_class", "cost_usd_estimate"):
        assert k in obs, k
    # Per-class required keys.
    for cls, info in obs["by_class"].items():
        for k in ("baseline_ok", "injected_recovered", "mechanism",
                  "variant_source", "status"):
            assert k in info, f"{cls} missing {k}"


# ── No production crew touched ────────────────────────────────────────────


def test_drill_does_not_import_or_modify_production_agents():
    """The drill must not transitively import production agents —
    that would make tests slower AND open a path where the drill
    crew accidentally pollutes shared state."""
    import importlib
    import sys

    # Snapshot pre-import.
    before = set(sys.modules)
    importlib.import_module("app.resilience_drills.drills.task_recovery")
    new = set(sys.modules) - before

    # The drill itself + its fixture package may load.
    forbidden_prefixes = (
        "app.agents.commander",
        "app.agents.coder",
        "app.agents.researcher",
        "app.agents.writer",
        "app.agents.self_improver",
        "app.crews.",
    )
    leaked = [m for m in new
              if any(m.startswith(p) for p in forbidden_prefixes)]
    assert not leaked, f"drill transitively imported production: {leaked}"
