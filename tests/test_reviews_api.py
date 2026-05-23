"""Tests for the two-reasoner reviews REST API (2026-05-20).

Covers Phase 4 piece 2b:
  * GET /api/cp/reviews — list, newest-first ordering
  * GET /api/cp/reviews?verdict=… — filter
  * GET /api/cp/reviews?verdict=bogus — 400 with valid-list message
  * GET /api/cp/reviews/{id} — 200 + 404
  * GET /api/cp/reviews/stats/summary — verdict counts
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

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


from app.risk_classifier import two_reasoner  # noqa: E402
from app.risk_classifier.two_reasoner import (  # noqa: E402
    ReasonerVerdict,
    ReviewOutcome,
    Verdict,
    append_review,
)


@pytest.fixture
def client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.control_plane.reviews_api import router

    two_reasoner.reset_for_tests(tmp_path)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    two_reasoner.reset_for_tests(None)


def _outcome(
    review_id: str,
    *,
    verdict: Verdict = Verdict.SAFE,
    confidence: float = 0.85,
    reviewed_at: str = "2026-05-20T12:00:00+00:00",
    zone: str = "chat",
) -> ReviewOutcome:
    return ReviewOutcome(
        review_id=review_id,
        reviewed_at=reviewed_at,
        verdict=verdict,
        confidence=confidence,
        per_reasoner=[
            ReasonerVerdict(
                reasoner_id="r1",
                verdict=verdict,
                confidence=confidence,
                reasoning="test",
            ),
        ],
        diagnostic="test diagnostic",
        zone=zone,
    )


# ── List endpoint ──────────────────────────────────────────────────


class TestList:
    def test_empty_audit_returns_empty(self, client):
        resp = client.get("/api/cp/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["total_scanned"] == 0
        assert data["filter_verdict"] is None

    def test_list_returns_reviews_newest_first(self, client):
        append_review(_outcome(
            "r1", reviewed_at="2026-05-18T00:00:00+00:00",
        ))
        append_review(_outcome(
            "r2", reviewed_at="2026-05-20T00:00:00+00:00",
        ))
        append_review(_outcome(
            "r3", reviewed_at="2026-05-19T00:00:00+00:00",
        ))
        resp = client.get("/api/cp/reviews")
        data = resp.json()
        assert data["count"] == 3
        ids = [r["review_id"] for r in data["reviews"]]
        assert ids == ["r2", "r3", "r1"]

    def test_filter_by_verdict(self, client):
        append_review(_outcome("safe1", verdict=Verdict.SAFE))
        append_review(_outcome("unsafe1", verdict=Verdict.UNSAFE))
        append_review(_outcome("disagree1", verdict=Verdict.DISAGREE))
        resp = client.get("/api/cp/reviews?verdict=disagree")
        data = resp.json()
        assert data["count"] == 1
        assert data["filter_verdict"] == "disagree"
        assert data["reviews"][0]["review_id"] == "disagree1"

    def test_invalid_verdict_returns_400(self, client):
        resp = client.get("/api/cp/reviews?verdict=bogus")
        assert resp.status_code == 400
        # Error message mentions valid options
        assert "valid" in resp.json()["detail"].lower()

    def test_limit_honored(self, client):
        for i in range(20):
            append_review(_outcome(
                f"r{i:02d}",
                reviewed_at=f"2026-05-{1 + i:02d}T00:00:00+00:00",
            ))
        resp = client.get("/api/cp/reviews?limit=5")
        data = resp.json()
        assert data["count"] == 5

    def test_invalid_limit_returns_422(self, client):
        # Pydantic Query validation catches out-of-range values
        resp = client.get("/api/cp/reviews?limit=0")
        assert resp.status_code == 422


# ── Detail endpoint ────────────────────────────────────────────────


class TestDetail:
    def test_get_known_review(self, client):
        append_review(_outcome("specific-id-123"))
        resp = client.get("/api/cp/reviews/specific-id-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["review_id"] == "specific-id-123"
        assert data["verdict"] == "safe"

    def test_get_unknown_returns_404(self, client):
        append_review(_outcome("known"))
        resp = client.get("/api/cp/reviews/unknown")
        assert resp.status_code == 404


# ── Summary endpoint ───────────────────────────────────────────────


class TestSummary:
    def test_empty_summary(self, client):
        resp = client.get("/api/cp/reviews/stats/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        # All five verdict counters present even when zero
        assert set(data["by_verdict"].keys()) == {
            "safe", "unsafe", "uncertain", "disagree", "disabled",
        }
        for count in data["by_verdict"].values():
            assert count == 0

    def test_summary_counts_by_verdict(self, client):
        for i in range(3):
            append_review(_outcome(f"safe{i}", verdict=Verdict.SAFE))
        append_review(_outcome("unsafe1", verdict=Verdict.UNSAFE))
        append_review(_outcome("disagree1", verdict=Verdict.DISAGREE))
        resp = client.get("/api/cp/reviews/stats/summary")
        data = resp.json()
        assert data["total"] == 5
        assert data["by_verdict"]["safe"] == 3
        assert data["by_verdict"]["unsafe"] == 1
        assert data["by_verdict"]["disagree"] == 1
        assert data["by_verdict"]["uncertain"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
