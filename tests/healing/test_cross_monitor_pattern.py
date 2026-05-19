"""Tests for app.healing.monitors.cross_monitor_pattern.

Verifies the cluster detection, dedup, and end-to-end run flow.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.healing.monitors import cross_monitor_pattern as cmp
    monkeypatch.setattr(cmp, "_workspace_root", lambda: tmp_path)
    return tmp_path


@dataclass(frozen=True)
class FakeEvent:
    """Mimics IdentityEvent shape — kind, actor, detail."""
    kind: str
    actor: str
    detail: dict[str, Any] = field(default_factory=dict)
    ts: str = "2026-05-19T00:00:00+00:00"


# ── _extract_path ──────────────────────────────────────────────────────


def test_extract_path_picks_first_present_key():
    from app.healing.monitors import cross_monitor_pattern as cmp
    assert cmp._extract_path({"path": "app/x.py"}) == "app/x.py"
    assert cmp._extract_path({"filepath": "app/y.py"}) == "app/y.py"
    assert cmp._extract_path({"file": "app/z.py"}) == "app/z.py"
    # path takes precedence over filepath
    assert cmp._extract_path({"path": "a", "filepath": "b"}) == "a"


def test_extract_path_returns_empty_when_no_path():
    from app.healing.monitors import cross_monitor_pattern as cmp
    assert cmp._extract_path({}) == ""
    assert cmp._extract_path({"path": ""}) == ""
    assert cmp._extract_path({"path": 42}) == ""


# ── cluster detection ─────────────────────────────────────────────────


def test_cluster_events_groups_by_path_then_kind():
    from app.healing.monitors import cross_monitor_pattern as cmp
    events = [
        FakeEvent("architectural_debt_drift", "elegance_drift", {"path": "app/foo.py"}),
        FakeEvent("architectural_debt_drift", "architectural_drift", {"path": "app/foo.py"}),
        FakeEvent("tz_drift", "tz_drift_monitor", {"path": "app/foo.py"}),
        FakeEvent("tz_drift", "tz_drift_monitor", {"path": "app/bar.py"}),
    ]
    clusters = cmp._cluster_events(events)
    assert set(clusters.keys()) == {"app/foo.py", "app/bar.py"}
    assert set(clusters["app/foo.py"].keys()) == {"architectural_debt_drift", "tz_drift"}


def test_cluster_events_skips_pathless_events():
    from app.healing.monitors import cross_monitor_pattern as cmp
    events = [
        FakeEvent("kind1", "a1", {"summary": "no path here"}),
        FakeEvent("kind2", "a2", {"path": "app/x.py"}),
    ]
    clusters = cmp._cluster_events(events)
    assert set(clusters.keys()) == {"app/x.py"}


def test_convergent_clusters_threshold():
    from app.healing.monitors import cross_monitor_pattern as cmp
    # Path with only 2 kinds — below threshold.
    raw = {
        "app/a.py": {"k1": ["actor1"], "k2": ["actor2"]},
        # Path with 3 distinct kinds — at threshold.
        "app/b.py": {"k1": ["a"], "k2": ["b"], "k3": ["c"]},
        # Path with 4 kinds.
        "app/c.py": {"k1": ["a"], "k2": ["b"], "k3": ["c"], "k4": ["d"]},
    }
    out = cmp._convergent_clusters(raw)
    paths = [c["path"] for c in out]
    assert "app/a.py" not in paths  # below threshold
    assert "app/b.py" in paths
    assert "app/c.py" in paths
    # Sorted by event count desc → c first
    assert paths[0] == "app/c.py"


# ── fingerprint + dedup ────────────────────────────────────────────────


def test_fingerprint_includes_path_and_sorted_kinds():
    from app.healing.monitors import cross_monitor_pattern as cmp
    cluster = {"path": "app/x.py", "kinds": ["a", "b", "c"]}
    assert cmp._fingerprint(cluster) == "app/x.py|a,b,c"


def test_fresh_returns_true_when_no_prior():
    from app.healing.monitors import cross_monitor_pattern as cmp
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    assert cmp._is_fresh("anything", {}, now) is True


def test_fresh_returns_false_within_dedup_window():
    from app.healing.monitors import cross_monitor_pattern as cmp
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    state = {"fp1": {"last_alerted_at": "2026-05-10T00:00:00+00:00"}}
    # 9 days ago, well inside the 30-day window.
    assert cmp._is_fresh("fp1", state, now) is False


def test_fresh_returns_true_outside_dedup_window():
    from app.healing.monitors import cross_monitor_pattern as cmp
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    state = {"fp1": {"last_alerted_at": "2026-03-01T00:00:00+00:00"}}
    # 79 days ago, beyond 30-day window.
    assert cmp._is_fresh("fp1", state, now) is True


# ── run() ──────────────────────────────────────────────────────────────


def test_run_disabled_short_circuits(isolated_workspace, monkeypatch):
    from app.healing.monitors import cross_monitor_pattern as cmp
    monkeypatch.setattr(cmp, "_enabled", lambda: False)
    result = cmp.run()
    assert result["disabled"] is True


def test_run_skipped_when_cadence_not_due(isolated_workspace, monkeypatch):
    from app.healing.monitors import cross_monitor_pattern as cmp
    state_path = isolated_workspace / "healing" / "cross_monitor_pattern_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_run": time.time(), "fingerprints": {}}))
    result = cmp.run()
    assert result.get("skipped_cadence") is True


def test_run_alerts_on_first_convergent_cluster(isolated_workspace, monkeypatch):
    """Seed ledger with 3 distinct kinds on the same path → alert."""
    from app.healing.monitors import cross_monitor_pattern as cmp

    fake_events = [
        FakeEvent("architectural_debt_drift", "elegance_drift", {"path": "app/foo.py"}),
        FakeEvent("tz_drift", "tz_drift_monitor", {"path": "app/foo.py"}),
        FakeEvent("feedback_loop_drift", "feedback_loop_drift", {"path": "app/foo.py"}),
    ]
    monkeypatch.setattr(
        "app.identity.continuity_ledger.list_events",
        lambda **kw: fake_events,
    )

    emitted: list = []
    monkeypatch.setattr(cmp, "_emit_alert", lambda clusters: emitted.append(clusters))

    result = cmp.run()
    assert result["checked"] is True
    assert result["n_clusters"] == 1
    assert result["n_alerted"] == 1
    assert len(emitted) == 1
    assert emitted[0][0]["path"] == "app/foo.py"


def test_run_dedups_known_fingerprint_on_second_pass(isolated_workspace, monkeypatch):
    from app.healing.monitors import cross_monitor_pattern as cmp

    fake_events = [
        FakeEvent("architectural_debt_drift", "elegance_drift", {"path": "app/x.py"}),
        FakeEvent("tz_drift", "tz_drift_monitor", {"path": "app/x.py"}),
        FakeEvent("feedback_loop_drift", "feedback_loop_drift", {"path": "app/x.py"}),
    ]
    monkeypatch.setattr(
        "app.identity.continuity_ledger.list_events",
        lambda **kw: fake_events,
    )

    emitted: list = []
    monkeypatch.setattr(cmp, "_emit_alert", lambda clusters: emitted.append(clusters))

    # First pass — alerts.
    first = cmp.run()
    assert first["n_alerted"] == 1

    # Reset cadence to force a second pass.
    state_path = isolated_workspace / "healing" / "cross_monitor_pattern_state.json"
    state = json.loads(state_path.read_text())
    state["last_run"] = 0
    state_path.write_text(json.dumps(state))

    # Second pass — same cluster, should be deduped.
    second = cmp.run()
    assert second["n_clusters"] == 1
    assert second["n_alerted"] == 0
    assert len(emitted) == 1  # still just the first alert


def test_detect_convergent_clusters_pure(monkeypatch):
    """detect_convergent_clusters is pure read-only — usable from
    diagnostics without touching state."""
    from app.healing.monitors import cross_monitor_pattern as cmp
    fake_events = [
        FakeEvent("k1", "a1", {"path": "app/x.py"}),
        FakeEvent("k2", "a2", {"path": "app/x.py"}),
        FakeEvent("k3", "a3", {"path": "app/x.py"}),
    ]
    monkeypatch.setattr(
        "app.identity.continuity_ledger.list_events",
        lambda **kw: fake_events,
    )
    out = cmp.detect_convergent_clusters()
    assert len(out) == 1
    assert out[0]["path"] == "app/x.py"
    assert set(out[0]["kinds"]) == {"k1", "k2", "k3"}
