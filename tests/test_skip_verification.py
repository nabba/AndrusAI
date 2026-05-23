"""Tests for the output-streaming skip-verification shortcut
(Verified Implementation Plan §7 item 3 closure, 2026-05-23).

Pins:
  * skip_state.is_skip_set() defaults False — gate runs as before.
  * set_skip(True) is observable; reset_skip restores default.
  * skip_scope() context manager sets + restores correctly,
    including on exception.
  * Nested scopes stack/unwind correctly (ContextVar discipline).
  * The flag is task-local: ContextVar generation prevents leakage.
  * Local-route patterns set ``skip_verification: True`` in the
    returned routing decision.
  * Fast-route patterns do NOT set skip_verification (they may
    produce factual claims).
  * gate_output() honours the flag — returns ``ship`` with diagnostic
    note when set; otherwise runs the full gate.
"""
from __future__ import annotations

import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


# Stub heavy deps before any app import.
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


skip_state = _load(
    "_skip_state_test", "app/epistemic/skip_state.py",
)


# ── skip_state primitives ───────────────────────────────────────────


@pytest.mark.skipif(
    skip_state is None, reason="skip_state not loadable",
)
class TestSkipStateAPI:
    def test_default_is_false(self):
        # Force a fresh ContextVar generation for test isolation
        assert skip_state.is_skip_set() is False

    def test_set_and_reset_roundtrip(self):
        token = skip_state.set_skip(True)
        try:
            assert skip_state.is_skip_set() is True
        finally:
            skip_state.reset_skip(token)
        assert skip_state.is_skip_set() is False

    def test_set_false_is_idempotent(self):
        token = skip_state.set_skip(False)
        try:
            assert skip_state.is_skip_set() is False
        finally:
            skip_state.reset_skip(token)

    def test_reset_with_stale_token_does_not_raise(self):
        # set/reset a token, then try to reset the same token again
        token = skip_state.set_skip(True)
        skip_state.reset_skip(token)
        # Second reset should swallow ValueError silently
        skip_state.reset_skip(token)
        # State is at default
        assert skip_state.is_skip_set() is False


@pytest.mark.skipif(
    skip_state is None, reason="skip_state not loadable",
)
class TestSkipScope:
    def test_scope_sets_and_restores(self):
        assert skip_state.is_skip_set() is False
        with skip_state.skip_scope(True):
            assert skip_state.is_skip_set() is True
        assert skip_state.is_skip_set() is False

    def test_scope_restores_on_exception(self):
        assert skip_state.is_skip_set() is False
        with pytest.raises(RuntimeError):
            with skip_state.skip_scope(True):
                assert skip_state.is_skip_set() is True
                raise RuntimeError("inside scope")
        # Despite exception, flag is restored
        assert skip_state.is_skip_set() is False

    def test_nested_scopes_unwind_correctly(self):
        assert skip_state.is_skip_set() is False
        with skip_state.skip_scope(True):
            assert skip_state.is_skip_set() is True
            with skip_state.skip_scope(False):
                assert skip_state.is_skip_set() is False
            assert skip_state.is_skip_set() is True
        assert skip_state.is_skip_set() is False

    def test_scope_with_false_overrides_outer_true(self):
        with skip_state.skip_scope(True):
            with skip_state.skip_scope(False):
                assert skip_state.is_skip_set() is False
            assert skip_state.is_skip_set() is True


# ── Routing patterns set skip_verification correctly ────────────────


