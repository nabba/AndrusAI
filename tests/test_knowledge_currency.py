"""Tests for app.healing.monitors.knowledge_currency — Gap #10 KB
stagnation detector."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.healing.monitors import knowledge_currency as kc  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(kc, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(kc, "_enabled", lambda: True)
    return tmp_path


def _write_ledger(workspace: Path, kb: str, tss: list[float]) -> None:
    path = workspace / kb / ".source_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ts in tss:
            f.write(json.dumps({"ts": ts, "doc_id": f"d_{ts}"}) + "\n")


def test_empty_kb_not_stagnant(_tmp_workspace: Path) -> None:
    result = kc.compute(now=1_700_000_000.0)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    assert by_kb["memory"]["n_rows"] == 0
    assert by_kb["memory"]["is_stagnant"] is False
    assert result["stagnant_kbs"] == []


def test_fresh_kb_not_stagnant(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    # All rows within last 30 days.
    _write_ledger(_tmp_workspace, "episteme", [now - 86400 * i for i in range(20)])
    result = kc.compute(now=now)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    assert by_kb["episteme"]["n_rows"] == 20
    assert by_kb["episteme"]["is_stagnant"] is False


def test_stagnant_kb_flagged(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    # 20 rows all ≥2 years old; last add was 8 months ago.
    old_base = now - 730 * 86400
    last_add = now - 240 * 86400
    tss = [old_base + 86400 * i for i in range(19)] + [last_add]
    _write_ledger(_tmp_workspace, "philosophy", tss)
    result = kc.compute(now=now)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    assert by_kb["philosophy"]["is_stagnant"] is True
    assert "philosophy" in result["stagnant_kbs"]


def test_below_min_rows_not_stagnant(_tmp_workspace: Path) -> None:
    """Even very old + never-touched KBs with <10 rows aren't flagged
    as stagnant — they may be intentional sparse references."""
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    old_base = now - 730 * 86400
    _write_ledger(_tmp_workspace, "aesthetics", [old_base + 86400 * i for i in range(5)])
    result = kc.compute(now=now)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    assert by_kb["aesthetics"]["is_stagnant"] is False


def test_recent_add_disqualifies_stagnation(_tmp_workspace: Path) -> None:
    """An old corpus that still gets new rows (within 6 months) is
    fresh enough."""
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    old_base = now - 730 * 86400
    recent_add = now - 90 * 86400  # 3 months ago
    tss = [old_base + 86400 * i for i in range(19)] + [recent_add]
    _write_ledger(_tmp_workspace, "experiential", tss)
    result = kc.compute(now=now)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    assert by_kb["experiential"]["is_stagnant"] is False


def test_briefing_section_lists_stagnant_kbs(_tmp_workspace: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    old_base = now - 730 * 86400
    last_add = now - 240 * 86400
    tss = [old_base + 86400 * i for i in range(19)] + [last_add]
    _write_ledger(_tmp_workspace, "philosophy", tss)
    snippet = kc.briefing_section()
    assert "philosophy" in snippet
    assert "Stagnant" in snippet


def test_briefing_section_empty_when_nothing_stagnant(_tmp_workspace: Path) -> None:
    assert kc.briefing_section() == ""


def test_run_alert_dedup(_tmp_workspace: Path, monkeypatch) -> None:
    """Same stagnant set within dedup window suppresses second alert."""
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    old_base = now - 730 * 86400
    last_add = now - 240 * 86400
    tss = [old_base + 86400 * i for i in range(19)] + [last_add]
    _write_ledger(_tmp_workspace, "tensions", tss)

    sent: list[dict] = []
    monkeypatch.setattr("app.notify.notify", lambda **kw: sent.append(kw))

    r1 = kc.run(now=now)
    r2 = kc.run(now=now + kc._INTERNAL_CADENCE_S + 60)  # past internal cadence, inside dedup
    assert r1["alert_sent"] is True
    assert r2["alert_sent"] is False
    assert len(sent) == 1


def test_run_skips_internal_cadence(_tmp_workspace: Path) -> None:
    kc.run(now=1_000_000.0)
    second = kc.run(now=1_000_000.0 + 60)
    assert second["ran"] is False


def test_run_skips_when_master_off(monkeypatch) -> None:
    monkeypatch.setattr(kc, "_enabled", lambda: False)
    assert kc.run() == {"ran": False, "skipped": True}


def test_compute_distribution_percentiles(_tmp_workspace: Path) -> None:
    now = 1_700_000_000.0
    # 11 rows spread across 1100 days (every 100 days).
    tss = [now - 100 * 86400 * i for i in range(11)]
    _write_ledger(_tmp_workspace, "knowledge", tss)
    result = kc.compute(now=now)
    by_kb = {row["kb"]: row for row in result["kbs"]}
    row = by_kb["knowledge"]
    assert row["n_rows"] == 11
    # Median age is 500 days (the middle of [0, 100, ..., 1000] is 500).
    assert row["median_age_days"] == pytest.approx(500.0)
