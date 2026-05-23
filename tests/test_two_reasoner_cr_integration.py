"""Tests for the two-reasoner CR-lifecycle integration (2026-05-20).

Covers Phase 4 piece 2c:
  * context_id round-trip on ReviewOutcome
  * find_review_for_context lookup by id
  * review_for_change_request helper
  * is_high_stakes_zone classification
  * _proposal_text_for_change_request builder
  * Lifecycle hook fires for high-stakes zones, skips for low-stakes
  * Master switch OFF → no review fires (lifecycle path proceeds)
  * Review exception in lifecycle is isolated — CR still created
  * REST endpoint GET /api/cp/changes/{id}/review (200 + 404 paths)
"""
from __future__ import annotations

import sys
import types
import unittest
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
from app.risk_classifier import two_reasoner  # noqa: E402
from app.risk_classifier.two_reasoner import (  # noqa: E402
    HIGH_STAKES_ZONES_FOR_REVIEW,
    ReasonerVerdict,
    ReviewOutcome,
    Verdict,
    _proposal_text_for_change_request,
    append_review,
    find_review_for_context,
    is_high_stakes_zone,
    review_for_change_request,
)


def _patch_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    two_reasoner.reset_for_tests(tmp_path)
    runtime_settings._cache = None  # type: ignore[attr-defined]
    yield
    runtime_settings._cache = None  # type: ignore[attr-defined]
    two_reasoner.reset_for_tests(None)


def _stub_safe(text, zone):
    return ReasonerVerdict(
        reasoner_id="stub-safe",
        verdict=Verdict.SAFE,
        confidence=0.9,
    )


def _stub_unsafe(text, zone):
    return ReasonerVerdict(
        reasoner_id="stub-unsafe",
        verdict=Verdict.UNSAFE,
        confidence=0.85,
    )


class _FakeCR:
    """Minimal CR stub matching the attribute surface
    ``_proposal_text_for_change_request`` reads."""

    def __init__(
        self,
        *,
        id: str = "cr-xyz",
        path: str = "app/control_plane/budgets.py",
        requestor: str = "self_heal_router",
        reason: str = "fix bug",
        diff: str = "--- a/file\n+++ b/file\n@@\n-x\n+y\n",
    ) -> None:
        self.id = id
        self.path = path
        self.requestor = requestor
        self.reason = reason
        self.diff = diff


# ── Context-id round-trip ───────────────────────────────────────────


class TestContextId:
    def test_review_outcome_carries_context_id(self):
        outcome = ReviewOutcome(
            review_id="r1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="cr-test-123",
        )
        assert outcome.context_id == "cr-test-123"

    def test_to_dict_includes_context_id(self):
        outcome = ReviewOutcome(
            review_id="r1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="cr-xyz",
        )
        assert outcome.to_dict()["context_id"] == "cr-xyz"

    def test_legacy_audit_without_context_id_loads_empty(self, tmp_path):
        # Pre-2026-05-20 audit lines lack the context_id key — they
        # should load with context_id="" rather than crashing.
        import json
        audit_path = tmp_path / "two_reasoner_reviews.jsonl"
        audit_path.write_text(json.dumps({
            "review_id": "legacy-r1",
            "reviewed_at": "2026-05-19T00:00:00+00:00",
            "verdict": "safe",
            "confidence": 0.8,
            "per_reasoner": [],
            "diagnostic": "test",
            "zone": "chat",
            # no context_id field
        }) + "\n")
        outcomes = two_reasoner.list_reviews()
        assert len(outcomes) == 1
        assert outcomes[0].context_id == ""


# ── find_review_for_context ────────────────────────────────────────


class TestFindReviewForContext:
    def test_returns_review_when_present(self):
        append_review(ReviewOutcome(
            review_id="r1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="cr-target",
        ))
        result = find_review_for_context("cr-target")
        assert result is not None
        assert result.review_id == "r1"

    def test_returns_none_when_absent(self):
        append_review(ReviewOutcome(
            review_id="r1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="cr-other",
        ))
        assert find_review_for_context("cr-target") is None

    def test_empty_context_id_returns_none(self):
        assert find_review_for_context("") is None

    def test_returns_newest_match(self):
        # Two reviews for the same context — newest wins (list_reviews
        # already orders newest-first).
        append_review(ReviewOutcome(
            review_id="older",
            reviewed_at="2026-05-19T00:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="cr-x",
        ))
        append_review(ReviewOutcome(
            review_id="newer",
            reviewed_at="2026-05-20T00:00:00+00:00",
            verdict=Verdict.DISAGREE,
            confidence=0.85,
            context_id="cr-x",
        ))
        result = find_review_for_context("cr-x")
        assert result.review_id == "newer"

    def test_legacy_review_without_context_id_skipped(self):
        # A review with empty context_id should NOT match an empty
        # query (already covered by test_empty_context_id_returns_none)
        # and should NOT spuriously match any non-empty query.
        append_review(ReviewOutcome(
            review_id="legacy",
            reviewed_at="2026-05-19T00:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.9,
            context_id="",
        ))
        assert find_review_for_context("cr-target") is None


