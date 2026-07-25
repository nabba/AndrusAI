"""The search failure chain must distinguish "no matches" from "backend broke".

Closes the 2026-07-25 UNKNOWN (`reports/GATE_DIAGNOSIS_2026-07-25.md`,
Addendum 10): `searxng:no_results` was logged while SearXNG demonstrably
worked, and no reason had been recorded anywhere, so the question could not be
answered retrospectively.

Every test here fails on the pre-2026-07-26 code, which inferred the label from
an empty return value alone.
"""

from __future__ import annotations

import pytest

web_search = pytest.importorskip("app.tools.web_search")


@pytest.fixture(autouse=True)
def _clean_diagnostics():
    web_search._last_backend_errors = {}
    yield
    web_search._last_backend_errors = {}


def test_zero_hits_reads_no_results():
    """A backend that genuinely matched nothing keeps the honest label."""
    assert web_search._chain_label("searxng") == "searxng:no_results"


def test_recorded_exception_reads_error_with_cause():
    """A raised backend must NOT be reported as 'no results'."""
    web_search._record_backend_error(
        "searxng", TimeoutError("HTTPConnectionPool: read timed out"),
    )
    label = web_search._chain_label("searxng")
    assert label != "searxng:no_results"
    assert label.startswith("searxng:error(")
    assert "TimeoutError" in label
    assert "read timed out" in label


def test_reason_is_length_capped():
    web_search._record_backend_error("ddg", RuntimeError("x" * 5000))
    assert len(web_search._last_backend_errors["ddg"]) <= 200


def test_each_backend_records_its_own_reason():
    web_search._record_backend_error("brave", RuntimeError("brave boom"))
    web_search._record_backend_error("ddg", ValueError("ddg boom"))
    assert "brave boom" in web_search._chain_label("brave")
    assert "ddg boom" in web_search._chain_label("ddg")
    # searxng never failed, so it must not inherit a sibling's reason.
    assert web_search._chain_label("searxng") == "searxng:no_results"


def test_searxng_transport_failure_surfaces_in_chain(monkeypatch):
    """End-to-end: a broken SearXNG names its cause in the failure chain."""
    monkeypatch.setattr(
        web_search, "_search_brave_raw", lambda q, c: [],
    )
    monkeypatch.setattr(
        web_search, "_search_duckduckgo", lambda q, c: [],
    )

    def _boom(query, count):
        try:
            raise ConnectionError("searxng unreachable")
        except ConnectionError as exc:
            web_search._record_backend_error("searxng", exc)
            return []

    monkeypatch.setattr(web_search, "_search_searxng", _boom)

    assert web_search.search_brave("anything", count=3) == []
    chain = web_search.get_search_status()["last_failure_chain"]
    searxng_labels = [c for c in chain if c.startswith("searxng")]
    assert searxng_labels, f"searxng absent from chain: {chain}"
    assert "searxng unreachable" in searxng_labels[0], searxng_labels


def test_status_exposes_backend_errors():
    web_search._record_backend_error("searxng", RuntimeError("nope"))
    status = web_search.get_search_status()
    assert "last_backend_errors" in status
    assert "nope" in status["last_backend_errors"]["searxng"]


def test_errors_reset_between_searches(monkeypatch):
    """A stale reason must not label a later, healthy call."""
    web_search._record_backend_error("searxng", RuntimeError("old failure"))
    monkeypatch.setattr(
        web_search, "_search_brave_raw", lambda q, c: [
            {"title": "t", "url": "https://example.org/a", "description": "d"},
        ],
    )
    monkeypatch.setattr(
        web_search, "_validate_results",
        lambda q, r, backend: (r, 0),
    )
    web_search.search_brave("anything", count=1)
    assert web_search._last_backend_errors == {}