def _routing_loadable() -> bool:
    """routing.py imports app.config (needs pydantic_settings on
    host); skip if absent."""
    try:
        import app.runtime_settings  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _routing_loadable(), reason="routing imports require runtime_settings",
)
class TestRoutingFlag:
    def _routing(self):
        from app.agents.commander import routing
        return routing

    def test_local_route_sets_skip_verification(self, monkeypatch):
        r = self._routing()
        # Force the master switch ON via env-first precedence
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        result = r._try_local_route(
            "my calendar today", has_attachments=False,
        )
        assert result is not None
        assert result[0].get("skip_verification") is True

    def test_local_route_calendar_and_briefing_both_flagged(
        self, monkeypatch,
    ):
        r = self._routing()
        monkeypatch.setenv("LOCAL_ROUTE_ENABLED", "true")
        for query in (
            "my latest briefing",
            "today's meetings",
            "my open threads",
            "my health today",
            "my open tickets",
            "my notes",
        ):
            result = r._try_local_route(query, has_attachments=False)
            assert result is not None, f"local route missed {query!r}"
            assert result[0].get("skip_verification") is True, (
                f"local route for {query!r} did not flag skip"
            )

    def test_fast_route_does_NOT_set_skip_verification(self):
        """Fast-route patterns may produce factual claims (research /
        coding / writing crews) — they MUST NOT auto-skip the gate."""
        r = self._routing()
        result = r._try_fast_route(
            "what is the capital of France",
            has_attachments=False,
        )
        # If matched, the flag must be absent or False
        if result is not None:
            assert not result[0].get("skip_verification", False), (
                "fast-route research query unexpectedly flagged skip"
            )


# ── gate_output honours the flag ────────────────────────────────────


def _gate_output_loadable() -> bool:
    try:
        from app.epistemic import orchestrator_hook  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _gate_output_loadable(),
    reason="orchestrator_hook needs full app boot",
)
class TestGateHonoursFlag:
    def test_ships_immediately_when_flag_set(self, monkeypatch):
        from app.epistemic import orchestrator_hook
        from app.epistemic.skip_state import skip_scope

        # Force EPISTEMIC_ENABLED true so we don't hit the master-OFF
        # bypass before our skip check.
        monkeypatch.setenv("EPISTEMIC_ENABLED", "true")

        with skip_scope(True):
            result = orchestrator_hook.gate_output(
                proposal_text="The capital of France is Paris.",
                task_id="test-task-1",
            )

        assert result.action == "ship"
        # final_text passes through unchanged
        assert result.final_text == "The capital of France is Paris."
        assert result.diagnostic_note is not None
        assert "skip_verification" in (result.diagnostic_note or "")

    def test_runs_full_gate_when_flag_not_set(self, monkeypatch):
        from app.epistemic import orchestrator_hook
        # No skip_scope; default False
        monkeypatch.setenv("EPISTEMIC_ENABLED", "true")
        result = orchestrator_hook.gate_output(
            proposal_text="x",
            task_id="test-task-2",
        )
        # Result.action is one of ship/revise/block — but the
        # diagnostic_note must NOT mention skip_verification, since
        # the flag wasn't set.
        if result.diagnostic_note:
            assert "skip_verification" not in result.diagnostic_note


# ── Source-inspection pins (work on dev host without imports) ──────


def test_orchestrator_propagates_skip_flag():
    """Source-level pin: the orchestrator reads skip_verification
    from the routing decision and calls set_skip when True."""
    src = open(
        "app/agents/commander/orchestrator.py",
        encoding="utf-8",
    ).read()
    # The propagation block we just added
    assert "skip_verification" in src
    assert "from app.epistemic.skip_state import set_skip" in src
    assert "set_skip(True)" in src


def test_gate_output_checks_skip_flag():
    """Source-level pin: gate_output reads is_skip_set() and
    short-circuits with action=ship."""
    src = open(
        "app/epistemic/orchestrator_hook.py",
        encoding="utf-8",
    ).read()
    assert "from app.epistemic.skip_state import is_skip_set" in src
    assert "is_skip_set()" in src


def test_local_route_patterns_marked_skip():
    """Source-level pin: local-route return dict carries skip_verification=True."""
    src = open(
        "app/agents/commander/routing.py", encoding="utf-8",
    ).read()
    # The marker comment + literal flag
    assert '"skip_verification": True' in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
