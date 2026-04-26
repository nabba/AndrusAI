"""
Tests for app/tools/web_search.py — verify the Brave → SearXNG → DDG cascade.

Each tier is mocked so the test never hits the real internet. The contract
the test enforces is the one the rest of the system relies on:
  - search_brave() always returns a list (possibly empty)
  - get_search_status() reflects which tier handled the most recent query
  - Brave 402 (quota exhausted) sets a backoff so subsequent calls skip Brave
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset web_search module state between tests so quota backoff and
    last-backend-used don't leak between cases."""
    from app.tools import web_search as ws
    ws._brave_quota_blocked_until = 0.0
    ws._last_backend_used = None
    ws._last_failure_chain = []
    yield


def _brave_response(status: int, items: list | None = None):
    resp = MagicMock()
    resp.status_code = status
    if status == 200:
        resp.raise_for_status = lambda: None
        resp.json = lambda: {
            "web": {
                "results": items or [
                    {"title": "Brave Hit", "url": "https://b.example", "description": "from brave"}
                ]
            }
        }
    else:
        resp.json = lambda: {}
    return resp


def _searxng_response(items: list | None = None):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = lambda: None
    resp.json = lambda: {
        "results": items or [
            {"title": "Sx Hit", "url": "https://s.example", "content": "from searxng"}
        ]
    }
    return resp


def test_brave_success_short_circuits_chain():
    """When Brave returns results, SearXNG and DDG are never called."""
    from app.tools import web_search as ws

    with patch.object(ws._session, "get", return_value=_brave_response(200)) as brave_get, \
         patch("requests.get") as searxng_get, \
         patch("requests.post") as ddg_post:
        results = ws.search_brave("test query", count=3)

    assert len(results) == 1
    assert results[0]["title"] == "Brave Hit"
    assert ws.get_search_status()["last_backend_used"] == "brave"
    assert searxng_get.called is False
    assert ddg_post.called is False


def test_brave_402_falls_through_to_searxng():
    """402 from Brave → quota backoff set, SearXNG called next."""
    from app.tools import web_search as ws

    with patch.object(ws._session, "get", return_value=_brave_response(402)), \
         patch("requests.get", return_value=_searxng_response()) as searxng_get, \
         patch("requests.post") as ddg_post:
        results = ws.search_brave("q")

    assert len(results) == 1
    assert results[0]["title"] == "Sx Hit"
    status = ws.get_search_status()
    assert status["last_backend_used"] == "searxng"
    assert "brave:quota" in status["last_failure_chain"]
    assert status["brave_quota_blocked_until"] is not None
    assert status["brave_quota_blocked_until"] > time.time()
    assert searxng_get.called is True
    assert ddg_post.called is False


def test_brave_backoff_skips_brave_on_subsequent_calls():
    """Once 402 sets the backoff, the next call must NOT touch Brave."""
    from app.tools import web_search as ws

    # First call — Brave 402s, falls through to SearXNG.
    with patch.object(ws._session, "get", return_value=_brave_response(402)), \
         patch("requests.get", return_value=_searxng_response()):
        ws.search_brave("first")

    assert ws._brave_blocked_now()

    # Second call — Brave should NOT be hit because we're in backoff.
    brave_calls = []

    def _record_brave(*args, **kwargs):
        brave_calls.append(args)
        return _brave_response(200)

    with patch.object(ws._session, "get", side_effect=_record_brave), \
         patch("requests.get", return_value=_searxng_response()):
        ws.search_brave("second")

    assert brave_calls == [], "Brave was called despite active quota backoff"
    assert ws.get_search_status()["last_backend_used"] == "searxng"


def test_all_backends_fail_returns_empty_list_with_chain():
    """When every tier fails, search_brave returns [] and the failure chain
    is recorded for the dashboard."""
    from app.tools import web_search as ws

    bad_resp = MagicMock()
    bad_resp.status_code = 500
    bad_resp.raise_for_status = MagicMock(side_effect=RuntimeError("boom"))

    ddg_resp = MagicMock()
    ddg_resp.raise_for_status = lambda: None
    ddg_resp.text = "<html><body><div>no results</div></body></html>"

    with patch.object(ws._session, "get", return_value=bad_resp), \
         patch("requests.get", return_value=bad_resp), \
         patch("requests.post", return_value=ddg_resp):
        results = ws.search_brave("nothing matches")

    assert results == []
    status = ws.get_search_status()
    assert status["last_backend_used"] is None
    assert len(status["last_failure_chain"]) == 3  # one entry per tier


def test_web_search_tool_wrapper_returns_no_results_string_when_empty():
    """The CrewAI @tool wrapper must keep its string contract."""
    from app.tools import web_search as ws

    bad_resp = MagicMock()
    bad_resp.status_code = 500
    bad_resp.raise_for_status = MagicMock(side_effect=RuntimeError("boom"))
    ddg_resp = MagicMock()
    ddg_resp.raise_for_status = lambda: None
    ddg_resp.text = "<html></html>"

    with patch.object(ws._session, "get", return_value=bad_resp), \
         patch("requests.get", return_value=bad_resp), \
         patch("requests.post", return_value=ddg_resp):
        out = ws.web_search.run("anything")

    assert out == "No results found."
