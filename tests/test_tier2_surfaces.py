"""Tier 2 — combined tests for substrate_radar, latency_slo,
mcp_discovery, recovery.auto_thread.

These five surfaces are independent; each gets a focused
sub-section. Tier 2.5 (trust delegation) is doc-only and has no
test surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pydantic_settings")


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    from app import paths as _paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", workspace)
    return workspace


# ─────────────────────────────────────────────────────────────────────────
#   2.1 substrate_radar
# ─────────────────────────────────────────────────────────────────────────


def _build_dockerfile(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "Dockerfile").write_text(body)


def test_substrate_radar_detects_deb11_eol(tmp_path, monkeypatch):
    from app.substrate_radar import radar

    repo = tmp_path / "repo"
    _build_dockerfile(repo, "FROM python:3.13-bullseye\nRUN echo hi\n")
    findings = radar._detect_base_image_eol(repo)
    # bullseye = debian 11
    assert any(f.subject.startswith("debian:11") for f in findings)


def test_substrate_radar_detects_unpinned_compose_images(tmp_path):
    from app.substrate_radar import radar

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(
        "version: '3'\nservices:\n  pg:\n    image: postgres:15\n"
    )
    findings = radar._detect_compose_issues(repo)
    kinds = {f.kind for f in findings}
    assert "compose_version" in kinds
    assert "unpinned_image" in kinds


def test_substrate_radar_skips_when_master_switch_off(monkeypatch):
    from app.substrate_radar import radar

    monkeypatch.setattr(
        "app.runtime_settings.get_substrate_radar_enabled", lambda: False
    )
    out = radar.run_one_pass()
    assert out["skipped_reason"] == "master_switch_off"


def test_substrate_radar_cloud_api_sunset(tmp_path, monkeypatch):
    from app.substrate_radar import radar

    repo = tmp_path / "repo"
    api_dir = repo / "app" / "substrate_radar"
    api_dir.mkdir(parents=True)
    soon = (datetime.now(timezone.utc) + timedelta(days=60)).date().isoformat()
    (api_dir / "cloud_api_eol.json").write_text(
        json.dumps([
            {"provider": "gcp", "api": "bigquery", "version": "v1",
             "eol_date": soon}
        ])
    )
    findings = radar._detect_cloud_api_sunsets(repo)
    assert len(findings) == 1
    assert findings[0].kind == "cloud_api_sunset"
    assert findings[0].severity.value in ("critical", "high")


# ─────────────────────────────────────────────────────────────────────────
#   2.2 latency_slo
# ─────────────────────────────────────────────────────────────────────────


def _write_audit(workspace: Path, rows: list[dict]) -> None:
    p = workspace / "audit.log"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_latency_slo_returns_empty_with_no_audit(monkeypatch, isolated_workspace):
    from app.healing.monitors import latency_slo

    monkeypatch.setattr(
        "app.runtime_settings.get_latency_slo_monitor_enabled",
        lambda: True,
    )
    latencies = latency_slo._collect_latencies()
    assert latencies == []


def test_latency_slo_pairs_request_response(monkeypatch, isolated_workspace):
    from app.healing.monitors import latency_slo

    monkeypatch.setattr(
        "app.runtime_settings.get_latency_slo_monitor_enabled",
        lambda: True,
    )
    base = datetime.now(timezone.utc)
    rows = []
    for i in range(60):
        t0 = base - timedelta(hours=i)
        t1 = t0 + timedelta(milliseconds=200 + i * 5)
        rows.append({"ts": t0.isoformat(), "event": "request_received", "sender": "andrus"})
        rows.append({"ts": t1.isoformat(), "event": "response_sent", "sender": "andrus"})
    _write_audit(isolated_workspace, rows)
    latencies = latency_slo._collect_latencies()
    assert len(latencies) == 60
    # First few are smallest, gradually growing
    assert min(latencies) < max(latencies)


def test_latency_slo_first_run_establishes_baseline(monkeypatch, isolated_workspace):
    from app.healing.monitors import latency_slo

    monkeypatch.setattr(
        "app.runtime_settings.get_latency_slo_monitor_enabled",
        lambda: True,
    )
    base = datetime.now(timezone.utc)
    rows = []
    for i in range(60):
        t0 = base - timedelta(hours=i)
        t1 = t0 + timedelta(milliseconds=200)
        rows.append({"ts": t0.isoformat(), "event": "request_received", "sender": "a"})
        rows.append({"ts": t1.isoformat(), "event": "response_sent", "sender": "a"})
    _write_audit(isolated_workspace, rows)
    out = latency_slo.run()
    assert out.get("established_baseline") is True


def test_latency_slo_alerts_on_drift(monkeypatch, isolated_workspace):
    from app.healing.monitors import latency_slo

    monkeypatch.setattr(
        "app.runtime_settings.get_latency_slo_monitor_enabled",
        lambda: True,
    )
    # Pre-write a baseline
    baseline = {"n": 100.0, "p50": 100.0, "p95": 200.0, "p99": 300.0, "mean": 150.0}
    bp = isolated_workspace / "healing" / "latency_slo_baseline.json"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(json.dumps(baseline))

    base = datetime.now(timezone.utc)
    rows = []
    for i in range(60):
        t0 = base - timedelta(hours=i)
        t1 = t0 + timedelta(milliseconds=600)  # 2× baseline p99
        rows.append({"ts": t0.isoformat(), "event": "request_received", "sender": "a"})
        rows.append({"ts": t1.isoformat(), "event": "response_sent", "sender": "a"})
    _write_audit(isolated_workspace, rows)

    alerts = []
    monkeypatch.setattr(
        "app.healing.monitors.latency_slo._alert",
        lambda cur, base, delta: alerts.append((cur, base, delta)) or True,
    )
    out = latency_slo.run()
    assert out.get("alerted") is True
    assert len(alerts) == 1


# ─────────────────────────────────────────────────────────────────────────
#   2.3 mcp_discovery
# ─────────────────────────────────────────────────────────────────────────


def test_mcp_discovery_master_switch_off(monkeypatch):
    from app.mcp_discovery.poller import run_discovery_pass

    monkeypatch.setattr(
        "app.runtime_settings.get_mcp_discovery_enabled", lambda: False
    )
    out = run_discovery_pass()
    assert out["skipped_reason"] == "master_switch_off"


def test_mcp_discovery_filters_low_quality(monkeypatch, isolated_workspace):
    from app.mcp_discovery.poller import run_discovery_pass

    monkeypatch.setattr(
        "app.runtime_settings.get_mcp_discovery_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.mcp_discovery.poller._stage_candidate", lambda c: True
    )
    # 3 entries — only 1 passes filters
    entries = [
        {"name": "good", "rating": 4.5, "install_count": 200,
         "publisher": "acme", "description": "x"},
        {"name": "low_rating", "rating": 3.0, "install_count": 1000,
         "publisher": "x", "description": "x"},
        {"name": "low_installs", "rating": 5.0, "install_count": 5,
         "publisher": "x", "description": "x"},
    ]
    out = run_discovery_pass(fetcher=lambda: entries)
    assert out["n_raw_entries"] == 3
    assert out["n_candidates_after_filter"] == 1
    assert out["n_staged"] == 1


def test_mcp_discovery_honors_denylist(monkeypatch, isolated_workspace):
    from app.mcp_discovery.poller import run_discovery_pass

    monkeypatch.setattr(
        "app.runtime_settings.get_mcp_discovery_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.mcp_discovery.poller._stage_candidate", lambda c: True
    )
    denylist = isolated_workspace / "mcp_discovery" / "denylist.txt"
    denylist.parent.mkdir(parents=True, exist_ok=True)
    denylist.write_text("acme/blocked\n")
    entries = [
        {"name": "blocked", "namespace": "acme",
         "rating": 5.0, "install_count": 500, "description": "x"},
        {"name": "allowed", "rating": 5.0, "install_count": 500,
         "description": "x"},
    ]
    out = run_discovery_pass(fetcher=lambda: entries)
    assert out["n_candidates_after_filter"] == 1
    assert out["staged_signatures"] == ["allowed"]


def test_mcp_discovery_rate_limit(monkeypatch, isolated_workspace):
    from app.mcp_discovery.poller import run_discovery_pass

    monkeypatch.setattr(
        "app.runtime_settings.get_mcp_discovery_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.mcp_discovery.poller._stage_candidate", lambda c: True
    )
    entries = [
        {"name": f"c{i}", "rating": 5.0, "install_count": 500,
         "description": "x"}
        for i in range(10)
    ]
    out = run_discovery_pass(fetcher=lambda: entries)
    assert out["n_staged"] <= 3  # _MAX_CANDIDATES_PER_PASS


# ─────────────────────────────────────────────────────────────────────────
#   2.4 recovery.auto_thread
# ─────────────────────────────────────────────────────────────────────────


def test_auto_thread_switch_off(monkeypatch):
    from app.recovery import auto_thread

    monkeypatch.setattr(
        "app.runtime_settings.get_recovery_auto_thread_enabled", lambda: False
    )
    out = auto_thread.maybe_open_thread(question="why does x happen?")
    assert out == {"opened": False, "reason": "switch_off"}


def test_auto_thread_too_short(monkeypatch):
    from app.recovery import auto_thread

    monkeypatch.setattr(
        "app.runtime_settings.get_recovery_auto_thread_enabled", lambda: True
    )
    out = auto_thread.maybe_open_thread(question="why?")
    assert out["reason"] == "too_short"


def test_auto_thread_dedup_against_open(monkeypatch):
    from app.recovery import auto_thread

    monkeypatch.setattr(
        "app.runtime_settings.get_recovery_auto_thread_enabled", lambda: True
    )
    existing = MagicMock()
    existing.title = "Hard question: how do we make X work better?"
    monkeypatch.setattr(auto_thread, "_list_open_threads", lambda: [existing])
    out = auto_thread.maybe_open_thread(
        question="how do we make X work better in production?"
    )
    assert out["reason"] == "dedup"


def test_auto_thread_creates_when_eligible(monkeypatch):
    from app.recovery import auto_thread

    monkeypatch.setattr(
        "app.runtime_settings.get_recovery_auto_thread_enabled", lambda: True
    )
    monkeypatch.setattr(auto_thread, "_list_open_threads", lambda: [])
    monkeypatch.setattr(auto_thread, "_count_recent_auto_threads", lambda: 0)
    created = MagicMock()
    created.id = "thread-xyz"
    monkeypatch.setattr(
        "app.threads.lifecycle.create_thread",
        lambda title, description: created,
    )
    out = auto_thread.maybe_open_thread(
        question="why is the substrate migration slow at scale?",
        failure_summary="recovery loop exhausted 6 strategies",
        triggering_request_id="req-123",
    )
    assert out["opened"] is True
    assert out["thread_id"] == "thread-xyz"


# ─────────────────────────────────────────────────────────────────────────
#   Runtime settings defaults
# ─────────────────────────────────────────────────────────────────────────


def test_master_switch_defaults():
    from app import runtime_settings

    assert runtime_settings.get_substrate_radar_enabled() is True
    assert runtime_settings.get_latency_slo_monitor_enabled() is True
    # MCP discovery + auto_thread default OFF — security/UX-sensitive
    assert runtime_settings.get_mcp_discovery_enabled() is False
    assert runtime_settings.get_recovery_auto_thread_enabled() is False


def test_trust_delegation_doc_exists():
    """Tier 2.5 is doc-only; the scoping doc must exist."""
    repo = Path(__file__).resolve().parents[1]
    doc = repo / "docs" / "TRUST_DELEGATION.md"
    assert doc.exists()
    body = doc.read_text()
    assert "DEFER" in body or "Defer" in body
    assert "Operator decision required" in body
