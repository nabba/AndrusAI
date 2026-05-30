"""Tests for Gate B — evidence-gated promotion (2026-05-30).

The proposal bridge used to promote a library_radar markdown-doc proposal to
an operator CR on a 7-day timer regardless of whether the library's trial
(PyPI resolution + venv smoke-import) passed, failed, or even ran. That
unverified doc-CR is the noise the operator kept rejecting. Gate B gates the
promotion on the trial verdict, so the operator only ever reviews library
proposals the system actually install+import verified (as the trial-backed
adoption CR). Other observational sources are unaffected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _staged(source="library_radar", signature="sig1", *, staged_days_ago=10):
    from app.proposal_bridge.store import ProposalState, ProposalStatus
    staged_at = (
        datetime.now(timezone.utc) - timedelta(days=staged_days_ago)
    ).isoformat()
    return ProposalState(
        source=source, signature=signature,
        title="OpenRouter Web Search & Fetch Tooling",
        target_path=f"docs/proposed_libraries/{signature}-x.md",
        body_hash="abc", staged_at=staged_at,
        status=ProposalStatus.STAGED, cooldown_days=7,
    )


class _TS:
    """Minimal trial_state.TrialState stand-in."""
    def __init__(self, status, error="", adoption_cr_id=""):
        self.status = status
        self.trial_error = error
        self.adoption_cr_id = adoption_cr_id


@pytest.fixture
def trial(monkeypatch):
    """Install a fake trial_state.get returning a chosen verdict (or None)."""
    import app.library_radar.trial_state as ts

    def _install(value):
        monkeypatch.setattr(ts, "get", lambda sig: value)
    return _install


@pytest.fixture
def gating_on(monkeypatch):
    from app.proposal_bridge import promoter
    monkeypatch.setattr(promoter, "_evidence_gating_enabled", lambda: True)


# ── _evidence_verdict ────────────────────────────────────────────────


def test_non_library_source_always_promotes(gating_on, trial):
    from app.proposal_bridge import promoter
    trial(_TS("failed", "irrelevant"))  # even with a failed trial row
    verdict, _ = promoter._evidence_verdict(_staged(source="capability_gap_analyzer"))
    assert verdict == "promote"


def test_pending_trial_waits(gating_on, trial):
    from app.proposal_bridge import promoter
    trial(_TS("pending"))
    assert promoter._evidence_verdict(_staged())[0] == "wait"


def test_no_trial_row_waits(gating_on, trial):
    from app.proposal_bridge import promoter
    trial(None)
    assert promoter._evidence_verdict(_staged())[0] == "wait"


def test_failed_trial_rejects(gating_on, trial):
    from app.proposal_bridge import promoter
    trial(_TS("failed", "no PyPI distribution for ['openrouter']"))
    verdict, reason = promoter._evidence_verdict(_staged())
    assert verdict == "reject"
    assert "no PyPI" in reason


def test_passed_trial_supersedes(gating_on, trial):
    from app.proposal_bridge import promoter
    trial(_TS("adoption_cr_filed", adoption_cr_id="cr_abc"))
    verdict, reason = promoter._evidence_verdict(_staged())
    assert verdict == "supersede"
    assert "cr_abc" in reason


def test_gating_disabled_promotes(monkeypatch, trial):
    from app.proposal_bridge import promoter
    monkeypatch.setattr(promoter, "_evidence_gating_enabled", lambda: False)
    trial(_TS("failed"))
    assert promoter._evidence_verdict(_staged())[0] == "promote"


def test_trial_state_failure_fails_open_to_promote(gating_on, monkeypatch):
    """A broken trial_state module must not silence the producer."""
    import app.library_radar.trial_state as ts
    from app.proposal_bridge import promoter

    def _boom(sig):
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(ts, "get", _boom)
    assert promoter._evidence_verdict(_staged())[0] == "promote"


# ── run_one_pass integration ─────────────────────────────────────────


@pytest.fixture
def fake_pass(monkeypatch):
    """Drive run_one_pass over a single in-memory proposal, capturing
    update_proposal writes and asserting _stage_to_cr is never reached on a
    non-promote verdict."""
    from app.proposal_bridge import promoter

    state = {"proposal": None, "staged_to_cr": 0, "updates": []}

    monkeypatch.setattr(promoter, "_enabled", lambda: True)
    monkeypatch.setattr(promoter, "iter_proposals", lambda: [state["proposal"]])
    monkeypatch.setattr(promoter, "_maybe_cleanup_terminal", lambda s, n: False)
    monkeypatch.setattr(promoter, "_maybe_expire_stale", lambda s, n: False)
    monkeypatch.setattr(promoter, "_evidence_gating_enabled", lambda: True)
    monkeypatch.setattr(promoter, "_publish_outcome", lambda c: None)

    def _update(s):
        state["updates"].append((s.status, dict(s.notes)))
    monkeypatch.setattr(promoter, "update_proposal", _update)

    def _stage(s):
        state["staged_to_cr"] += 1
        return "cr_should_not_happen"
    monkeypatch.setattr(promoter, "_stage_to_cr", _stage)

    return state


def test_passed_trial_terminates_without_filing_doc_cr(fake_pass, trial):
    from app.proposal_bridge import promoter
    from app.proposal_bridge.store import ProposalStatus
    fake_pass["proposal"] = _staged()
    trial(_TS("adoption_cr_filed", adoption_cr_id="cr_abc"))

    counters = promoter.run_one_pass()

    assert fake_pass["staged_to_cr"] == 0          # never filed a doc-CR
    assert counters["evidence_superseded"] == 1
    assert counters["promoted_to_cr"] == 0
    # proposal terminated as APPLIED with an audit note
    assert fake_pass["updates"][-1][0] == ProposalStatus.APPLIED
    assert "evidence_outcome" in fake_pass["updates"][-1][1]


def test_failed_trial_terminates_as_rejected(fake_pass, trial):
    from app.proposal_bridge import promoter
    from app.proposal_bridge.store import ProposalStatus
    fake_pass["proposal"] = _staged()
    trial(_TS("failed", "no PyPI distribution"))

    counters = promoter.run_one_pass()

    assert fake_pass["staged_to_cr"] == 0
    assert counters["evidence_rejected"] == 1
    assert fake_pass["updates"][-1][0] == ProposalStatus.REJECTED


def test_pending_trial_neither_promotes_nor_terminates(fake_pass, trial):
    from app.proposal_bridge import promoter
    fake_pass["proposal"] = _staged()
    trial(_TS("running"))

    counters = promoter.run_one_pass()

    assert fake_pass["staged_to_cr"] == 0          # waits, no doc-CR
    assert counters["evidence_waiting"] == 1
    assert fake_pass["updates"] == []              # untouched, stays STAGED


def test_non_library_source_still_promotes_normally(fake_pass, trial):
    from app.proposal_bridge import promoter
    fake_pass["proposal"] = _staged(source="paper_pipeline", signature="p1")
    trial(_TS("failed"))  # ignored for non-library sources

    counters = promoter.run_one_pass()

    assert fake_pass["staged_to_cr"] == 1          # normal promotion path
    assert counters["promoted_to_cr"] == 1
