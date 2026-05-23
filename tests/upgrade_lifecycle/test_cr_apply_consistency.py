"""Tests for app.healing.monitors.cr_apply_consistency (B3-P2)."""
from __future__ import annotations

import json
import pytest

# Healing __init__ pulls app.config → pydantic_settings.
pytest.importorskip("pydantic_settings")

from app.healing.monitors import cr_apply_consistency as cac   # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    state_dir = tmp_path / "healing"
    state_dir.mkdir()
    monkeypatch.setattr(
        cac, "_state_path", lambda: state_dir / "consistency_state.json",
    )
    return tmp_path


# ── _iter_recent_applied_docs_crs ──────────────────────────────────────


def test_iter_filters_to_docs_proposed_upgrades(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text("\n".join(json.dumps(r) for r in [
        {"cr_id": "cr-1", "path": "docs/proposed_upgrades/x.md",
         "status": "applied", "ts": "2026-05-23T00:00:00+00:00"},
        {"cr_id": "cr-2", "path": "app/user.py",
         "status": "applied", "ts": "2026-05-23T01:00:00+00:00"},
        {"cr_id": "cr-3", "path": "docs/proposed_upgrades/y.md",
         "status": "applied", "ts": "2026-05-23T02:00:00+00:00"},
    ]) + "\n")

    rows = list(cac._iter_recent_applied_docs_crs(audit))
    cr_ids = {r[0] for r in rows}
    assert cr_ids == {"cr-1", "cr-3"}


def test_iter_filters_to_applied_status_only(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text("\n".join(json.dumps(r) for r in [
        {"cr_id": "cr-1", "path": "docs/proposed_upgrades/x.md",
         "status": "applied", "ts": "2026-05-23T00:00:00+00:00"},
        {"cr_id": "cr-2", "path": "docs/proposed_upgrades/y.md",
         "status": "rejected", "ts": "2026-05-23T01:00:00+00:00"},
        {"cr_id": "cr-3", "path": "docs/proposed_upgrades/z.md",
         "status": "pending", "ts": "2026-05-23T02:00:00+00:00"},
    ]) + "\n")
    rows = list(cac._iter_recent_applied_docs_crs(audit))
    assert {r[0] for r in rows} == {"cr-1"}


def test_iter_collapses_per_cr_to_latest_status(tmp_path):
    """Two rows for same cr_id — only the latest status matters."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text("\n".join(json.dumps(r) for r in [
        {"cr_id": "cr-x", "path": "docs/proposed_upgrades/a.md",
         "status": "pending", "ts": "2026-05-23T00:00:00+00:00"},
        {"cr_id": "cr-x", "path": "docs/proposed_upgrades/a.md",
         "status": "applied", "ts": "2026-05-23T01:00:00+00:00"},
    ]) + "\n")
    rows = list(cac._iter_recent_applied_docs_crs(audit))
    assert len(rows) == 1
    assert rows[0][0] == "cr-x"


def test_iter_caps_at_limit(tmp_path):
    """Returns at most _MAX_RECENT_APPLIED newest matches."""
    audit = tmp_path / "audit.jsonl"
    rows = []
    for i in range(60):
        rows.append({
            "cr_id": f"cr-{i:03d}",
            "path": f"docs/proposed_upgrades/file_{i}.md",
            "status": "applied",
            "ts": f"2026-05-{i % 28 + 1:02d}T00:00:00+00:00",
        })
    audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = list(cac._iter_recent_applied_docs_crs(audit, limit=10))
    assert len(out) == 10


# ── run() integration ─────────────────────────────────────────────────


def test_run_alerts_when_file_missing(isolated, tmp_path, monkeypatch):
    """Audit says APPLIED but file doesn't exist on disk → alert."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({
        "cr_id": "cr-missing",
        "path": "docs/proposed_upgrades/missing.md",
        "status": "applied",
        "ts": "2026-05-23T00:00:00+00:00",
    }) + "\n")
    monkeypatch.setattr(cac, "_enabled", lambda: True)
    monkeypatch.setattr(cac, "_audit_path", lambda: audit)
    monkeypatch.setattr(cac, "_repo_root", lambda: tmp_path)

    notified = []
    monkeypatch.setattr(cac, "_notify", lambda alerts: notified.append(alerts))
    cac.run()
    assert len(notified) == 1
    assert any("cr-missing" in a for a in notified[0])


def test_run_silent_when_file_exists(isolated, tmp_path, monkeypatch):
    """File present on disk → no alert."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({
        "cr_id": "cr-present",
        "path": "docs/proposed_upgrades/present.md",
        "status": "applied",
        "ts": "2026-05-23T00:00:00+00:00",
    }) + "\n")
    # Materialise the file at the expected location.
    (tmp_path / "docs" / "proposed_upgrades").mkdir(parents=True)
    (tmp_path / "docs" / "proposed_upgrades" / "present.md").write_text("body")

    monkeypatch.setattr(cac, "_enabled", lambda: True)
    monkeypatch.setattr(cac, "_audit_path", lambda: audit)
    monkeypatch.setattr(cac, "_repo_root", lambda: tmp_path)

    notified = []
    monkeypatch.setattr(cac, "_notify", lambda alerts: notified.append(alerts))
    cac.run()
    assert notified == []


def test_run_dedups_within_week(isolated, tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({
        "cr_id": "cr-x",
        "path": "docs/proposed_upgrades/missing.md",
        "status": "applied",
        "ts": "2026-05-23T00:00:00+00:00",
    }) + "\n")
    monkeypatch.setattr(cac, "_enabled", lambda: True)
    monkeypatch.setattr(cac, "_audit_path", lambda: audit)
    monkeypatch.setattr(cac, "_repo_root", lambda: tmp_path)

    notified = []
    monkeypatch.setattr(cac, "_notify", lambda alerts: notified.append(alerts))
    cac.run()
    cac.run()
    # Internal weekly cadence → second run is a no-op.
    assert len(notified) == 1


def test_run_master_switch_off(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(cac, "_enabled", lambda: False)
    notified = []
    monkeypatch.setattr(cac, "_notify", lambda alerts: notified.append(alerts))
    cac.run()
    assert notified == []
