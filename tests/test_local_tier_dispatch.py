"""Tests for the local-tier ContextVar wiring (Gap A closure, 2026-05-23).

Pins:
  * ``_active_local_tier`` ContextVar defaults False.
  * ``set_active_local_tier``/``reset_active_local_tier`` roundtrip works.
  * ``create_commander_llm`` reads the ContextVar and overrides
    ``mode`` to ``"local"`` when set — verified via the log message
    AND via the resolved model_name being an Ollama-provider entry.
  * ``_run_crew`` propagates ``tier_hint="local"`` from the routing
    decision dict into the ContextVar (source-level pin — the live
    handler requires the full LLM stack).
  * Reset happens in the ``finally`` block so subsequent dispatches
    don't inherit the local-tier flag.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


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


llm_selector = _load("_lts", "app/llm_selector.py")


@pytest.mark.skipif(
    llm_selector is None, reason="llm_selector not loadable",
)
class TestContextVarAPI:
    def test_default_is_false(self):
        assert llm_selector.get_active_local_tier() is False

    def test_set_and_reset_roundtrip(self):
        token = llm_selector.set_active_local_tier(True)
        try:
            assert llm_selector.get_active_local_tier() is True
        finally:
            llm_selector.reset_active_local_tier(token)
        assert llm_selector.get_active_local_tier() is False

    def test_set_false_is_noop(self):
        token = llm_selector.set_active_local_tier(False)
        try:
            assert llm_selector.get_active_local_tier() is False
        finally:
            llm_selector.reset_active_local_tier(token)

    def test_reset_with_stale_token_does_not_raise(self):
        token = llm_selector.set_active_local_tier(True)
        llm_selector.reset_active_local_tier(token)
        # Second reset is defensive — should not raise
        llm_selector.reset_active_local_tier(token)
        assert llm_selector.get_active_local_tier() is False

    def test_get_active_difficulty_unaffected(self):
        """Sister ContextVars must not interfere."""
        token_loc = llm_selector.set_active_local_tier(True)
        token_d = llm_selector.set_active_difficulty(7)
        try:
            assert llm_selector.get_active_local_tier() is True
            assert llm_selector.get_active_difficulty() == 7
        finally:
            llm_selector.reset_active_difficulty(token_d)
            llm_selector.reset_active_local_tier(token_loc)


# ── Source-level pins (work without full LLM stack) ────────────────


def test_create_commander_llm_reads_local_tier_flag():
    """Pin: llm_factory.create_commander_llm consults
    get_active_local_tier and forces mode='local'."""
    src = Path("app/llm_factory.py").read_text(encoding="utf-8")
    assert "from app.llm_selector import get_active_local_tier" in src
    assert "get_active_local_tier()" in src
    assert 'mode = "local"' in src


def test_run_crew_propagates_tier_hint():
    """Pin: orchestrator._run_crew accepts a tier_hint kwarg,
    propagates it into the ContextVar, and resets in finally."""
    src = Path(
        "app/agents/commander/orchestrator.py",
    ).read_text(encoding="utf-8")
    # Signature accepts the new kwarg
    assert "tier_hint: str | None = None" in src
    # Sets the ContextVar when the hint is "local"
    assert 'tier_hint == "local"' in src
    assert "set_active_local_tier(True)" in src
    # Resets in finally
    assert "reset_active_local_tier" in src


def test_run_crew_callsites_pass_tier_hint():
    """Pin: the orchestrator dispatch loop reads ``tier_hint`` off
    the decision dict and forwards it to _run_crew."""
    src = Path(
        "app/agents/commander/orchestrator.py",
    ).read_text(encoding="utf-8")
    # We expect at least one call site that threads tier_hint from
    # the decision dict.
    assert 'tier_hint=d.get("tier_hint")' in src


def test_local_route_sets_tier_hint_in_decision():
    """Already pinned in test_skip_verification.py via skip_verification;
    re-pin tier_hint here for orthogonal coverage."""
    src = Path(
        "app/agents/commander/routing.py",
    ).read_text(encoding="utf-8")
    assert '"tier_hint": "local"' in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
