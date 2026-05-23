"""Tests for app.upgrade_lifecycle.retention (P1#d).

PROGRAM §63 follow-up. Covers:

  1. compact_capability_ledger keeps latest row per to_version
  2. compact_capability_ledger preserves single-row ledger unchanged
  3. compact_capability_ledger rebuilds hash chain correctly
  4. prune_trial_results removes orphan results past retention
  5. prune_trial_results KEEPS results still referenced by capability
  6. prune_trial_results KEEPS young results regardless
  7. cap_pending_queue truncates to max rows
  8. cap_pending_queue no-op under cap
  9. prune_budget_ledgers trims old rows
  10. run_retention_pass composes the four ops
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import retention


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _cap_row(*, package: str, to_version: str,
            extracted_at: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "payload": {
            "package": package, "from_version": "1.0",
            "to_version": to_version, "source": "github_releases",
            "extracted_at": extracted_at,
            "new_features": [], "deprecations": [],
            "breaking_changes": [], "security_fixes": [],
            "perf_notes": [], "notes": "",
            "raw_excerpt_sha256": "",
        },
        "prev_hash": "0" * 64,
        "hash": "x" * 64,   # dummy; compaction rebuilds chain
    }


# ── 1: compact_capability_ledger ────────────────────────────────────────


def test_compact_keeps_latest_per_version(isolated_dir):
    cap_dir = isolated_dir / "capabilities"
    cap_dir.mkdir(parents=True)
    p = cap_dir / "starlette.jsonl"
    _write_jsonl(p, [
        _cap_row(package="starlette", to_version="1.0",
                extracted_at="2025-01-01T00:00:00+00:00"),
        _cap_row(package="starlette", to_version="1.0",   # dup
                extracted_at="2025-06-01T00:00:00+00:00"),
        _cap_row(package="starlette", to_version="2.0",
                extracted_at="2026-01-01T00:00:00+00:00"),
    ])
    result = retention.compact_capability_ledger(p)
    assert result["rows_before"] == 3
    assert result["rows_after"] == 2
    assert result["dropped"] == 1

    rows = retention._read_jsonl_rows(p)
    versions = sorted(r["payload"]["to_version"] for r in rows)
    assert versions == ["1.0", "2.0"]
    # The kept 1.0 row is the LATER extracted one (2025-06-01)
    one_zero = next(r for r in rows if r["payload"]["to_version"] == "1.0")
    assert one_zero["payload"]["extracted_at"] == "2025-06-01T00:00:00+00:00"


def test_compact_singleton_ledger_unchanged(isolated_dir):
    cap_dir = isolated_dir / "capabilities"
    cap_dir.mkdir(parents=True)
    p = cap_dir / "one.jsonl"
    _write_jsonl(p, [_cap_row(package="one", to_version="1.0")])
    result = retention.compact_capability_ledger(p)
    assert result["dropped"] == 0


def test_compact_rebuilds_hash_chain(isolated_dir):
    """After compaction, verify_chain still says OK."""
    cap_dir = isolated_dir / "capabilities"
    cap_dir.mkdir(parents=True)
    p = cap_dir / "rebuild.jsonl"
    _write_jsonl(p, [
        _cap_row(package="rebuild", to_version="1.0",
                extracted_at="2026-01-01T00:00:00+00:00"),
        _cap_row(package="rebuild", to_version="1.0",
                extracted_at="2026-06-01T00:00:00+00:00"),
        _cap_row(package="rebuild", to_version="2.0",
                extracted_at="2026-08-01T00:00:00+00:00"),
    ])
    retention.compact_capability_ledger(p)
    from app.upgrade_lifecycle.changelog_fetcher import verify_chain
    ok, broken = verify_chain("rebuild")
    assert ok is True, f"chain broken at row {broken}"


# ── 4-6: prune_trial_results ───────────────────────────────────────────


def test_prune_removes_orphan_old_trial(isolated_dir):
    trials = isolated_dir / "trials"
    trials.mkdir(parents=True)
    orphan = trials / "ghost__99.0.json"
    orphan.write_text(json.dumps({
        "package": "ghost", "to_version": "99.0", "status": "ok",
    }))
    # Make orphan old enough
    old_ts = time.time() - 400 * 86400   # 400d old
    os.utime(orphan, (old_ts, old_ts))

    result = retention.prune_trial_results()
    assert result["removed"] == 1
    assert not orphan.exists()


def test_prune_keeps_referenced_trial(isolated_dir):
    # Set up a capability row that references the trial's version.
    cap_dir = isolated_dir / "capabilities"
    cap_dir.mkdir(parents=True)
    _write_jsonl(cap_dir / "alpha.jsonl", [
        _cap_row(package="alpha", to_version="2.0"),
    ])

    trials = isolated_dir / "trials"
    trials.mkdir()
    referenced = trials / "alpha__2_0.json"
    referenced.write_text(json.dumps({
        "package": "alpha", "to_version": "2.0", "status": "ok",
    }))
    old_ts = time.time() - 400 * 86400
    os.utime(referenced, (old_ts, old_ts))

    result = retention.prune_trial_results()
    assert result["removed"] == 0
    assert referenced.exists()


def test_prune_keeps_young_orphan(isolated_dir):
    trials = isolated_dir / "trials"
    trials.mkdir(parents=True)
    young = trials / "young__1_0.json"
    young.write_text(json.dumps({
        "package": "young", "to_version": "1.0", "status": "ok",
    }))   # mtime is now — well within retention

    result = retention.prune_trial_results()
    assert result["removed"] == 0
    assert young.exists()


# ── 7-8: cap_pending_queue ─────────────────────────────────────────────


def test_cap_pending_queue_truncates_to_max(isolated_dir, monkeypatch):
    monkeypatch.setattr(retention, "_PENDING_QUEUE_MAX_ROWS", 5)
    trials = isolated_dir / "trials"
    trials.mkdir(parents=True)
    p = trials / "_pending.jsonl"
    with p.open("w") as f:
        for i in range(20):
            f.write(json.dumps({"package": f"p{i}", "to_version": "1.0"}) + "\n")

    result = retention.cap_pending_queue()
    assert result["rotated"] is True
    assert result["rows"] == 5
    assert result["dropped"] == 15

    # Most-recent kept
    remaining = [json.loads(line) for line in p.read_text().splitlines() if line]
    assert remaining[0]["package"] == "p15"
    assert remaining[-1]["package"] == "p19"


def test_cap_pending_queue_no_op_under_cap(isolated_dir):
    trials = isolated_dir / "trials"
    trials.mkdir(parents=True)
    p = trials / "_pending.jsonl"
    p.write_text(json.dumps({"package": "x", "to_version": "1.0"}) + "\n")
    result = retention.cap_pending_queue()
    assert result["rotated"] is False


# ── 9: prune_budget_ledgers ────────────────────────────────────────────


def test_prune_budget_ledgers_trims_old(isolated_dir, monkeypatch):
    monkeypatch.setattr(retention, "_BUDGET_LEDGER_RETAIN_DAYS", 30)
    ledger = isolated_dir / "extraction_budget_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = [
        {"ts": (now - timedelta(days=400)).isoformat(),
         "cost_usd": 0.1, "month": "2025-01"},
        {"ts": (now - timedelta(days=10)).isoformat(),
         "cost_usd": 0.1, "month": now.strftime("%Y-%m")},
    ]
    with ledger.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    result = retention.prune_budget_ledgers(now=now)
    assert result["ledgers"][0]["removed"] == 1
    assert result["ledgers"][0]["kept"] == 1


# ── 10: composite pass ─────────────────────────────────────────────────


def test_run_retention_pass_composes_all_four(isolated_dir):
    """One pass must execute every sub-op and return the shape."""
    out = retention.run_retention_pass()
    assert "compaction" in out
    assert "trial_prune" in out
    assert "pending_cap" in out
    assert "budget_prune" in out
    # State file written
    state_path = retention._state_path()
    assert state_path.exists()
