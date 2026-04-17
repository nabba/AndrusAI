"""Tests for app/tools/browser_tools.py and app/tools/session_search_tool.py."""
from unittest.mock import MagicMock, patch

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()


class TestBrowserTools:
    def test_returns_empty_when_playwright_missing(self, monkeypatch):
        import importlib
        # Pretend playwright import fails
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *a, **kw):
            if name == "playwright":
                raise ImportError("playwright not installed")
            return real_import(name, *a, **kw)

        # Just force the ImportError path by monkey-patching sys.modules
        monkeypatch.setitem(__import__("sys").modules, "playwright", None)
        from app.tools import browser_tools
        importlib.reload(browser_tools)
        tools = browser_tools.create_browser_tools()
        assert tools == []

    def test_validate_url_blocks_localhost(self):
        from app.tools.browser_tools import _validate_url
        ok, reason = _validate_url("https://localhost:8080/x")
        assert ok is False

    def test_validate_url_allows_public(self):
        from app.tools.browser_tools import _validate_url
        ok, reason = _validate_url("https://example.com")
        assert ok is True


class TestSessionSearchTool:
    def test_create_session_search_tools_returns_one_tool(self):
        pytest.importorskip("crewai")
        from app.tools.session_search_tool import create_session_search_tools
        tools = create_session_search_tools()
        assert len(tools) == 1
        assert tools[0].name == "session_search"

    def test_tool_run_calls_search_messages(self, monkeypatch):
        pytest.importorskip("crewai")
        from app.tools import session_search_tool

        captured = []

        def fake_search(query, limit=10):
            captured.append((query, limit))
            return [
                {"role": "user", "content_snippet": "hello world", "ts": "2026-01-01T00:00:00+00:00"},
                {"role": "assistant", "content_snippet": ">>>hello<<< back", "ts": "2026-01-01T00:01:00+00:00"},
            ]

        monkeypatch.setattr("app.conversation_store.search_messages", fake_search)

        tools = session_search_tool.create_session_search_tools()
        out = tools[0]._run(query="hello", limit=5)
        assert "2 matches" in out
        assert "hello world" in out
        assert ">>>hello<<<" in out
        assert captured == [("hello", 5)]

    def test_tool_run_handles_no_results(self, monkeypatch):
        pytest.importorskip("crewai")
        from app.tools import session_search_tool
        monkeypatch.setattr("app.conversation_store.search_messages",
                            lambda q, limit=10: [])
        tools = session_search_tool.create_session_search_tools()
        out = tools[0]._run(query="missing")
        assert "No past messages" in out

    def test_tool_run_clamps_limit(self, monkeypatch):
        pytest.importorskip("crewai")
        from app.tools import session_search_tool

        captured = []

        def fake_search(query, limit=10):
            captured.append(limit)
            return []

        monkeypatch.setattr("app.conversation_store.search_messages", fake_search)
        tools = session_search_tool.create_session_search_tools()
        # Limit above max should be clamped to 50
        tools[0]._run(query="x", limit=999)
        # Limit below min should be clamped to 1
        tools[0]._run(query="x", limit=0)
        assert captured == [50, 1]
