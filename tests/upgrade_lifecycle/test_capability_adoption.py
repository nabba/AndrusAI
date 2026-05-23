"""Tests for app.upgrade_lifecycle.capability_adoption (U5).

PROGRAM §62. Covers the four-gate orchestrator:

  1.  Master switch OFF returns immediately
  2.  Rate limit hard cap (1 CR / ISO week)
  3.  Budget exhausted blocks LLM calls
  4.  Quarterly budget rollover restores capacity
  5.  Calendar-quarter key generation across months
  6.  Dedup against open architecture-requests
  7.  Framework package skipped (handled by annual snapshot)
  8.  No capabilities to walk → no_capabilities reason
  9.  LLM declines (should_refactor=false) → no CR
  10. LLM low-confidence → no CR
  11. LLM passes → CR filed, rate counter bumped, budget recorded
  12. Refused TIER_IMMUTABLE — stage_fn never called twice for same path
  13. Signature is deterministic for same inputs
  14. Budget ledger row is appended on every attempt (success and failure)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import capability_adoption as ca
from app.upgrade_lifecycle.protocol import Capability


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(ca, "_enabled", lambda: True)
    monkeypatch.setattr(ca, "_quarterly_budget_usd", lambda: 20.0)


@pytest.fixture
def fake_repo(tmp_path):
    """A minimal repo with one app file containing the feature symbol."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "user.py").write_text(
        "import asyncio\n"
        "async def fan_out():\n"
        "    await asyncio.gather(*[task() for task in tasks])\n"
    )
    return repo


def _cap_with_feature(feature: str = "asyncio.TaskGroup for structured concurrency") -> Capability:
    return Capability(
        package="somelib",
        from_version="1.0.0",
        to_version="2.0.0",
        source="github_releases",
        extracted_at="2026-05-23T00:00:00+00:00",
        new_features=(feature,),
    )


def _now() -> datetime:
    return datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


_PASSING_NEW_CONTENT = (
    "import asyncio\n"
    "async def fan_out():\n"
    "    async with asyncio.TaskGroup() as tg:\n"
    "        for task in tasks:\n"
    "            tg.create_task(task())\n"
)


def _passing_proposal() -> dict:
    """P0#2: LLM contract now requires a real ``new_content`` field."""
    return {
        "should_refactor": True,
        "rationale": "fan_out uses gather; TaskGroup is cleaner here.",
        "patch_summary": "Replace gather with TaskGroup",
        "confidence": 0.85,
        "new_content": _PASSING_NEW_CONTENT,
    }


class _StubLLM:
    def __init__(self, reply_json: dict | str) -> None:
        if isinstance(reply_json, dict):
            self._reply = json.dumps(reply_json)
        else:
            self._reply = reply_json
        self.calls = 0

    def call(self, _messages):
        self.calls += 1
        return self._reply


def _builder_for(reply):
    llm = _StubLLM(reply)
    return lambda: llm, llm


# ── 1: Master switch OFF ────────────────────────────────────────────────


def test_master_switch_off_returns_immediately(isolated_dir, monkeypatch, fake_repo):
    monkeypatch.setattr(ca, "_enabled", lambda: False)
    builder, _ = _builder_for(_passing_proposal())
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "master_switch_off"


# ── 2: Rate limit ───────────────────────────────────────────────────────


def test_rate_limit_blocks_second_cr_in_same_week(isolated_dir, enabled, fake_repo):
    builder, llm = _builder_for(_passing_proposal())
    staged: list[dict] = []
    # First call — files CR
    out1 = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out1["cr_filed"] is True
    assert len(staged) == 1
    assert out1["crs_this_week"] == 1

    # Second call same week — blocked
    out2 = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature("different feature")],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out2["cr_filed"] is False
    assert out2["reason"] == "rate_limited"
    assert len(staged) == 1   # still 1 — second was blocked
    # LLM not invoked when rate-limited
    assert llm.calls == 1


# ── 3-4: Budget gating ──────────────────────────────────────────────────


def test_budget_exhausted_blocks(isolated_dir, monkeypatch, fake_repo):
    monkeypatch.setattr(ca, "_enabled", lambda: True)
    monkeypatch.setattr(ca, "_quarterly_budget_usd", lambda: 0.005)   # below per-attempt cost
    builder, llm = _builder_for(_passing_proposal())
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "budget_exhausted"
    assert llm.calls == 0


