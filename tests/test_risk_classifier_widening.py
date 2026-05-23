"""Tests for the trust-zone widening proposer (Phase 4 piece 1, 2026-05-20).

Covers:
  * WideningEvidence aggregation (approvals/rejections/rollbacks counts +
    rates + history days)
  * propose_widenings decision rules (4 gates: min_approvals,
    max_rollback_rate, max_rejection_rate, min_history_days)
  * Already-allowlisted entries skipped
  * Path-prefix grouping
  * Empty history → no proposals
  * Max-proposals cap
  * Audit log JSONL round-trip
  * runtime_settings master switch + threshold setters
  * Defensive: empty inputs, missing decided_at, status enum vs string

Safety invariants pinned:
  * Default thresholds reject low-evidence widenings
  * Any rollback in history defeats the proposal (rollback_rate ≤ 0 default)
  * proposer never auto-applies — returns proposals; doesn't call setters
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app import runtime_settings  # noqa: E402
from app.risk_classifier import widening  # noqa: E402
from app.risk_classifier.widening import (  # noqa: E402
    WideningEvidence,
    WideningProposal,
    aggregate_evidence,
    append_proposal,
    list_proposals,
    propose_widenings,
)


# ── Stub CR shape (matches duck-typed status/requestor/path/etc.) ──


@dataclass
class StubCR:
    id: str
    requestor: str
    path: str
    status: str    # raw string; the aggregator handles both enum + str
    created_at: str = "2026-03-01T00:00:00+00:00"
    decided_at: str = "2026-04-05T00:00:00+00:00"


def _approvals(
    *,
    n: int,
    requestor: str,
    path_pattern: str,
    days_span: int = 35,
) -> list[StubCR]:
    """Build N approved CRs spanning ``days_span`` days. The first
    CR is dated 2026-01-01; the last is N*days_span/N days later."""
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    step = timedelta(days=days_span / max(n - 1, 1))
    out: list[StubCR] = []
    for i in range(n):
        when = (start + step * i).isoformat()
        out.append(StubCR(
            id=f"cr-{requestor}-{i:03d}",
            requestor=requestor,
            path=path_pattern.format(i=i),
            status="approved",
            created_at=when,
            decided_at=when,
        ))
    return out


def _patch_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    runtime_settings._cache = None  # type: ignore[attr-defined]
    yield
    runtime_settings._cache = None  # type: ignore[attr-defined]


# ============================================================================
# aggregate_evidence
# ============================================================================


class TestAggregateEvidence:
    def test_empty_input_returns_empty(self):
        assert aggregate_evidence([]) == {}

    def test_counts_approvals(self):
        crs = _approvals(n=5, requestor="r1", path_pattern="workspace/notes/{i}.md")
        ev = aggregate_evidence(crs)
        key = ("r1", "workspace/notes/")
        assert key in ev
        assert ev[key].approvals == 5
        assert ev[key].rejections == 0
        assert ev[key].rollbacks == 0

    def test_counts_rejections_and_rollbacks(self):
        crs = [
            StubCR(id="a", requestor="r1", path="workspace/notes/x", status="approved"),
            StubCR(id="b", requestor="r1", path="workspace/notes/y", status="rejected"),
            StubCR(id="c", requestor="r1", path="workspace/notes/z", status="rolled_back"),
            StubCR(id="d", requestor="r1", path="workspace/notes/w", status="applied"),
            StubCR(id="e", requestor="r1", path="workspace/notes/v", status="apply_failed"),
        ]
        ev = aggregate_evidence(crs)[("r1", "workspace/notes/")]
        assert ev.approvals == 2     # approved + applied
        assert ev.rejections == 1
        assert ev.rollbacks == 1
        assert ev.applied == 2       # applied + rolled_back
        assert ev.apply_failed == 1

    def test_groups_by_path_prefix(self):
        crs = [
            StubCR(id="a", requestor="r1", path="app/agents/coder.py", status="approved"),
            StubCR(id="b", requestor="r1", path="app/agents/researcher.py", status="approved"),
            StubCR(id="c", requestor="r1", path="app/control_plane/x.py", status="approved"),
        ]
        ev = aggregate_evidence(crs)
        # Same requestor + path-prefix groups together
        assert ("r1", "app/agents/") in ev
        assert ("r1", "app/control_plane/") in ev
        assert ev[("r1", "app/agents/")].approvals == 2
        assert ev[("r1", "app/control_plane/")].approvals == 1

    def test_skips_top_level_paths(self):
        # A bare ``foo.py`` is too coarse to group on — excluded.
        crs = [
            StubCR(id="a", requestor="r1", path="foo.py", status="approved"),
        ]
        assert aggregate_evidence(crs) == {}

    def test_skips_missing_requestor_or_path(self):
        crs = [
            StubCR(id="a", requestor="", path="workspace/notes/x", status="approved"),
            StubCR(id="b", requestor="r1", path="", status="approved"),
        ]
        assert aggregate_evidence(crs) == {}

    def test_temporal_extent_recorded(self):
        crs = [
            StubCR(id="a", requestor="r1", path="workspace/notes/x",
                   status="approved",
                   decided_at="2026-01-15T00:00:00+00:00"),
            StubCR(id="b", requestor="r1", path="workspace/notes/y",
                   status="approved",
                   decided_at="2026-03-15T00:00:00+00:00"),
        ]
        ev = aggregate_evidence(crs)[("r1", "workspace/notes/")]
        assert ev.first_at == "2026-01-15T00:00:00+00:00"
        assert ev.last_at == "2026-03-15T00:00:00+00:00"
        # ~59 days
        assert ev.history_days > 50

    def test_sample_cr_ids_capped_at_five(self):
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md")
        ev = aggregate_evidence(crs)[("r1", "workspace/notes/")]
        assert len(ev.sample_cr_ids) == 5

    def test_rollback_rate_calculation(self):
        crs = [
            StubCR(id=f"a{i}", requestor="r1", path="workspace/notes/x", status="applied")
            for i in range(8)
        ] + [
            StubCR(id="r1", requestor="r1", path="workspace/notes/x", status="rolled_back"),
            StubCR(id="r2", requestor="r1", path="workspace/notes/x", status="rolled_back"),
        ]
        ev = aggregate_evidence(crs)[("r1", "workspace/notes/")]
        # applied = 8 (applied status) + 2 (rolled_back) = 10
        # rollbacks = 2
        # rollback_rate = 2/10 = 0.2
        assert ev.applied == 10
        assert ev.rollbacks == 2
        assert ev.rollback_rate == pytest.approx(0.2)


# ============================================================================
# propose_widenings — decision gates
# ============================================================================


class TestProposeWidenings:
    def test_empty_input_no_proposals(self):
        assert propose_widenings([]) == []

    def test_below_min_approvals_no_proposal(self):
        # 9 approvals < default 10 → no proposal
        crs = _approvals(n=9, requestor="r1", path_pattern="workspace/notes/{i}.md")
        assert propose_widenings(crs) == []

    def test_at_min_approvals_with_history_gets_proposal(self):
        # 10 approvals + 35 days of history + no rollbacks → proposal
        crs = _approvals(n=10, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=35)
        props = propose_widenings(crs)
        # Two proposals: one for the requestor allowlist, one for the path
        assert len(props) == 2
        names = {p.list_name for p in props}
        assert names == {"auto_apply_allowed_requestors", "auto_apply_allowed_paths"}

    def test_any_rollback_blocks_proposal(self):
        # 12 applied + 1 rolled_back → rollback_rate > 0 → blocked
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        crs[0] = StubCR(
            id="rolled", requestor="r1",
            path="workspace/notes/oops",
            status="rolled_back",
            decided_at=crs[0].decided_at,
        )
        props = propose_widenings(crs)
        assert props == []

    def test_high_rejection_rate_blocks(self):
        approvals = _approvals(n=8, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        rejections = [
            StubCR(id=f"rej-{i}", requestor="r1", path="workspace/notes/rej",
                   status="rejected", decided_at="2026-02-15T00:00:00+00:00")
            for i in range(3)
        ]
        # 8 approvals + 3 rejections = 11 decided; rejection_rate = 3/11 ≈ 0.27
        # Default max_rejection_rate is 0.10 → blocked.
        props = propose_widenings(approvals + rejections)
        assert props == []

    def test_insufficient_history_blocks(self):
        # 12 approvals but all dated within 5 days → no proposal
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=5)
        assert propose_widenings(crs) == []

    def test_already_allowlisted_requestor_skipped(self):
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        props = propose_widenings(
            crs,
            current_allowed_requestors=["r1"],
            current_allowed_paths=[],
        )
        # Only path proposal — requestor already in allowlist
        assert len(props) == 1
        assert props[0].list_name == "auto_apply_allowed_paths"

    def test_already_allowlisted_path_skipped(self):
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        props = propose_widenings(
            crs,
            current_allowed_requestors=[],
            current_allowed_paths=["workspace/notes/"],
        )
        # Only requestor proposal — path already in allowlist
        assert len(props) == 1
        assert props[0].list_name == "auto_apply_allowed_requestors"

    def test_both_allowlisted_no_proposal(self):
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        props = propose_widenings(
            crs,
            current_allowed_requestors=["r1"],
            current_allowed_paths=["workspace/notes/"],
        )
        assert props == []

    def test_max_proposals_cap(self):
        # 4 strong (requestor, path) combos × 2 proposals = 8 ⇒ cap at 5
        crs = []
        for j in range(4):
            crs.extend(_approvals(
                n=12, requestor=f"r{j}",
                path_pattern=f"app/d{j}/{{i}}.py",
                days_span=40,
            ))
        props = propose_widenings(crs, max_proposals=5)
        assert len(props) == 5

    def test_relaxed_thresholds_allow_more(self):
        # Same data as below-min-approvals test, but with relaxed gate.
        crs = _approvals(n=5, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        props = propose_widenings(crs, min_approvals=5)
        assert len(props) == 2  # both lists qualify

    def test_proposal_carries_evidence(self):
        crs = _approvals(n=12, requestor="r1", path_pattern="workspace/notes/{i}.md", days_span=40)
        props = propose_widenings(crs)
        assert props
        for p in props:
            assert p.evidence.requestor == "r1"
            assert p.evidence.path_prefix == "workspace/notes/"
            assert p.evidence.approvals == 12
            assert p.evidence.rollbacks == 0
            assert p.proposal_id  # non-empty uuid
            assert p.proposed_at  # ISO timestamp
            assert "approvals" in p.rationale
            assert len(p.evidence.sample_cr_ids) > 0


# ============================================================================
# Audit log (JSONL)
# ============================================================================


class TestAuditLog:
    def test_append_and_list_roundtrip(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        proposal = WideningProposal(
            proposal_id="hex123",
            proposed_at="2026-05-20T12:00:00+00:00",
            list_name="auto_apply_allowed_requestors",
            new_entry="r1",
            evidence=WideningEvidence(
                requestor="r1", path_prefix="x/",
                approvals=10, rollbacks=0,
            ),
            rationale="test",
        )
        append_proposal(proposal)
        results = list_proposals()
        assert len(results) == 1
        assert results[0].proposal_id == "hex123"
        assert results[0].new_entry == "r1"
        assert results[0].evidence.approvals == 10

    def test_list_proposals_newest_first(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        for i in range(3):
            append_proposal(WideningProposal(
                proposal_id=f"id-{i}",
                proposed_at=f"2026-05-{15 + i:02d}T00:00:00+00:00",
                list_name="auto_apply_allowed_requestors",
                new_entry=f"r{i}",
                evidence=WideningEvidence(requestor=f"r{i}", path_prefix=""),
            ))
        results = list_proposals()
        # Newest first
        assert results[0].new_entry == "r2"
        assert results[2].new_entry == "r0"

    def test_list_proposals_missing_file_returns_empty(self, tmp_path):
        widening.reset_for_tests(tmp_path / "fresh")
        assert list_proposals() == []

    def test_list_proposals_handles_corrupt_lines(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        path = tmp_path / "widening_proposals.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"proposal_id": "ok", "proposed_at": "2026-01-01T00:00:00+00:00", "list_name": "x", "new_entry": "y"}\n'
            'not json at all\n'
            '{"proposal_id": "ok2", "proposed_at": "2026-01-02T00:00:00+00:00", "list_name": "x", "new_entry": "z"}\n',
        )
        results = list_proposals()
        # Corrupt line skipped; two valid lines kept
        assert len(results) == 2

    def test_proposal_roundtrip_via_dict(self):
        original = WideningProposal(
            proposal_id="x", proposed_at="2026-05-20T00:00:00+00:00",
            list_name="auto_apply_allowed_paths",
            new_entry="workspace/notes/",
            evidence=WideningEvidence(
                requestor="r1", path_prefix="workspace/notes/",
                approvals=15, rejections=1, rollbacks=0,
                first_at="2026-03-01T00:00:00+00:00",
                last_at="2026-04-05T00:00:00+00:00",
                sample_cr_ids=["a", "b", "c"],
            ),
            rationale="reason",
        )
        d = original.to_dict()
        json.dumps(d)  # serialisable
        reloaded = WideningProposal.from_dict(d)
        assert reloaded.proposal_id == original.proposal_id
        assert reloaded.evidence.approvals == 15
        assert reloaded.evidence.sample_cr_ids == ["a", "b", "c"]


# ============================================================================
# runtime_settings master switch + thresholds
# ============================================================================


class TestRuntimeSettings:
    def test_default_off(self):
        with _patch_settings():
            assert not runtime_settings.get_widening_proposer_enabled()

    def test_set_and_get_master_switch(self):
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_widening_proposer_enabled(True)
            assert runtime_settings.get_widening_proposer_enabled()

    def test_default_threshold_values(self):
        with _patch_settings():
            assert runtime_settings.get_widening_min_approvals() == 10
            assert runtime_settings.get_widening_max_rollback_rate() == 0.0
            assert runtime_settings.get_widening_max_rejection_rate() == 0.10
            assert runtime_settings.get_widening_min_history_days() == 30

    def test_setters_reject_invalid_ranges(self):
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            with pytest.raises(ValueError):
                runtime_settings.set_widening_min_approvals(0)
            with pytest.raises(ValueError):
                runtime_settings.set_widening_min_approvals(10_000)
            with pytest.raises(ValueError):
                runtime_settings.set_widening_max_rollback_rate(-0.1)
            with pytest.raises(ValueError):
                runtime_settings.set_widening_max_rollback_rate(1.5)
            with pytest.raises(ValueError):
                runtime_settings.set_widening_min_history_days(0)
            with pytest.raises(ValueError):
                runtime_settings.set_widening_min_history_days(99_999)


# ============================================================================
# run_widening_scan (top-level)
# ============================================================================


class TestRunWideningScan:
    def test_disabled_returns_empty(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        with _patch_settings(widening_proposer_enabled=False):
            result = widening.run_widening_scan(crs=[])
        assert result == []

    def test_enabled_with_qualifying_crs(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        crs = _approvals(
            n=12, requestor="r1",
            path_pattern="workspace/notes/{i}.md",
            days_span=40,
        )
        with _patch_settings(widening_proposer_enabled=True):
            result = widening.run_widening_scan(
                crs=crs,
                emit_audit=True,
            )
        assert len(result) == 2
        # Confirm audit log written
        audit_file = tmp_path / "widening_proposals.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_audit_skipped_when_emit_audit_false(self, tmp_path):
        widening.reset_for_tests(tmp_path)
        crs = _approvals(
            n=12, requestor="r1",
            path_pattern="workspace/notes/{i}.md",
            days_span=40,
        )
        with _patch_settings(widening_proposer_enabled=True):
            result = widening.run_widening_scan(
                crs=crs, emit_audit=False,
            )
        assert len(result) == 2
        audit_file = tmp_path / "widening_proposals.jsonl"
        assert not audit_file.exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
