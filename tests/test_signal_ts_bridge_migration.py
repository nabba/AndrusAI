"""Characterization tests for the 4 bridges migrated onto SignalTsBridge.

Pin each module's PUBLIC API + on-disk value schema + TTL-purge + bespoke ops,
so the 2026-06-07 consolidation is provably behaviour-neutral. Host-runnable:
the bridge modules are pure local logic (the live Signal flow lives in main.py,
which calls these unchanged public functions).
"""
import json
import time

import pytest


# ── governance ───────────────────────────────────────────────────────
def test_governance_roundtrip_schema_unregister_purge(tmp_path, monkeypatch):
    gov = pytest.importorskip("app.governance_signal_bridge")
    monkeypatch.setattr(gov, "WORKSPACE_ROOT", tmp_path)

    gov.register(123, "req-abc")
    assert gov.find_request_id(123) == "req-abc"

    on_disk = json.loads((tmp_path / "governance_signal_bridge.json").read_text())["123"]
    assert set(on_disk) == {"request_id", "created_at", "created_at_epoch"}

    gov.unregister("req-abc")
    assert gov.find_request_id(123) is None

    # expired entry is purged on find
    (tmp_path / "governance_signal_bridge.json").write_text(
        json.dumps({"999": {"request_id": "old", "created_at_epoch": time.time() - 999_999}}))
    assert gov.find_request_id(999) is None


# ── interest_goal ────────────────────────────────────────────────────
def test_interest_goal_roundtrip_and_unregister(tmp_path, monkeypatch):
    ig = pytest.importorskip("app.interest_goal_signal_bridge")
    monkeypatch.setattr("app.paths.WORKSPACE_ROOT", tmp_path)

    ig.register("t1", "run-x")
    assert ig.find_run_id("t1") == "run-x"
    on_disk = json.loads((tmp_path / "interest_goal_signal_bridge.json").read_text())["t1"]
    assert set(on_disk) == {"run_id", "created_at", "created_at_epoch"}
    ig.unregister("run-x")
    assert ig.find_run_id("t1") is None


# ── briefing feedback (8-day TTL, idempotent register) ───────────────
def test_feedback_roundtrip_schema_and_idempotent(tmp_path, monkeypatch):
    fb = pytest.importorskip("app.life_companion.briefing_evolution.feedback_bridge")
    monkeypatch.setattr("app.paths.WORKSPACE_ROOT", tmp_path)

    fb.register("t2", "sec-1")
    assert fb.find_section_for_ts("t2") == "sec-1"
    path = tmp_path / "life_companion" / "briefing_evolution" / "feedback_bridge.json"
    on_disk = json.loads(path.read_text())["t2"]
    assert set(on_disk) == {"section_id", "created_at_iso", "created_at_epoch"}
    # idempotent: same pair is a no-op, doesn't raise or duplicate
    fb.register("t2", "sec-1")
    assert fb.find_section_for_ts("t2") == "sec-1"


# ── epistemic reaction (registered_at ts-field, persist_on_get=False) ─
def test_reaction_roundtrip_schema_and_handle(tmp_path, monkeypatch):
    rb = pytest.importorskip("app.epistemic.reaction_bridge")
    monkeypatch.setattr("app.paths.WORKSPACE_ROOT", tmp_path)

    rb.register(99, task_id="task-1", gate_action="ship", reply_preview="hello")
    ctx = rb.find_context(99)
    assert ctx and ctx["task_id"] == "task-1" and ctx["gate_action"] == "ship"
    on_disk = json.loads((tmp_path / "epistemic_reaction_bridge.json").read_text())["99"]
    assert "registered_at" in on_disk  # custom ts-field preserved
    assert "created_at_epoch" not in on_disk

    # 👍 is a no-op; non-tracked ts → None
    assert rb.handle_reaction(99, "👍") is None
    assert rb.handle_reaction(123456, "👎") is None
    # 👎 on a tracked "ship" reply → operator-pushback path → ack + ledger row
    ack = rb.handle_reaction(99, "👎")
    assert ack and "pushback" in ack.lower()
    assert (tmp_path / "epistemic" / "operator_disagreements.jsonl").exists()
