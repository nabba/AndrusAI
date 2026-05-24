"""Tests for app.healing.monitors.config_coherence — Gap #3 invariant
checker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.healing.monitors import config_coherence as cc  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(cc, "_workspace", lambda: tmp_path)
    return tmp_path


def test_no_findings_on_canonical_defaults() -> None:
    """A snapshot matching the documented defaults should produce zero
    findings — the rule set must NOT alert on the system's natural
    initial state."""
    defaults = {
        "goodhart_hard_gate_disabled": False,
        "goodhart_hard_gate_enforcing": False,
        "structured_diagnosis_threshold_floor": 0.50,
        "structured_diagnosis_threshold_ceiling": 0.95,
        "structured_diagnosis_threshold_override": None,
        "vision_cu_enabled": False,
        "vision_cu_monthly_cap_usd": 10.0,
        "binauthz_mode": "AUDIT",
        "binauthz_attestor_name": "",
        "person_correlation_enabled": False,
        "person_centrality_enabled": False,
        "person_suggestions_enabled": False,
        "person_correlation_social_graph_enabled": False,
        "graph_shortest_path_enabled": False,
        "graph_communities_enabled": False,
        "graph_bridges_enabled": False,
        "graph_suggestions_enabled": False,
        "resilience_drills_enabled": True,
        "upgrade_lifecycle_enabled": True,
        "architecture_requests_enabled": True,
        "architecture_adoption_monitor_enabled": True,
        "chat_blocked_models": [],
        "no_function_calling_models": [],
        "vpc_sc_enabled": False,
        "vpc_sc_dry_run": True,
    }
    assert cc.evaluate(defaults) == []


def test_goodhart_disabled_and_enforcing_fires() -> None:
    snap = {"goodhart_hard_gate_disabled": True, "goodhart_hard_gate_enforcing": True}
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "goodhart_disabled_and_enforcing" for f in findings)
    matching = [f for f in findings if f.rule_id == "goodhart_disabled_and_enforcing"][0]
    assert matching.severity == "warning"


def test_inverted_band_fires_critical() -> None:
    snap = {
        "structured_diagnosis_threshold_floor": 0.90,
        "structured_diagnosis_threshold_ceiling": 0.50,
    }
    findings = cc.evaluate(snap)
    matching = [f for f in findings if f.rule_id == "structured_diagnosis_band_inverted"]
    assert len(matching) == 1
    assert matching[0].severity == "critical"


def test_override_out_of_band_fires() -> None:
    snap = {
        "structured_diagnosis_threshold_floor": 0.50,
        "structured_diagnosis_threshold_ceiling": 0.95,
        "structured_diagnosis_threshold_override": 0.99,
    }
    findings = cc.evaluate(snap)
    matching = [f for f in findings if f.rule_id == "structured_diagnosis_override_out_of_band"]
    assert len(matching) == 1


def test_override_inside_band_does_not_fire() -> None:
    snap = {
        "structured_diagnosis_threshold_floor": 0.50,
        "structured_diagnosis_threshold_ceiling": 0.95,
        "structured_diagnosis_threshold_override": 0.80,
    }
    findings = cc.evaluate(snap)
    assert not any(f.rule_id == "structured_diagnosis_override_out_of_band" for f in findings)


def test_override_null_does_not_fire() -> None:
    """An unset override (None) must never trip the out-of-band rule."""
    snap = {
        "structured_diagnosis_threshold_floor": 0.50,
        "structured_diagnosis_threshold_ceiling": 0.95,
        "structured_diagnosis_threshold_override": None,
    }
    findings = cc.evaluate(snap)
    assert not any(f.rule_id == "structured_diagnosis_override_out_of_band" for f in findings)


def test_vision_cu_enabled_with_zero_cap() -> None:
    snap = {"vision_cu_enabled": True, "vision_cu_monthly_cap_usd": 0.0}
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "vision_cu_enabled_with_zero_cap" for f in findings)


def test_binauthz_enforce_without_attestor_is_critical() -> None:
    snap = {"binauthz_mode": "ENFORCE", "binauthz_attestor_name": ""}
    findings = cc.evaluate(snap)
    matching = [f for f in findings if f.rule_id == "binauthz_enforce_without_attestor"]
    assert len(matching) == 1
    assert matching[0].severity == "critical"


def test_binauthz_enforce_with_attestor_does_not_fire() -> None:
    snap = {"binauthz_mode": "ENFORCE", "binauthz_attestor_name": "cosign-prod"}
    findings = cc.evaluate(snap)
    assert not any(f.rule_id == "binauthz_enforce_without_attestor" for f in findings)


def test_person_centrality_without_correlation() -> None:
    snap = {"person_centrality_enabled": True, "person_correlation_enabled": False}
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "person_centrality_without_correlation" for f in findings)


def test_graph_feature_without_social_graph() -> None:
    snap = {
        "person_correlation_social_graph_enabled": False,
        "graph_communities_enabled": True,
    }
    findings = cc.evaluate(snap)
    matching = [f for f in findings if f.rule_id == "graph_feature_without_social_graph"]
    assert len(matching) == 1
    assert "graph_communities_enabled" in matching[0].detail


def test_drills_disabled_with_per_drill_on() -> None:
    snap = {
        "resilience_drills_enabled": False,
        "drill_backup_restore_enabled": True,
    }
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "drills_disabled_with_per_drill_on" for f in findings)


def test_apply_hook_without_any_writer() -> None:
    snap = {
        "upgrade_lifecycle_apply_hook_enabled": True,
        "upgrade_lifecycle_requirements_writer_enabled": False,
        "upgrade_lifecycle_dockerfile_writer_enabled": False,
        "upgrade_lifecycle_pyproject_writer_enabled": False,
    }
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "apply_hook_without_any_writer" for f in findings)


def test_apply_hook_with_at_least_one_writer_is_ok() -> None:
    snap = {
        "upgrade_lifecycle_apply_hook_enabled": True,
        "upgrade_lifecycle_requirements_writer_enabled": True,
    }
    findings = cc.evaluate(snap)
    assert not any(f.rule_id == "apply_hook_without_any_writer" for f in findings)


def test_chat_blocklist_runaway_at_threshold() -> None:
    snap = {
        "chat_blocked_models": [f"m{i}" for i in range(40)],
        "no_function_calling_models": [f"n{i}" for i in range(20)],
    }
    findings = cc.evaluate(snap)
    assert any(f.rule_id == "chat_blocklist_runaway" for f in findings)


def test_chat_blocklist_below_threshold_ok() -> None:
    snap = {"chat_blocked_models": ["a", "b"], "no_function_calling_models": []}
    findings = cc.evaluate(snap)
    assert not any(f.rule_id == "chat_blocklist_runaway" for f in findings)


def test_run_persists_state_and_returns_summary(monkeypatch, _tmp_workspace: Path) -> None:
    """Full run path with a coherent snapshot — no findings, no alert."""
    monkeypatch.setattr(
        "app.runtime_settings.snapshot",
        lambda: {"resilience_drills_enabled": True},
    )
    monkeypatch.setattr(cc, "_enabled", lambda: True)
    result = cc.run(now=1000.0)
    assert result["ran"] is True
    assert result["n_findings"] == 0
    state = json.loads((_tmp_workspace / "healing" / "config_coherence_state.json").read_text())
    assert state["last_run_at"] == 1000.0


def test_run_respects_internal_cadence(monkeypatch, _tmp_workspace: Path) -> None:
    monkeypatch.setattr(
        "app.runtime_settings.snapshot",
        lambda: {"resilience_drills_enabled": True},
    )
    monkeypatch.setattr(cc, "_enabled", lambda: True)
    cc.run(now=1000.0)
    second = cc.run(now=1000.0 + 60)  # 60s later — well within cadence
    assert second["ran"] is False


def test_run_skips_when_master_off(monkeypatch) -> None:
    monkeypatch.setattr(cc, "_enabled", lambda: False)
    result = cc.run(now=1000.0)
    assert result == {"ran": False, "skipped": True}


def test_alert_dedup_window(monkeypatch, _tmp_workspace: Path) -> None:
    """Same finding-set within 28d should suppress the second alert."""
    monkeypatch.setattr(cc, "_enabled", lambda: True)
    monkeypatch.setattr(
        "app.runtime_settings.snapshot",
        lambda: {
            "goodhart_hard_gate_disabled": True,
            "goodhart_hard_gate_enforcing": True,
        },
    )
    sent: list[dict] = []

    def fake_notify(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("app.notify.notify", fake_notify)
    r1 = cc.run(now=1000.0)
    # Advance past the internal cadence but inside the dedup window.
    r2 = cc.run(now=1000.0 + cc._INTERNAL_CADENCE_S + 1)
    assert r1["alert_sent"] is True
    assert r2["alert_sent"] is False
    assert len(sent) == 1


def test_alert_refires_after_dedup_window(monkeypatch, _tmp_workspace: Path) -> None:
    monkeypatch.setattr(cc, "_enabled", lambda: True)
    monkeypatch.setattr(
        "app.runtime_settings.snapshot",
        lambda: {"goodhart_hard_gate_disabled": True, "goodhart_hard_gate_enforcing": True},
    )
    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))
    cc.run(now=1000.0)
    cc.run(now=1000.0 + cc._DEDUP_WINDOW_S + 100)
    assert len(sent) == 2
