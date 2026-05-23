"""Pin the Round 2 audit follow-up — HOT-4 + AE-2 visibility for the
autonomous executor and resilience drills.

Background — the original Round 1 audit (commit 7560d067) flagged that
the Q5 sentience modules sit on top of canonical input streams and
are structurally blind to substantial new functionality that writes
to its own dedicated audit ledgers:

  * HOT-4 reads ``workspace/observability/loadable_agent_usage.jsonl``;
    the autonomous executor's per-step LLM activity is invisible.
  * AE-2 reads errors + welfare + audit_log; executor + drill failures
    are invisible.

Round 2 closes both gaps by:
  1. Writing executor step telemetry to a parallel JSONL
     (``workspace/observability/executor_step_calls.jsonl``) that
     HOT-4 reads alongside the LoadableAgent stream.
  2. Adding two new outcome adapters in AE-2 for the executor +
     drill audit ledgers.

These pinning tests catch regressions to either wire.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ── HOT-4 visibility ───────────────────────────────────────────────


def test_emit_step_telemetry_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.autonomous_executor.hot4_telemetry import emit_step_telemetry

    class _Run:
        run_id = "run-probe-id"

    class _Step:
        step_id = "step-probe-1"
        iteration = 3
        model = "claude-haiku-4-5"
        tokens_used = 420
        cost_usd = 0.01
        ended_at = "2026-05-23T12:00:00+00:00"

        class _Status:
            value = "completed"

        status = _Status()

    ok = emit_step_telemetry(_Run(), _Step())
    assert ok is True

    p = tmp_path / "observability" / "executor_step_calls.jsonl"
    assert p.exists()
    rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["agent_id"] == "autonomous_executor:run-probe-id"
    assert r["iteration"] == 3
    assert r["model"] == "claude-haiku-4-5"
    assert r["output_tokens"] == 420
    # HOT-4's confidence_proxy uses ratio output/input — input_tokens
    # MUST be at least 1 to avoid div-by-zero.
    assert r["input_tokens"] >= 1


def test_hot4_iter_telemetry_reads_both_paths(tmp_path, monkeypatch):
    """HOT-4's _iter_telemetry must fold rows from both the
    LoadableAgent stream and the executor-telemetry stream."""
    from app.sentience_experiments import hot4_metacog_monitor as hot4

    usage_path = tmp_path / "observability" / "loadable_agent_usage.jsonl"
    executor_path = tmp_path / "observability" / "executor_step_calls.jsonl"

    monkeypatch.setattr(hot4, "_default_usage_path", lambda: usage_path)
    monkeypatch.setattr(
        hot4, "_default_executor_telemetry_path", lambda: executor_path,
    )

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(json.dumps({
        "ts": recent, "agent_id": "researcher",
        "iteration": 1, "model": "claude-haiku",
        "input_tokens": 100, "output_tokens": 50,
    }) + "\n")
    executor_path.write_text(json.dumps({
        "ts": recent, "agent_id": "autonomous_executor:run-probe",
        "iteration": 1, "model": "claude-sonnet",
        "input_tokens": 1, "output_tokens": 420,
    }) + "\n")

    rows = list(hot4._iter_telemetry(window_days=7))
    agents = {r.get("agent_id") for r in rows}
    assert "researcher" in agents
    assert "autonomous_executor:run-probe" in agents


def test_hot4_iter_telemetry_window_filter_holds(tmp_path, monkeypatch):
    """Out-of-window rows must be filtered from BOTH paths."""
    from app.sentience_experiments import hot4_metacog_monitor as hot4

    usage_path = tmp_path / "observability" / "loadable_agent_usage.jsonl"
    executor_path = tmp_path / "observability" / "executor_step_calls.jsonl"

    monkeypatch.setattr(hot4, "_default_usage_path", lambda: usage_path)
    monkeypatch.setattr(
        hot4, "_default_executor_telemetry_path", lambda: executor_path,
    )

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(
        json.dumps({"ts": recent, "agent_id": "a"}) + "\n"
        + json.dumps({"ts": old, "agent_id": "a-old"}) + "\n"
    )
    executor_path.write_text(
        json.dumps({"ts": recent, "agent_id": "exec-recent"}) + "\n"
        + json.dumps({"ts": old, "agent_id": "exec-old"}) + "\n"
    )

    rows = list(hot4._iter_telemetry(window_days=7))
    agents = {r["agent_id"] for r in rows}
    assert agents == {"a", "exec-recent"}


# ── AE-2 visibility ────────────────────────────────────────────────


def test_executor_outcome_adapter_maps_transitions():
    from app.sentience_experiments.ae2_causal_credit import (
        _outcome_kind_from_executor,
    )

    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "blocked"}}
    ) == "executor:blocked"
    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "failed"}}
    ) == "executor:failed"
    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "aborted"}}
    ) == "executor:aborted"
    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "budget_exhausted"}}
    ) == "executor:budget_exhausted"
    # Routine transitions filtered out
    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "running"}}
    ) is None
    assert _outcome_kind_from_executor(
        {"kind": "transition", "payload": {"to": "completed"}}
    ) is None
    # Non-transition kinds
    assert _outcome_kind_from_executor(
        {"kind": "escalation_emitted"}
    ) == "executor:blocked"
    assert _outcome_kind_from_executor(
        {"kind": "step_failed"}
    ) == "executor:step_failed"
    assert _outcome_kind_from_executor(
        {"kind": "run_created"}
    ) is None  # routine — not an outcome


def test_drill_outcome_adapter_maps_status():
    from app.sentience_experiments.ae2_causal_credit import (
        _outcome_kind_from_drill,
    )

    assert _outcome_kind_from_drill(
        {"status": "fail", "drill_name": "vendor_independence"}
    ) == "drill:fail:vendor_independence"
    assert _outcome_kind_from_drill(
        {"status": "error", "drill_name": "embedding_migration"}
    ) == "drill:error:embedding_migration"
    # PASS is filtered (common, expected)
    assert _outcome_kind_from_drill({"status": "pass"}) is None
    assert _outcome_kind_from_drill({"status": "skipped"}) is None


def test_detect_associations_reads_executor_and_drill_paths(
    tmp_path, monkeypatch,
):
    """End-to-end: when both executor and drill audits have outcome
    rows, AE-2 finds the rows via the new readers. We don't assert
    any specific association — that requires action rows too — only
    that the new readers participate without raising."""
    from app.sentience_experiments import ae2_causal_credit as ae2

    # Stub all four reader paths to the tmp dir.
    usage_path = tmp_path / "obs" / "usage.jsonl"
    errors_path = tmp_path / "obs" / "errors.jsonl"
    welfare_path = tmp_path / "affect" / "welfare_audit.jsonl"
    audit_path = tmp_path / "audit_log.jsonl"
    exec_path = tmp_path / "executor_audit.jsonl"
    drill_path = tmp_path / "drill_audit.jsonl"

    for p in (usage_path, errors_path, welfare_path, audit_path,
              exec_path, drill_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ae2, "_default_usage_path", lambda: usage_path)
    monkeypatch.setattr(ae2, "_default_errors_path", lambda: errors_path)
    monkeypatch.setattr(
        ae2, "_default_welfare_audit_path", lambda: welfare_path,
    )
    monkeypatch.setattr(ae2, "_default_audit_log_path", lambda: audit_path)
    monkeypatch.setattr(
        ae2, "_default_executor_audit_path", lambda: exec_path,
    )
    monkeypatch.setattr(
        ae2, "_default_drill_audit_path", lambda: drill_path,
    )

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # An action row so AE-2 has something to correlate against
    usage_path.write_text(json.dumps({
        "ts": recent, "agent_id": "researcher",
        "iteration": 1, "model": "claude",
    }) + "\n")
    # Executor BLOCKED outcome
    exec_path.write_text(json.dumps({
        "ts": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "kind": "transition", "payload": {"to": "blocked"},
        "run_id": "run-x",
    }) + "\n")
    # Drill failure outcome (uses completed_at)
    drill_path.write_text(json.dumps({
        "completed_at": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
        "drill_name": "vendor_independence", "status": "fail",
    }) + "\n")

    # The call must not raise. The output list may be empty (we don't
    # have enough observations to cross the rarity threshold), but
    # that's fine — what matters is the new readers participate.
    result = ae2.detect_associations(
        window_days=7,
        min_observations=1,
        rarity_ceiling=1.0,
        min_density_ratio=0.5,
    )
    assert isinstance(result, list)


def test_drill_path_handles_completed_at_field(tmp_path, monkeypatch):
    """Drill rows use ``completed_at`` not ``ts``. Pin the helper that
    extracts the timestamp so a future refactor doesn't silently lose
    drill rows by falling through to the ``ts``-only ``_iter_jsonl``."""
    from app.sentience_experiments.ae2_causal_credit import (
        _drill_ts_from_row,
    )

    iso = "2026-05-23T12:00:00+00:00"
    assert _drill_ts_from_row({"completed_at": iso}) is not None
    assert _drill_ts_from_row(
        {"started_at": iso}
    ) is not None  # fallback to started_at
    assert _drill_ts_from_row({}) is None
    assert _drill_ts_from_row({"completed_at": "bad-ts"}) is None