def test_budget_resets_on_quarter_rollover(isolated_dir, enabled, fake_repo):
    """Q1 attempts shouldn't count against Q2 budget."""
    # Q1 datetime: Feb 15
    q1 = datetime(2026, 2, 15, tzinfo=timezone.utc)
    # Burn the budget in Q1.
    ca.record_attempt(
        cost_usd=18.0, package="x", target_path="app/x.py",
        succeeded=False, now=q1,
    )
    assert ca.current_quarter_spend(now=q1) == 18.0

    # Q2 datetime: May 1 (different quarter)
    q2 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert ca.current_quarter_spend(now=q2) == 0.0
    assert ca.remaining_quarter_budget(now=q2) == 20.0


# ── 5: Quarter key derivation ───────────────────────────────────────────


def test_quarter_key_derivation_across_months():
    assert ca._current_quarter_key(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-Q1"
    assert ca._current_quarter_key(datetime(2026, 3, 31, tzinfo=timezone.utc)) == "2026-Q1"
    assert ca._current_quarter_key(datetime(2026, 4, 1, tzinfo=timezone.utc)) == "2026-Q2"
    assert ca._current_quarter_key(datetime(2026, 9, 30, tzinfo=timezone.utc)) == "2026-Q3"
    assert ca._current_quarter_key(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "2026-Q4"
    assert ca._current_quarter_key(datetime(2026, 12, 31, tzinfo=timezone.utc)) == "2026-Q4"


# ── 6: Dedup against architecture-requests ──────────────────────────────


def test_dedup_against_open_architecture_request(isolated_dir, enabled, fake_repo):
    builder, llm = _builder_for(_passing_proposal())
    staged: list[dict] = []
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: True,   # everything is deduped
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "no_candidate"
    assert len(staged) == 0
    assert llm.calls == 0


# ── 7: Framework package skipped ────────────────────────────────────────


def test_framework_package_skipped(isolated_dir, enabled, fake_repo):
    builder, llm = _builder_for(_passing_proposal())
    cap = Capability(
        package="crewai",   # framework
        from_version="1", to_version="2",
        source="github_releases",
        extracted_at="2026-05-23T00:00:00+00:00",
        new_features=("asyncio.TaskGroup added",),
    )
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [cap],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "no_candidate"
    assert llm.calls == 0


# ── 8: No capabilities ──────────────────────────────────────────────────


def test_no_capabilities_returns_no_capabilities_reason(isolated_dir, enabled, fake_repo):
    builder, _ = _builder_for(_passing_proposal())
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "no_capabilities"


# ── 9-10: LLM gates ─────────────────────────────────────────────────────


def test_llm_decline_skips_cr(isolated_dir, enabled, fake_repo):
    decline = {"should_refactor": False, "reason": "no_clear_opportunity"}
    builder, llm = _builder_for(decline)
    staged: list[dict] = []
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False
    assert out["reason"] == "no_candidate"
    assert len(staged) == 0
    # LLM was invoked, attempt recorded in budget ledger
    assert llm.calls == 1
    rows = ca._read_budget_ledger()
    assert len(rows) == 1
    assert rows[0]["succeeded"] is False


def test_low_confidence_proposal_skipped(isolated_dir, enabled, fake_repo):
    # P0#2: confidence threshold tightened from 0.5 → 0.7 because the
    # CR now carries a real diff.
    low = {
        "should_refactor": True,
        "rationale": "tentative", "patch_summary": "maybe",
        "confidence": 0.65,    # below 0.7
        "new_content": _PASSING_NEW_CONTENT,
    }
    builder, _ = _builder_for(low)
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False


def test_proposal_without_new_content_skipped(isolated_dir, enabled, fake_repo):
    """P0#2: refused if LLM doesn't supply new_content."""
    no_content = {
        "should_refactor": True,
        "rationale": "trust me", "patch_summary": "vague",
        "confidence": 0.95,
        # new_content deliberately missing
    }
    builder, _ = _builder_for(no_content)
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False


def test_proposal_with_truncation_sentinels_refused(isolated_dir, enabled, fake_repo):
    """P0#2: refused if new_content looks truncated."""
    truncated = {
        "should_refactor": True,
        "rationale": "x", "patch_summary": "x",
        "confidence": 0.95,
        "new_content": "import asyncio\n\n# TODO: rest of file\n",
    }
    builder, _ = _builder_for(truncated)
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is False


# ── 11: Happy path — CR filed ───────────────────────────────────────────


def test_happy_path_files_cr_and_bumps_rate_counter(isolated_dir, enabled, fake_repo):
    builder, _ = _builder_for(_passing_proposal())
    staged: list[dict] = []
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["cr_filed"] is True
    assert out["reason"] == "ok"
    assert out["crs_this_week"] == 1
    # Stage call inspection
    assert len(staged) == 1
    assert staged[0]["source"] == "dependency_radar"
    assert staged[0]["target_path"].endswith("app/user.py")
    assert staged[0]["cooldown_days"] == 14
    assert "asyncio.TaskGroup" in staged[0]["title"] or "structured concurrency" in staged[0]["title"]
    # P0#2: body_markdown now carries the actual new file content
    assert "asyncio.TaskGroup" in staged[0]["body_markdown"]


# ── 12: Signature is deterministic ──────────────────────────────────────


def test_signature_deterministic_for_same_inputs():
    cap = _cap_with_feature()
    s1 = ca._signature_for(cap, "app/user.py")
    s2 = ca._signature_for(cap, "app/user.py")
    s3 = ca._signature_for(cap, "app/other.py")
    assert s1 == s2
    assert s1 != s3
    # Must satisfy proposal_bridge's signature regex [A-Za-z0-9_.-]+
    import re
    assert re.match(r"^[A-Za-z0-9_.-]+$", s1)


# ── 13: Budget rows for both success + failure ──────────────────────────


def test_budget_records_both_success_and_failure(isolated_dir, enabled, fake_repo):
    # Attempt 1: decline (failure row)
    builder1, _ = _builder_for({"should_refactor": False, "reason": "x"})
    ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature("feature_one asyncio.gather")],
        llm_builder=builder1,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )

    # Next week — rate counter resets
    next_week = _now().replace(day=30)  # 2026-05-30 (next ISO week)
    builder2, _ = _builder_for(_passing_proposal())
    ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature("feature_two asyncio.gather")],
        llm_builder=builder2,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=next_week,
    )
    rows = ca._read_budget_ledger()
    assert len(rows) >= 2
    statuses = [r["succeeded"] for r in rows]
    assert False in statuses
    assert True in statuses