# ── is_high_stakes_zone ────────────────────────────────────────────


class TestIsHighStakesZone:
    def test_high_stakes_zones_return_true(self):
        for z in HIGH_STAKES_ZONES_FOR_REVIEW:
            assert is_high_stakes_zone(z), f"{z!r} should be high-stakes"

    def test_low_stakes_zones_return_false(self):
        for z in ["chat", "free", "reversible", "observable", "operator_gated"]:
            assert not is_high_stakes_zone(z)

    def test_empty_or_none_returns_false(self):
        assert not is_high_stakes_zone("")
        assert not is_high_stakes_zone(None)  # type: ignore[arg-type]

    def test_case_insensitive(self):
        assert is_high_stakes_zone("FINANCIAL")
        assert is_high_stakes_zone("  Financial  ")


# ── _proposal_text_for_change_request ──────────────────────────────


class TestProposalTextBuilder:
    def test_includes_path_and_requestor(self):
        cr = _FakeCR(path="app/x.py", requestor="agent1")
        text = _proposal_text_for_change_request(cr)
        assert "app/x.py" in text
        assert "agent1" in text

    def test_includes_reason_and_diff(self):
        cr = _FakeCR(
            reason="multi-line\nreason text",
            diff="diff content here",
        )
        text = _proposal_text_for_change_request(cr)
        assert "multi-line" in text
        assert "diff content here" in text

    def test_long_diff_truncated(self):
        long_diff = "x" * 6000
        cr = _FakeCR(diff=long_diff)
        text = _proposal_text_for_change_request(cr)
        # Bounded at 4000 chars with a truncation marker
        assert "[...diff truncated for review...]" in text

    def test_missing_fields_handled_gracefully(self):
        class _BareCR:
            id = "x"
        cr = _BareCR()
        text = _proposal_text_for_change_request(cr)
        # Doesn't raise; includes the unknown markers
        assert "unknown" in text.lower()


# ── review_for_change_request ──────────────────────────────────────


class TestReviewForChangeRequest:
    def test_passes_cr_id_as_context(self):
        cr = _FakeCR(id="cr-context-test")

        with _patch_settings(two_reasoner_review_enabled=True):
            outcome = review_for_change_request(
                cr,
                zone="financial",
                reasoners=[_stub_safe, _stub_safe],
            )
        assert outcome.context_id == "cr-context-test"
        assert outcome.zone == "financial"

    def test_master_switch_off_yields_disabled(self):
        cr = _FakeCR(id="cr-disabled-test")
        with _patch_settings(two_reasoner_review_enabled=False):
            outcome = review_for_change_request(
                cr,
                zone="financial",
                reasoners=[_stub_safe],
            )
        assert outcome.verdict is Verdict.DISABLED
        assert outcome.context_id == "cr-disabled-test"

    def test_disagreement_outcome_recorded(self):
        cr = _FakeCR(id="cr-disagree")
        with _patch_settings(two_reasoner_review_enabled=True):
            outcome = review_for_change_request(
                cr,
                zone="financial",
                reasoners=[_stub_safe, _stub_unsafe],
            )
        assert outcome.verdict is Verdict.DISAGREE


# ── Lifecycle hook integration ─────────────────────────────────────


@pytest.fixture
def cr_store_dir(tmp_path, monkeypatch):
    """Redirect the CR store + audit log so create_request doesn't
    pollute real workspace state."""
    from app.change_requests import store as cr_store
    monkeypatch.setattr(cr_store, "_STORE_DIR", tmp_path / "change_requests")
    monkeypatch.setattr(
        cr_store, "_AUDIT_LOG",
        tmp_path / "change_requests" / "audit.jsonl",
    )
    cr_store.reset_for_tests()
    return tmp_path


