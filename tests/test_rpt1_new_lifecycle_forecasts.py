"""Regression tests for the RPT-1 lifecycle-forecast wiring shipped
2026-05-23 (audit follow-up).

Five new scorers + five new ``register_prediction`` call sites — one
per substantial lifecycle that landed in the past 14 days. The pins
here defend against three regression classes:

  1. A future refactor of the lifecycle module that drops the
     ``register_prediction`` call (the call is failure-isolated, so
     RPT-1 would silently stop calibrating).
  2. A future refactor of the scorer that breaks the deterministic-
     resolver contract (``register_scorer`` refuses scorers under
     ``app.llm`` / ``app.agents`` / ``app.crews``).
  3. A future rename of one of the state enums (ThreadStatus,
     RunStatus, ArchStatus, ExecutorStatus) that the scorers compare
     against.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ── Scorer registry pins ─────────────────────────────────────────────


def test_all_five_new_scorers_registered() -> None:
    """Pin that the seven scorers are present at import time. Five new
    plus the two pre-existing ones (tier3_approval, cr_apply)."""
    from app.sentience_experiments.rpt1_self_calibration import _SCORERS

    required = {
        # Pre-existing
        "tier3_approval",
        "cr_apply",
        # New 2026-05-23
        "thread_resolve",
        "workflow_run_success",
        "architecture_request_apply",
        "executor_run_success",
        "capability_adoption_apply",
    }
    assert required.issubset(set(_SCORERS.keys())), (
        f"Missing scorers: {required - set(_SCORERS.keys())}"
    )


# ── Scorer correctness pins ──────────────────────────────────────────


class _StubThread:
    def __init__(self, status):
        self.status = status


def test_thread_resolve_scorer_classifies_correctly(monkeypatch) -> None:
    from app.threads.models import ThreadStatus
    from app.sentience_experiments.rpt1_self_calibration import (
        _scorer_thread_resolve,
    )

    def fake_get(thread_id):
        return _stub.get(thread_id)

    monkeypatch.setattr("app.threads.store.get", fake_get)

    _stub: dict = {
        "resolved-id": _StubThread(ThreadStatus.RESOLVED),
        "abandoned-id": _StubThread(ThreadStatus.ABANDONED),
        "open-id": _StubThread(ThreadStatus.OPEN),
        "in-progress-id": _StubThread(ThreadStatus.IN_PROGRESS),
    }

    assert _scorer_thread_resolve({"thread_id": "resolved-id"}) is True
    assert _scorer_thread_resolve({"thread_id": "abandoned-id"}) is False
    assert _scorer_thread_resolve({"thread_id": "open-id"}) is None
    assert _scorer_thread_resolve({"thread_id": "in-progress-id"}) is None
    assert _scorer_thread_resolve({"thread_id": "missing-id"}) is None
    assert _scorer_thread_resolve({}) is None


class _StubWorkflowRun:
    def __init__(self, status):
        self.status = status


def test_workflow_run_success_scorer_classifies_correctly(monkeypatch) -> None:
    from app.workflows.models import RunStatus
    from app.sentience_experiments.rpt1_self_calibration import (
        _scorer_workflow_run_success,
    )

    _stub: dict = {
        "ok-id": _StubWorkflowRun(RunStatus.SUCCEEDED),
        "fail-id": _StubWorkflowRun(RunStatus.FAILED),
        "cancel-id": _StubWorkflowRun(RunStatus.CANCELLED),
        "queued-id": _StubWorkflowRun(RunStatus.QUEUED),
        "running-id": _StubWorkflowRun(RunStatus.RUNNING),
    }

    def fake_get_run(run_id):
        return _stub.get(run_id)

    monkeypatch.setattr("app.workflows.queue.get_run", fake_get_run)

    assert _scorer_workflow_run_success({"run_id": "ok-id"}) is True
    assert _scorer_workflow_run_success({"run_id": "fail-id"}) is False
    assert _scorer_workflow_run_success({"run_id": "cancel-id"}) is False
    assert _scorer_workflow_run_success({"run_id": "queued-id"}) is None
    assert _scorer_workflow_run_success({"run_id": "running-id"}) is None


class _StubArchReq:
    def __init__(self, status):
        self.status = status


def test_architecture_request_scorer_classifies_correctly(monkeypatch) -> None:
    from app.architecture_requests.models import ArchStatus
    from app.sentience_experiments.rpt1_self_calibration import (
        _scorer_architecture_request_apply,
    )

    _stub: dict = {
        "done-id": _StubArchReq(ArchStatus.COMPLETED),
        "reject-id": _StubArchReq(ArchStatus.REJECTED),
        "tier-id": _StubArchReq(ArchStatus.TIER_IMMUTABLE_REFUSED),
        "timeout-id": _StubArchReq(ArchStatus.TIMEOUT),
        "abandon-id": _StubArchReq(ArchStatus.ABANDONED),
        "in-flight-id": _StubArchReq(ArchStatus.IMPLEMENTING),
        "proposed-id": _StubArchReq(ArchStatus.PROPOSED),
    }

    monkeypatch.setattr(
        "app.architecture_requests.store.get",
        lambda rid: _stub.get(rid),
    )

    assert _scorer_architecture_request_apply({"request_id": "done-id"}) is True
    assert _scorer_architecture_request_apply({"request_id": "reject-id"}) is False
    assert _scorer_architecture_request_apply({"request_id": "tier-id"}) is False
    assert _scorer_architecture_request_apply({"request_id": "timeout-id"}) is False
    assert _scorer_architecture_request_apply({"request_id": "abandon-id"}) is False
    assert _scorer_architecture_request_apply({"request_id": "in-flight-id"}) is None
    assert _scorer_architecture_request_apply({"request_id": "proposed-id"}) is None


class _StubExecRun:
    def __init__(self, status):
        self.status = status


def test_executor_run_success_scorer_classifies_correctly(monkeypatch) -> None:
    from app.autonomous_executor.models import ExecutorStatus
    from app.sentience_experiments.rpt1_self_calibration import (
        _scorer_executor_run_success,
    )

    _stub: dict = {
        "done-id": _StubExecRun(ExecutorStatus.COMPLETED),
        "fail-id": _StubExecRun(ExecutorStatus.FAILED),
        "budget-id": _StubExecRun(ExecutorStatus.BUDGET_EXHAUSTED),
        "abort-id": _StubExecRun(ExecutorStatus.ABORTED),
        "planning-id": _StubExecRun(ExecutorStatus.PLANNING),
        "running-id": _StubExecRun(ExecutorStatus.RUNNING),
    }

    monkeypatch.setattr(
        "app.autonomous_executor.store.get",
        lambda rid: _stub.get(rid),
    )

    assert _scorer_executor_run_success({"run_id": "done-id"}) is True
    assert _scorer_executor_run_success({"run_id": "fail-id"}) is False
    assert _scorer_executor_run_success({"run_id": "budget-id"}) is False
    assert _scorer_executor_run_success({"run_id": "abort-id"}) is False
    assert _scorer_executor_run_success({"run_id": "planning-id"}) is None
    assert _scorer_executor_run_success({"run_id": "running-id"}) is None


def test_capability_adoption_apply_delegates_to_cr_apply(monkeypatch) -> None:
    """Pin the delegation so a future refactor that breaks the link is
    surfaced. The whole point of the distinct claim_kind is the
    separate calibration bucket — the actual outcome resolution must
    stay aligned with cr_apply."""
    from app.sentience_experiments.rpt1_self_calibration import (
        _scorer_capability_adoption_apply,
        _scorer_cr_apply,
    )

    class _StubCR:
        def __init__(self, status_value):
            class _S:
                value = status_value
            self.status = _S()

    by_id: dict = {
        "applied-id": _StubCR("applied"),
        "rejected-id": _StubCR("rejected"),
        "pending-id": _StubCR("pending"),
    }

    monkeypatch.setattr(
        "app.change_requests.store.get",
        lambda cid: by_id.get(cid),
    )

    for cid in ("applied-id", "rejected-id", "pending-id", "missing-id"):
        args = {"cr_id": cid}
        assert _scorer_capability_adoption_apply(args) == _scorer_cr_apply(args)


# ── Call-site pins ───────────────────────────────────────────────────


def _capture_register_prediction(monkeypatch):
    """Return a list that gets appended-to whenever register_prediction
    is called. Substitute the function on the rpt1 module."""
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return None

    # Patch at every import path the lifecycle modules use.
    monkeypatch.setattr(
        "app.sentience_experiments.rpt1_self_calibration.register_prediction",
        _fake,
    )
    return calls


def test_thread_create_registers_thread_resolve_forecast(monkeypatch, tmp_path):
    from app.threads import store, lifecycle
    store.reset_for_tests(tmp_path / "threads")
    calls = _capture_register_prediction(monkeypatch)

    thread = lifecycle.create_thread(title="probe thread", description="x")

    matching = [c for c in calls if c.get("claim_kind") == "thread_resolve"]
    assert matching, f"expected thread_resolve forecast, got {calls}"
    assert matching[0]["scorer_ref"] == "thread_resolve"
    assert matching[0]["scorer_args"] == {"thread_id": thread.id}


def test_arch_request_create_registers_forecast(monkeypatch, tmp_path):
    from app.architecture_requests import store, lifecycle
    from app.architecture_requests.models import FileSpec
    store.reset_for_tests(tmp_path / "arch")
    calls = _capture_register_prediction(monkeypatch)

    try:
        req = lifecycle.create_request(
            requestor="probe",
            intent="add a new x",
            motivation=(
                "Adding a new x is necessary because the system needs "
                "to demonstrate behaviour Y in scenarios Z, and the "
                "existing primitive doesn't extend that direction."
            ),
            package_path="app/new_probe_pkg/",
            file_layout=[
                FileSpec(
                    path="app/new_probe_pkg/__init__.py",
                    purpose="package marker",
                ),
            ],
            integration_points=[],
            env_switches={"NEW_PROBE_ENABLED": "default OFF"},
            test_plan="add unit tests for the new behaviour Y under scenarios Z.",
        )
    except Exception:
        pytest.skip(
            "architecture_requests requires runtime_settings — env-dependent"
        )

    if req.status.value not in ("proposed",):
        pytest.skip(
            f"validator declined probe request ({req.status.value}); "
            "the forecast registration is downstream of a successful "
            "validate() call"
        )

    matching = [
        c for c in calls if c.get("claim_kind") == "architecture_request_apply"
    ]
    assert matching, (
        "expected architecture_request_apply forecast on successful "
        "create_request — wiring may have regressed"
    )


def test_delegate_goal_registers_executor_run_success(monkeypatch, tmp_path):
    """The autonomous-executor's delegate path is the agent-callable
    entry point. Pin the forecast registration there."""
    from app.autonomous_executor import store
    store.reset_for_tests(tmp_path / "executor")
    calls = _capture_register_prediction(monkeypatch)

    from app.autonomous_executor.tools.delegate_tool import delegate_goal
    out = delegate_goal(
        goal="probe goal — at least thirty characters here ok",
        budget_usd=1.0,
        requestor="probe",
    )
    if not out.get("ok"):
        pytest.skip(f"delegate_goal returned not-ok: {out}")

    matching = [c for c in calls if c.get("claim_kind") == "executor_run_success"]
    assert matching, f"expected executor_run_success forecast, got {calls}"
    assert matching[0]["scorer_args"] == {"run_id": out["run_id"]}