# ── 14-15: Cooperative scheduler yield (2026-05-23) ─────────────────────


def test_yields_before_llm_call_when_scheduler_timer_fires(
    isolated_dir, enabled, fake_repo, monkeypatch,
):
    """Cooperative yield to idle_scheduler's LIGHT-phase timer.

    The capability_adoption inner loop polls ``_should_yield()`` before
    each LLM call. When the scheduler's 60 s LIGHT budget fires, the
    pass returns early with ``reason="yielded"`` rather than running
    the LLM call to completion and blocking the asyncio event loop.

    Critically, the rate counter is NOT bumped on yield — the next
    idle cycle picks up the same capability backlog cleanly with no
    work duplicated, no budget double-debited.
    """
    monkeypatch.setattr(ca, "_should_yield", lambda: True)

    builder, llm = _builder_for(_passing_proposal())
    staged: list[dict] = []
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: staged.append(kw),
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    assert out["reason"] == "yielded"
    assert out["cr_filed"] is False
    assert out["crs_this_week"] == 0    # rate counter NOT bumped
    assert llm.calls == 0               # LLM never invoked
    assert len(staged) == 0
    # Budget ledger empty — no attempt was made
    assert ca._read_budget_ledger() == []


def test_mid_pass_budget_check_short_circuits_long_candidate_list(
    isolated_dir, monkeypatch, fake_repo,
):
    """A long candidate list cannot burn the entire quarterly budget
    in a single pass.

    Setup: budget is just above the per-attempt cost so the entry-gate
    passes, but the first attempt's debit pushes the remaining budget
    below the threshold. The mid-pass recheck catches this on the
    SECOND iteration of the inner loop and short-circuits with
    ``reason="budget_exhausted"`` — without the recheck the pass would
    burn through every candidate site before noticing.
    """
    monkeypatch.setattr(ca, "_enabled", lambda: True)
    monkeypatch.setattr(
        ca, "_quarterly_budget_usd",
        lambda: ca._ESTIMATED_COST_PER_ATTEMPT_USD * 1.5,
    )

    # A capability whose discover_candidate_sites yields 2+ sites:
    # we add a second app/ file that also mentions the symbol.
    second = fake_repo / "app" / "other.py"
    second.write_text(
        "import asyncio\n"
        "async def another():\n"
        "    await asyncio.gather(task())\n"
    )

    decline = {"should_refactor": False, "reason": "no_clear_opportunity"}
    builder, llm = _builder_for(decline)
    out = ca.run_one_pass(
        repo_root=fake_repo,
        capability_iterator=lambda: [_cap_with_feature()],
        llm_builder=builder,
        stage_fn=lambda **kw: None,
        architecture_dedup=lambda p: False,
        now=_now(),
    )
    # Exactly ONE LLM call — the second was short-circuited by the
    # mid-pass recheck before the LLM was invoked.
    assert llm.calls == 1
    assert out["cr_filed"] is False
    assert out["reason"] == "budget_exhausted"
