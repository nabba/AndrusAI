"""Regression tests for the HOT-1 trace-input fix + new lifecycle hooks
shipped 2026-05-23 (audit follow-up).

Two intertwined fixes:

  1. **Parser bug** — ``_load_trace_points`` was reading ``ts`` /
     ``valence`` / ``arousal`` / ``controllability`` at top level, but
     the canonical producer (``app.affect.core._append_trace``) writes
     rows shaped ``{"affect": {...}, "viability": {...}}``. So HOT-1
     was reading zero rows from a trace file with thousands of rows
     since Q5 shipped (2026-05-13). The fix prefers the nested shape
     and falls back to flat for backward compatibility.

  2. **New lifecycle hooks** — ``app/autonomous_executor/escalation.py``
     (BLOCKED transition) and ``app/threads/lifecycle.py`` (closure
     via resolve/abandon) now call ``compute_affect(persist=True)`` so
     HOT-1 sees an affect snapshot timestamped at the event boundary.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ── Parser pins ──────────────────────────────────────────────────────


def _write_trace(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_trace_points_reads_nested_canonical_shape(tmp_path, monkeypatch):
    """The canonical producer shape — nested under "affect" — must
    parse. Pre-fix this returned 0 rows."""
    from app.sentience_experiments import hot1_meta_affect

    trace_path = tmp_path / "trace.jsonl"
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_trace(trace_path, [
        {
            "affect": {
                "valence": 0.6,
                "arousal": 0.3,
                "controllability": 0.5,
                "attractor": "peace",
                "ts": ts,
            },
            "viability": {"total_error": 0.1},
        },
    ])
    monkeypatch.setattr(
        hot1_meta_affect, "_default_trace_path", lambda: trace_path,
    )

    pts = hot1_meta_affect._load_trace_points(window_days=7)
    assert len(pts) == 1
    assert pts[0]["valence"] == 0.6
    assert pts[0]["attractor"] == "peace"


def test_load_trace_points_reads_flat_shape_for_back_compat(tmp_path, monkeypatch):
    """Flat-shape rows (e.g. from older test fixtures) must still parse."""
    from app.sentience_experiments import hot1_meta_affect

    trace_path = tmp_path / "trace.jsonl"
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_trace(trace_path, [
        {
            "valence": 0.4,
            "arousal": 0.7,
            "controllability": 0.2,
            "attractor": "alert",
            "ts": ts,
        },
    ])
    monkeypatch.setattr(
        hot1_meta_affect, "_default_trace_path", lambda: trace_path,
    )

    pts = hot1_meta_affect._load_trace_points(window_days=7)
    assert len(pts) == 1
    assert pts[0]["valence"] == 0.4
    assert pts[0]["attractor"] == "alert"


def test_load_trace_points_window_filter(tmp_path, monkeypatch):
    """Cutoff filtering must still work post-fix."""
    from app.sentience_experiments import hot1_meta_affect

    trace_path = tmp_path / "trace.jsonl"
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _write_trace(trace_path, [
        {"affect": {"ts": recent, "valence": 0.1, "arousal": 0.1, "controllability": 0.1}},
        {"affect": {"ts": old, "valence": 0.9, "arousal": 0.9, "controllability": 0.9}},
    ])
    monkeypatch.setattr(
        hot1_meta_affect, "_default_trace_path", lambda: trace_path,
    )

    pts = hot1_meta_affect._load_trace_points(window_days=7)
    assert len(pts) == 1
    assert pts[0]["valence"] == 0.1


def test_load_trace_points_skips_malformed(tmp_path, monkeypatch):
    """Rows missing ts or with non-numeric V/A/C must skip silently."""
    from app.sentience_experiments import hot1_meta_affect

    trace_path = tmp_path / "trace.jsonl"
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_trace(trace_path, [
        {"affect": {"ts": ts, "valence": 0.5, "arousal": 0.5, "controllability": 0.5}},
        {"affect": {"valence": 0.5}},                # no ts
        {"affect": {"ts": ts, "valence": "bad"}},    # non-numeric
        {"affect": {"ts": "garbage", "valence": 0.5, "arousal": 0.5, "controllability": 0.5}},
    ])
    monkeypatch.setattr(
        hot1_meta_affect, "_default_trace_path", lambda: trace_path,
    )

    pts = hot1_meta_affect._load_trace_points(window_days=7)
    assert len(pts) == 1


# ── Lifecycle-hook pins ──────────────────────────────────────────────


def test_thread_closure_calls_compute_affect(monkeypatch, tmp_path):
    """resolve_thread + abandon_thread must invoke compute_affect so
    HOT-1 sees a snapshot at the event boundary."""
    from app.threads import store, lifecycle
    store.reset_for_tests(tmp_path / "threads")

    calls: list[tuple] = []

    def fake_compute(*args, **kwargs):
        calls.append((args, kwargs))
        from app.affect.schemas import AffectState
        from app.affect.viability import ViabilityFrame
        return AffectState(), ViabilityFrame(values={}, setpoints={}, weights={}, per_variable_error={}, out_of_band=[], total_error=0.0, sources={}, ts="")

    monkeypatch.setattr("app.affect.core.compute_affect", fake_compute)

    t = lifecycle.create_thread(title="probe thread", description="x")
    calls.clear()  # ignore any creation-time hooks

    lifecycle.resolve_thread(t.id, summary="solved by approach X")
    assert calls, "resolve_thread must call compute_affect"
    assert any(kwargs.get("persist") is True for _, kwargs in calls)

    # Abandon a fresh thread (the first is now RESOLVED)
    t2 = lifecycle.create_thread(title="probe thread 2", description="y")
    calls.clear()
    lifecycle.abandon_thread(t2.id, reason="dependency unavailable")
    assert calls, "abandon_thread must call compute_affect"
    assert any(kwargs.get("persist") is True for _, kwargs in calls)


def test_executor_escalate_blocker_calls_compute_affect(monkeypatch, tmp_path):
    """escalate_blocker must emit an affect snapshot. Routed via the
    injectable signal_sender so the test never touches the live
    Signal client."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    calls: list[tuple] = []

    def fake_compute(*args, **kwargs):
        calls.append((args, kwargs))
        from app.affect.schemas import AffectState
        from app.affect.viability import ViabilityFrame
        return AffectState(), ViabilityFrame(values={}, setpoints={}, weights={}, per_variable_error={}, out_of_band=[], total_error=0.0, sources={}, ts="")

    monkeypatch.setattr("app.affect.core.compute_affect", fake_compute)

    from app.autonomous_executor.escalation import escalate_blocker
    escalate_blocker(
        run_id="run-probe-abcdef",
        reason="missing operator input",
        goal_preview="probe goal preview",
        signal_sender=lambda body: {"ts": "1700000000000"},
    )

    assert calls, "escalate_blocker must call compute_affect"
    assert any(kwargs.get("persist") is True for _, kwargs in calls)


def test_blocked_hook_failure_isolated(monkeypatch, tmp_path):
    """A broken compute_affect must NOT propagate into the escalation
    path — the BLOCKED state has already been committed by the caller."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    def broken_compute(*args, **kwargs):
        raise RuntimeError("affect computation hard-failed in probe")

    monkeypatch.setattr("app.affect.core.compute_affect", broken_compute)

    from app.autonomous_executor.escalation import escalate_blocker
    # The call must not raise.
    escalate_blocker(
        run_id="run-probe-abcdef",
        reason="missing operator input",
        signal_sender=lambda body: {"ts": "1700000000000"},
    )
