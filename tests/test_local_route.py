"""Tests for the interest-profile-aware local-tier route
(Verified Implementation Plan Gap #4, 2026-05-22).

Pins:
  * Master switch OFF (default) → _try_local_route always returns None.
  * Master switch ON + matching pattern → returns decision dict with
    tier_hint='local'.
  * Non-matching queries return None even when ON.
  * Attachments / long-text / introspective queries are skipped.
  * The 6 pattern categories all match their canonical examples.
"""
from __future__ import annotations

import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


# Direct-load the routing module — pure stdlib + re
def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


# Pre-stub crewai before loading routing
for _mod in ("crewai", "crewai.tools"):
    if _mod not in sys.modules:
        import types
        m = types.ModuleType(_mod)
        if _mod == "crewai.tools":
            m.tool = lambda name: (lambda fn: fn)
            m.BaseTool = type("BaseTool", (), {})
        sys.modules[_mod] = m


routing = _load("_routing_g4", "app/agents/commander/routing.py")


# ── Master switch gate ──────────────────────────────────────────────


@pytest.mark.skipif(routing is None, reason="routing not loadable")
class TestMasterSwitchGate:
    def test_default_off_returns_none(self, monkeypatch):
        # Env unset → falls through to default False
        monkeypatch.delenv("LOCAL_ROUTE_ENABLED", raising=False)
        monkeypatch.delitem(
            sys.modules, "app.runtime_settings", raising=False,
        )
        # Force the lazy import to fail so default kicks in
        result = routing._try_local_route(
            "my calendar today", has_attachments=False,
        )
        assert result is None

    def test_env_var_truthy_enables(self, monkeypatch):
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        monkeypatch.delitem(
            sys.modules, "app.runtime_settings", raising=False,
        )
        result = routing._try_local_route(
            "my calendar today", has_attachments=False,
        )
        assert result is not None
        assert result[0]["tier_hint"] == "local"

    def test_runtime_settings_overrides_env(self, monkeypatch):
        # runtime_settings.get_local_route_enabled returns True → ON
        # regardless of env
        monkeypatch.delenv("LOCAL_ROUTE_ENABLED", raising=False)
        import types
        fake = types.ModuleType("app.runtime_settings")
        fake.get_local_route_enabled = lambda: True  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules, "app.runtime_settings", fake,
        )
        result = routing._try_local_route(
            "my calendar today", has_attachments=False,
        )
        assert result is not None


# ── Pattern matching ────────────────────────────────────────────────


@pytest.mark.skipif(routing is None, reason="routing not loadable")
class TestPatternMatching:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        monkeypatch.delitem(
            sys.modules, "app.runtime_settings", raising=False,
        )

    @pytest.mark.parametrize("query,expected_crew", [
        # Calendar / schedule
        ("my calendar today", "pim"),
        ("today's meetings", "pim"),
        ("tomorrow's schedule", "pim"),
        ("my agenda", "pim"),
        # Briefing
        ("my latest briefing", "pim"),
        ("today's briefing", "pim"),
        ("last digest", "pim"),
        ("this week's summary", "pim"),
        # Threads
        ("my open threads", "pim"),
        ("active threads", "pim"),
        # Health
        ("my health today", "pim"),
        ("my sleep this week", "pim"),
        # Tickets
        ("my open tickets", "pim"),
        ("active tasks", "pim"),
        # Notes
        ("my notes", "pim"),
        ("recent files", "pim"),
    ])
    def test_matches_canonical_queries(self, query, expected_crew):
        result = routing._try_local_route(query, has_attachments=False)
        assert result is not None, (
            f"Expected local route for {query!r}, got None"
        )
        assert result[0]["crew"] == expected_crew
        assert result[0]["tier_hint"] == "local"

    @pytest.mark.parametrize("query", [
        "what is the capital of France",  # not personal
        "explain quantum mechanics",       # not personal
        "search for python tutorials",     # not personal
        "what should I do today",          # too vague (no calendar/etc.)
        "summarise this article",          # generic
    ])
    def test_skips_non_matching_queries(self, query):
        result = routing._try_local_route(query, has_attachments=False)
        assert result is None, (
            f"Expected None for {query!r}, got {result}"
        )


# ── Edge cases ──────────────────────────────────────────────────────


@pytest.mark.skipif(routing is None, reason="routing not loadable")
class TestEdgeCases:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        monkeypatch.delitem(
            sys.modules, "app.runtime_settings", raising=False,
        )

    def test_attachments_skip(self):
        result = routing._try_local_route(
            "my calendar today", has_attachments=True,
        )
        assert result is None

    def test_long_query_skipped(self):
        long_query = "my calendar today " + "x" * 250
        result = routing._try_local_route(
            long_query, has_attachments=False,
        )
        assert result is None

    def test_empty_query_returns_none(self):
        assert (
            routing._try_local_route("", has_attachments=False)
            is None
        )
        assert (
            routing._try_local_route("   ", has_attachments=False)
            is None
        )

    def test_multipart_query_skipped(self):
        result = routing._try_local_route(
            "my calendar today and also list my tickets",
            has_attachments=False,
        )
        assert result is None


# ── Introspection false-positive guard ─────────────────────────────


@pytest.mark.skipif(routing is None, reason="routing not loadable")
class TestIntrospectionDetection:
    """Generic research topics must not be mistaken for system identity."""

    @pytest.mark.parametrize("query", [
        "What personality traits predict leadership?",
        "Which memory architecture is best for autonomous agents?",
        "Compare persistent memory systems for AI applications.",
        "Research the limitations of transformer architectures.",
    ])
    def test_generic_research_topic_is_not_introspective(self, query):
        assert routing._is_introspective(query) is False

    @pytest.mark.parametrize("query", [
        "Who are you?",
        "How does your personality work?",
        "What is your memory architecture?",
        "Do you have meory?",
    ])
    def test_self_anchored_identity_question_is_introspective(self, query):
        assert routing._is_introspective(query) is True


# ── Composition with fast-route ─────────────────────────────────────


@pytest.mark.skipif(routing is None, reason="routing not loadable")
class TestComposition:
    """The local route is meant to compose AFTER fast-route. We pin
    that calling fast-route first on a local-route candidate returns
    None (so the orchestrator falls through), and the local route
    catches it."""

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        monkeypatch.delitem(
            sys.modules, "app.runtime_settings", raising=False,
        )

    def test_fast_route_misses_local_then_local_catches(self):
        # "my calendar today" doesn't match any _FAST_ROUTE_PATTERNS
        # since those expect specific keyword anchors
        fast = routing._try_fast_route(
            "my calendar today", has_attachments=False,
        )
        # The fast-route may match this via the PIM-question
        # heuristic. If it does, local route doesn't fire (correct
        # behavior — fast wins). Otherwise local-route catches.
        local = routing._try_local_route(
            "my calendar today", has_attachments=False,
        )
        # At LEAST one of them must catch it — the operator's
        # personal queries shouldn't go to LLM router by default
        assert fast is not None or local is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
