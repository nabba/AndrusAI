"""Gap 2 — interest_goal_emitter tests.

Covers:
  * Master switch off → run() short-circuits with skipped_reason
  * Welfare-breach + operator-unavailable gates short-circuit
  * Pattern qualification (strength threshold, prior-detection
    threshold, decline cooldown, person/topic discriminator)
  * Emission window cap (1 emission per 7 days)
  * Spawn writes an ExecutorRun and registers a Signal-bridge entry
  * decline() aborts the run + records cooldown + ledger landmark
  * State file is idempotent across loads
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Same pattern as the drill tests — the test surface imports
# autonomous_executor which transitively pulls pydantic.
pytest.importorskip("pydantic")


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    from app import paths as _paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", workspace)
    # Reset autonomous_executor store base_dir so per-test runs
    # don't see leaked state from a prior test.
    try:
        from app.autonomous_executor import store as _store

        _store._base_dir_override = workspace / "autonomous_executor"
        _store._INDEX = None
    except Exception:
        pass
    return workspace


def _build_pattern_row(
    topic="kaicart",
    *,
    modalities=("email", "chat", "browse"),
    occurrences=15,
    strength=0.85,
    detected_at=None,
    kind="topic",
):
    return {
        "topic": topic,
        "modalities": list(modalities),
        "occurrences_per_modality": {m: occurrences // len(modalities) for m in modalities},
        "occurrences_total": occurrences,
        "window_days": 21,
        "strength": strength,
        "detected_at": detected_at or datetime.now(timezone.utc).isoformat(),
        "first_seen_age_days": 22.0,
        "triggered_tension_boost": 0,
        "kind": kind,
    }


def test_master_switch_off_short_circuits(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: False)
    out = ige.run()
    assert out["emitted"] == 0
    assert out["skipped_reason"] == "master_switch_off"


def test_executor_disabled_short_circuits(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: True)
    monkeypatch.setattr(ige, "_executor_enabled", lambda: False)
    out = ige.run()
    assert out["skipped_reason"] == "executor_disabled"


def test_welfare_breach_short_circuits(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: True)
    monkeypatch.setattr(ige, "_executor_enabled", lambda: True)
    monkeypatch.setattr(ige, "_welfare_breaching", lambda: True)
    out = ige.run()
    assert out["skipped_reason"] == "welfare_breach"


def test_operator_unavailable_short_circuits(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: True)
    monkeypatch.setattr(ige, "_executor_enabled", lambda: True)
    monkeypatch.setattr(ige, "_welfare_breaching", lambda: False)
    monkeypatch.setattr(ige, "_operator_unavailable", lambda: True)
    out = ige.run()
    assert out["skipped_reason"] == "operator_unavailable"


def test_qualify_rejects_below_strength_threshold():
    from app.companion import interest_goal_emitter as ige

    weak = _build_pattern_row(strength=0.5)
    # Need 2 prior detections, so include twice in the input list
    out = ige._qualify_patterns([weak, weak], state={"declines": {}}, now=datetime.now(timezone.utc))
    assert out == []


def test_qualify_rejects_person_kind():
    from app.companion import interest_goal_emitter as ige

    person = _build_pattern_row(kind="person")
    out = ige._qualify_patterns(
        [person, person], state={"declines": {}}, now=datetime.now(timezone.utc)
    )
    assert out == []


def test_qualify_requires_min_prior_detections():
    from app.companion import interest_goal_emitter as ige

    # Single appearance → fewer than _MIN_PRIOR_DETECTIONS
    row = _build_pattern_row()
    out = ige._qualify_patterns([row], state={"declines": {}}, now=datetime.now(timezone.utc))
    assert out == []


def test_qualify_accepts_strong_sustained_pattern():
    from app.companion import interest_goal_emitter as ige

    row = _build_pattern_row(topic="kaicart", strength=0.85)
    out = ige._qualify_patterns(
        [row, row, row], state={"declines": {}}, now=datetime.now(timezone.utc)
    )
    assert len(out) == 1
    assert out[0].topic == "kaicart"
    assert out[0].strength >= 0.7


def test_qualify_skips_declined_topic_within_cooldown():
    from app.companion import interest_goal_emitter as ige

    row = _build_pattern_row(topic="kaicart")
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    state = {"declines": {"kaicart": future}}
    out = ige._qualify_patterns([row, row], state=state, now=datetime.now(timezone.utc))
    assert out == []


def test_qualify_allows_topic_after_decline_cooldown_expires():
    from app.companion import interest_goal_emitter as ige

    row = _build_pattern_row(topic="kaicart")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    state = {"declines": {"kaicart": past}}
    out = ige._qualify_patterns([row, row], state=state, now=datetime.now(timezone.utc))
    assert len(out) == 1


def test_emission_window_blocks_second_emit_within_seven_days(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: True)
    monkeypatch.setattr(ige, "_executor_enabled", lambda: True)
    monkeypatch.setattr(ige, "_welfare_breaching", lambda: False)
    monkeypatch.setattr(ige, "_operator_unavailable", lambda: False)
    # Pre-load state with one emission within the window
    state = {
        "emissions": [
            {"emitted_at": datetime.now(timezone.utc).isoformat(), "topic": "x"}
        ],
        "declines": {},
    }
    ige._save_state(state)
    out = ige.run()
    assert out["skipped_reason"] == "emission_window_full"


def test_emission_spawns_executor_run(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    qp = ige.QualifiedPattern(
        topic="kaicart",
        modalities=["email", "chat", "browse"],
        occurrences_total=15,
        strength=0.85,
        detected_at=datetime.now(timezone.utc).isoformat(),
        prior_detections=3,
    )
    # No Signal — that's fine, alert is best-effort
    monkeypatch.setattr(ige, "_signal_alert", lambda *a, **kw: None)
    monkeypatch.setattr(ige, "_emit_landmark", lambda *a, **kw: None)
    outcome = ige.emit_for_pattern(qp)
    assert outcome["ok"] is True
    run_id = outcome["run_id"]
    assert run_id.startswith("run-")

    from app.autonomous_executor import store as _store

    run = _store.get(run_id)
    assert run is not None
    assert run.requestor == "interest_goal_emitter"
    assert run.zone == "autonomous"
    # Budget enforced at ExecutorRun creation
    assert float(run.budget.cap_usd) == pytest.approx(2.0)


def test_emission_writes_state_with_topic_key(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    qp = ige.QualifiedPattern(
        topic="KaiCart",
        modalities=["email", "chat"],
        occurrences_total=10,
        strength=0.8,
        detected_at=datetime.now(timezone.utc).isoformat(),
        prior_detections=2,
    )
    monkeypatch.setattr(ige, "_signal_alert", lambda *a, **kw: "1234567890")
    monkeypatch.setattr(ige, "_emit_landmark", lambda *a, **kw: None)
    ige.emit_for_pattern(qp)
    state = ige._load_state()
    assert len(state["emissions"]) == 1
    assert state["emissions"][0]["topic_key"] == "kaicart"
    assert state["emissions"][0]["signal_ts"] == "1234567890"


def test_decline_records_cooldown_and_aborts_run(monkeypatch):
    from app.companion import interest_goal_emitter as ige
    from app.autonomous_executor import models as _models, store as _store

    # Spawn a real run we can then decline
    qp = ige.QualifiedPattern(
        topic="kaicart",
        modalities=["email"],
        occurrences_total=5,
        strength=0.8,
        detected_at=datetime.now(timezone.utc).isoformat(),
        prior_detections=2,
    )
    monkeypatch.setattr(ige, "_signal_alert", lambda *a, **kw: None)
    monkeypatch.setattr(ige, "_emit_landmark", lambda *a, **kw: None)
    outcome = ige.emit_for_pattern(qp)
    run_id = outcome["run_id"]

    out = ige.decline("KaiCart", run_id=run_id)
    assert out["ok"] is True
    assert out["topic_key"] == "kaicart"

    state = ige._load_state()
    assert "kaicart" in state["declines"]

    run = _store.get(run_id)
    assert run is not None
    assert run.status == _models.ExecutorStatus.ABORTED
    assert "operator" in run.abort_reason.lower() or "declined" in run.abort_reason.lower()


def test_run_emits_when_pattern_qualifies(monkeypatch):
    from app.companion import interest_goal_emitter as ige

    monkeypatch.setattr(ige, "_master_switch_on", lambda: True)
    monkeypatch.setattr(ige, "_executor_enabled", lambda: True)
    monkeypatch.setattr(ige, "_welfare_breaching", lambda: False)
    monkeypatch.setattr(ige, "_operator_unavailable", lambda: False)

    row = _build_pattern_row(topic="signal_resilience", strength=0.85)
    monkeypatch.setattr(
        "app.companion.cross_modal_patterns.list_recent_patterns",
        lambda n=50, min_strength=0.7: [row, row, row],
    )
    monkeypatch.setattr(ige, "_signal_alert", lambda *a, **kw: None)
    monkeypatch.setattr(ige, "_emit_landmark", lambda *a, **kw: None)

    out = ige.run()
    assert out["emitted"] == 1
    assert out["topic"] == "signal_resilience"
    assert out["run_id"].startswith("run-")


def test_signal_bridge_register_and_lookup(isolated_workspace):
    from app import interest_goal_signal_bridge as bridge

    bridge.register("1700000000123", "run-abc123def456")
    assert bridge.find_run_id("1700000000123") == "run-abc123def456"
    bridge.unregister("run-abc123def456")
    assert bridge.find_run_id("1700000000123") is None


def test_signal_bridge_purges_expired_entries(monkeypatch, isolated_workspace):
    from app import interest_goal_signal_bridge as bridge

    p = bridge._bridge_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    stale_epoch = datetime.now(timezone.utc).timestamp() - 26 * 3600
    fresh_epoch = datetime.now(timezone.utc).timestamp()
    p.write_text(
        json.dumps(
            {
                "stale_ts": {"run_id": "run-stale", "created_at_epoch": stale_epoch},
                "fresh_ts": {"run_id": "run-fresh", "created_at_epoch": fresh_epoch},
            }
        )
    )
    assert bridge.find_run_id("stale_ts") is None
    assert bridge.find_run_id("fresh_ts") == "run-fresh"


def test_master_switch_default_off():
    """Goodhart guard: emitter is opt-in even after wiring."""
    from app import runtime_settings

    assert runtime_settings.get_interest_goal_emitter_enabled() is False


def test_qualified_pattern_renders_goal_text():
    from app.companion.interest_goal_emitter import QualifiedPattern

    qp = QualifiedPattern(
        topic="kaicart",
        modalities=["email", "chat", "browse"],
        occurrences_total=15,
        strength=0.85,
        detected_at="2026-05-24T00:00:00+00:00",
        prior_detections=3,
    )
    text = qp.as_goal_text()
    assert "kaicart" in text
    assert "3 modalities" in text
    assert "notes/" in text


def test_run_id_registered_on_signal_alert(monkeypatch):
    """The Signal bridge MUST be populated when an alert lands.
    Otherwise the 👎 reaction handler can't find the run id."""
    from app.companion import interest_goal_emitter as ige
    from app import interest_goal_signal_bridge as bridge

    qp = ige.QualifiedPattern(
        topic="kaicart",
        modalities=["email"],
        occurrences_total=5,
        strength=0.8,
        detected_at=datetime.now(timezone.utc).isoformat(),
        prior_detections=2,
    )
    monkeypatch.setattr(ige, "_signal_alert", lambda *a, **kw: "1700000000999")
    monkeypatch.setattr(ige, "_emit_landmark", lambda *a, **kw: None)
    outcome = ige.emit_for_pattern(qp)
    run_id = outcome["run_id"]
    assert bridge.find_run_id("1700000000999") == run_id
