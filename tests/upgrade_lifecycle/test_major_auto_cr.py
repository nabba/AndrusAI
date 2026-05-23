"""Tests for app.upgrade_lifecycle.major_auto_cr (U4).

PROGRAM §62. Covers the five-condition gate + CR composition:

  1.  Gate passes when all 5 conditions hold
  2.  Master switch OFF blocks
  3.  Framework exclusion blocks (crewai/chromadb/fastapi/...)
  4.  Post-release window <30d blocks
  5.  PyPI metadata missing blocks
  6.  Unparseable upload_time blocks
  7.  Impact analysis breaking_hits>0 blocks
  8.  Impact analysis tier_immutable_touched blocks
  9.  Impact analysis missing blocks
  10. Trial not run blocks
  11. Trial test_failure blocks
  12. CR body composition includes capability details
  13. CR body composition includes impact summary + top-10 sites
  14. file_major_auto_cr stages on pass, doesn't stage on fail
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.upgrade_lifecycle import major_auto_cr as mac
from app.upgrade_lifecycle.protocol import (
    CallSite,
    Capability,
    ImpactReport,
    TrialResult,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime(2026, 5, 23, tzinfo=timezone.utc)


def _pypi_metadata_for(version: str, *, released_days_ago: int = 60) -> dict:
    upload_time = (_now() - timedelta(days=released_days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S",
    )
    return {
        "info": {"version": version},
        "releases": {
            version: [{"upload_time": upload_time}],
        },
    }


def _passing_capability() -> Capability:
    return Capability(
        package="somelib",
        from_version="1.0.0",
        to_version="2.0.0",
        source="github_releases",
        extracted_at="2026-05-23T00:00:00+00:00",
        new_features=("Async support added",),
    )


def _passing_impact() -> ImpactReport:
    return ImpactReport(
        package="somelib",
        from_version="1.0.0",
        to_version="2.0.0",
        breaking_hits=0,
        deprecation_hits=2,
        tier_immutable_touched=False,
        call_sites=[
            CallSite(
                file_path="app/user.py", line=12, symbol="somelib.foo",
                kind="from_import", matched_capability="foo",
            ),
        ],
    )


def _passing_trial() -> TrialResult:
    return TrialResult(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        status="ok", pass_count=42, fail_count=0,
    )


# ── 1: Happy-path gate pass ─────────────────────────────────────────────


def test_gate_passes_when_all_five_conditions_hold():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        capability=_passing_capability(),
        impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0.0"),
        now=_now(),
        master_switch_override=True,
    )
    assert outcome.passed is True
    assert outcome.reason == "ok"


# ── 2: Master switch OFF ────────────────────────────────────────────────


def test_gate_blocks_when_master_switch_off():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0.0"),
        now=_now(),
        master_switch_override=False,
    )
    assert outcome.passed is False
    assert outcome.reason == "master_switch_off"


# ── 3: Framework exclusion ──────────────────────────────────────────────


def test_gate_blocks_framework_packages():
    for pkg in ("crewai", "chromadb", "fastapi", "pydantic", "starlette", "anthropic"):
        outcome = mac.evaluate_gate(
            package=pkg, from_version="1.0", to_version="2.0",
            capability=_passing_capability(),
            impact=_passing_impact(),
            trial=_passing_trial(),
            pypi_metadata=_pypi_metadata_for("2.0"),
            now=_now(), master_switch_override=True,
        )
        assert outcome.passed is False, f"{pkg} should be blocked"
        assert outcome.reason.startswith("framework_exclusion")


def test_gate_normalizes_underscore_in_framework_check():
    """pydantic_settings has an underscore but the framework set uses hyphen."""
    outcome = mac.evaluate_gate(
        package="pydantic_settings", from_version="1.0", to_version="2.0",
        capability=_passing_capability(),
        impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert "pydantic-settings" in outcome.reason


# ── 4-6: Post-release window ────────────────────────────────────────────


def test_gate_blocks_under_30_days():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0", released_days_ago=10),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert "post_release_too_short" in outcome.reason


def test_gate_blocks_when_pypi_metadata_missing():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=None,
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert outcome.reason == "no_pypi_metadata"


def test_gate_blocks_when_version_not_in_releases_dict():
    md = {"info": {"version": "2.0"}, "releases": {"1.0": []}}
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=md,
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert "version_not_in_pypi_releases" in outcome.reason


def test_gate_blocks_when_upload_time_unparseable():
    md = {
        "releases": {
            "2.0": [{"upload_time": "this is not a date"}],
        },
    }
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=md,
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert outcome.reason == "upload_time_unparseable"


# ── 7-9: Impact analysis ────────────────────────────────────────────────


def test_gate_blocks_when_breaking_hits_present():
    impact = _passing_impact()
    impact.breaking_hits = 3
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=impact,
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert "breaking_hits:3" == outcome.reason


def test_gate_blocks_when_tier_immutable_touched():
    impact = _passing_impact()
    impact.tier_immutable_touched = True
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=impact,
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert outcome.reason == "tier_immutable_touched"


def test_gate_blocks_when_impact_missing():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=None,
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert outcome.reason == "impact_not_run"


# ── 10-11: Trial ────────────────────────────────────────────────────────


def test_gate_blocks_when_trial_missing():
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=None,
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert outcome.reason == "trial_not_run"


def test_gate_blocks_when_trial_failed():
    trial = _passing_trial()
    trial.status = "test_failure"
    outcome = mac.evaluate_gate(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=trial,
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(), master_switch_override=True,
    )
    assert outcome.passed is False
    assert "trial_status:test_failure" == outcome.reason


# ── 12-13: CR body composition ──────────────────────────────────────────


def test_cr_body_includes_capability_sections():
    cap = _passing_capability()
    body = mac.compose_cr_body(
        package=cap.package, from_version=cap.from_version, to_version=cap.to_version,
        capability=cap, impact=_passing_impact(), trial=_passing_trial(),
        gate=mac.GateOutcome(passed=True, reason="ok"),
        days_since_release=60,
    )
    assert "Upgrade `somelib`" in body
    assert "New features" in body
    assert "Async support added" in body


def test_cr_body_includes_impact_summary_and_top_sites():
    impact = _passing_impact()
    # Add 15 call sites so we can verify the "...and N more" line
    for i in range(14):
        impact.call_sites.append(CallSite(
            file_path=f"app/use_{i}.py", line=i + 1, symbol="somelib.foo",
            kind="from_import", matched_capability="foo",
        ))
    body = mac.compose_cr_body(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=impact, trial=_passing_trial(),
        gate=mac.GateOutcome(passed=True, reason="ok"),
        days_since_release=60,
    )
    assert "Impact" in body
    assert "and 5 more" in body   # 15 - 10 shown
    assert "app/use_0.py:1" in body


# ── 14: file_major_auto_cr orchestration ────────────────────────────────


def test_file_major_auto_cr_stages_on_pass():
    staged_calls: list[dict] = []

    def _fake_stage(**kw):
        staged_calls.append(kw)

    outcome = mac.file_major_auto_cr(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        capability=_passing_capability(),
        impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0.0"),
        now=_now(),
        stage_fn=_fake_stage,
    )
    assert outcome is not None
    assert outcome.passed is True
    assert len(staged_calls) == 1
    call = staged_calls[0]
    assert call["source"] == "dependency_radar"
    assert "major_auto_somelib" in call["signature"]
    assert call["target_path"] == "requirements.txt"
    assert call["cooldown_days"] == 14
    assert "Upgrade somelib" in call["title"]


def test_file_major_auto_cr_skips_on_fail():
    staged_calls: list[dict] = []

    def _fake_stage(**kw):
        staged_calls.append(kw)

    outcome = mac.file_major_auto_cr(
        package="crewai",   # framework_exclusion → fail
        from_version="0.1", to_version="1.0",
        capability=_passing_capability(),
        impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("1.0"),
        now=_now(),
        stage_fn=_fake_stage,
    )
    assert outcome is not None
    assert outcome.passed is False
    assert len(staged_calls) == 0   # no CR filed


def test_file_major_auto_cr_handles_stage_exception_gracefully():
    def _exploding_stage(**kw):
        raise RuntimeError("simulated stage failure")

    outcome = mac.file_major_auto_cr(
        package="somelib", from_version="1.0", to_version="2.0",
        capability=_passing_capability(), impact=_passing_impact(),
        trial=_passing_trial(),
        pypi_metadata=_pypi_metadata_for("2.0"),
        now=_now(),
        stage_fn=_exploding_stage,
    )
    # Gate passed but stage exploded — returns None (caller falls back to Signal).
    assert outcome is None