class TestLifecycleHook:
    def test_low_stakes_zone_no_review_recorded(self, cr_store_dir):
        from app.change_requests.lifecycle import create_request
        cr = create_request(
            requestor="test-agent",
            path="workspace/notes/test.md",  # zone=REVERSIBLE
            new_content="new content\n",
            old_content="",
            reason="testing low-stakes",
        )
        # No review for low-stakes
        assert find_review_for_context(cr.id) is None

    def test_high_stakes_with_switch_on_records_review(
        self, cr_store_dir, monkeypatch,
    ):
        from app.change_requests.lifecycle import create_request

        captured = []

        def _stub_reasoner(text, zone):
            captured.append((text, zone))
            return ReasonerVerdict(
                reasoner_id="lifecycle-stub",
                verdict=Verdict.SAFE,
                confidence=0.9,
            )

        # Patch the default reasoners so the lifecycle hook uses our
        # stub (avoids the real Anthropic call).
        monkeypatch.setattr(
            two_reasoner,
            "DEFAULT_REASONERS",
            (_stub_reasoner, _stub_reasoner),
        )

        # Path under app/control_plane/budgets.py → FINANCIAL zone
        with _patch_settings(two_reasoner_review_enabled=True):
            cr = create_request(
                requestor="test-agent",
                path="deploy/scripts/test_runner.sh",
                new_content="x = 1\n",
                old_content="x = 0\n",
                reason="bump budget",
            )

        # Review should be recorded
        review = find_review_for_context(cr.id)
        assert review is not None
        assert review.context_id == cr.id
        assert review.verdict is Verdict.SAFE
        # Reasoners were invoked with the CR's content
        assert len(captured) >= 1
        assert "bump budget" in captured[0][0]

    def test_high_stakes_with_switch_off_no_llm_call(
        self, cr_store_dir, monkeypatch,
    ):
        """With master switch OFF, the lifecycle hook still runs but
        the review returns Verdict.DISABLED without calling reasoners."""
        from app.change_requests.lifecycle import create_request

        call_count = [0]

        def _stub_reasoner(text, zone):
            call_count[0] += 1
            return ReasonerVerdict(
                reasoner_id="stub", verdict=Verdict.SAFE, confidence=0.9,
            )

        monkeypatch.setattr(
            two_reasoner,
            "DEFAULT_REASONERS",
            (_stub_reasoner,),
        )

        with _patch_settings(two_reasoner_review_enabled=False):
            cr = create_request(
                requestor="test-agent",
                path="deploy/scripts/test_runner.sh",
                new_content="new\n",
                old_content="old\n",
                reason="test",
            )

        # No reasoner calls when switch is off
        assert call_count[0] == 0
        # CR still created normally
        assert cr.id

    def test_review_exception_isolated(self, cr_store_dir, monkeypatch):
        """A crashing reasoner doesn't fail CR creation."""
        from app.change_requests.lifecycle import create_request

        def _boom(text, zone):
            raise RuntimeError("LLM unreachable")

        monkeypatch.setattr(
            two_reasoner,
            "DEFAULT_REASONERS",
            (_boom,),
        )

        with _patch_settings(two_reasoner_review_enabled=True):
            cr = create_request(
                requestor="test-agent",
                path="deploy/scripts/test_runner.sh",
                new_content="new\n",
                old_content="old\n",
                reason="test",
            )
        # CR still created
        assert cr.id


# ── REST endpoint ──────────────────────────────────────────────────


@pytest.fixture
def changes_client(cr_store_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.control_plane.changes_api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestChangeReviewEndpoint:
    def test_get_review_for_unknown_cr_404(self, changes_client):
        resp = changes_client.get("/api/cp/changes/nonexistent/review")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_review_when_no_review_recorded_404(
        self, changes_client, cr_store_dir,
    ):
        from app.change_requests.lifecycle import create_request
        # Low-stakes CR → no review fires
        cr = create_request(
            requestor="test-agent",
            path="workspace/notes/test.md",
            new_content="x\n",
            old_content="",
            reason="test",
        )
        resp = changes_client.get(f"/api/cp/changes/{cr.id}/review")
        assert resp.status_code == 404
        assert "no two-reasoner review" in resp.json()["detail"].lower()

    def test_get_review_when_recorded_200(
        self, changes_client, cr_store_dir,
    ):
        # Manually append a review for a fake CR + a real CR
        from app.change_requests.lifecycle import create_request
        cr = create_request(
            requestor="test-agent",
            path="workspace/notes/test.md",
            new_content="x\n",
            old_content="",
            reason="test",
        )
        append_review(ReviewOutcome(
            review_id="manual-review-1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.85,
            diagnostic="manual test",
            zone="reversible",
            context_id=cr.id,
        ))
        resp = changes_client.get(f"/api/cp/changes/{cr.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["review_id"] == "manual-review-1"
        assert data["context_id"] == cr.id


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
