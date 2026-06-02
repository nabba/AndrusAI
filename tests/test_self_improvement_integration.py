"""Integration tests for the self-improvement error-handler hook chain.

Verifies that an error reported through error_handler dispatches to the
ON_ERROR lifecycle hook chain end-to-end:

  Fix 1: error_handler.report_error invokes the ON_ERROR hook chain

NOTE: The legacy evolution stack (experiment_runner / evolution /
meta_evolution) that the other fixes in this suite exercised was removed in
the 2026-06 evolution-consolidation refactor; those tests were dropped with it.
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Fix 1: error_handler invokes ON_ERROR hook chain ────────────────────────

class TestErrorHandlerHookIntegration:
    """Fix 1: report_error() must dispatch to the lifecycle hook chain."""

    def test_report_error_invokes_hook_registry(self):
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_registry = MagicMock()
            mock_get_reg.return_value = mock_registry
            report_error(ErrorCategory.LOGIC, "test error", context={"crew": "test"})
            assert mock_registry.execute.called
            args, kwargs = mock_registry.execute.call_args
            from app.lifecycle_hooks import HookPoint
            assert args[0] == HookPoint.ON_ERROR

    def test_report_error_passes_context_to_hook(self):
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_registry = MagicMock()
            mock_get_reg.return_value = mock_registry
            report_error(
                ErrorCategory.DATA,
                "parse failed",
                context={"crew": "researcher", "agent_id": "r1"},
            )
            ctx = mock_registry.execute.call_args[0][1]
            assert "parse failed" in ctx.errors[0]
            assert ctx.metadata.get("category") == "data"

    def test_report_error_prevents_reentry(self):
        """A hook that calls report_error must not cause infinite recursion."""
        from app.error_handler import report_error, ErrorCategory
        call_count = [0]

        def recursive_hook(ctx):
            call_count[0] += 1
            # The hook itself reports an error — must not loop
            report_error(ErrorCategory.LOGIC, "hook reported")
            return ctx

        # Patch registry to call our recursive hook
        from app.lifecycle_hooks import HookPoint, HookContext
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.execute = lambda hp, ctx: recursive_hook(ctx)
            mock_get_reg.return_value = mock_reg
            report_error(ErrorCategory.LOGIC, "outer")
            # The outer call dispatches once; the inner call's reentry guard prevents
            # a second dispatch. So hook is called exactly once.
            assert call_count[0] == 1

    def test_report_error_never_crashes_on_hook_failure(self):
        """Hook failures must not propagate to the caller."""
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.execute.side_effect = Exception("hook crashed")
            mock_get_reg.return_value = mock_reg
            # Should not raise
            report_error(ErrorCategory.SYSTEM, "test")


# ── End-to-end: full chain ──────────────────────────────────────────────────

class TestEndToEndFixIntegration:
    """Verify the fixes work together when an error fires through the chain."""

    def test_error_triggers_failure_classifier_via_hook_chain(self):
        """Fix 1 + failure_taxonomy: an error reported through error_handler
        must produce a MAST classification in metadata."""
        # Use the real registry (it has the failure_classifier hook registered
        # at startup). Fire an error and verify classification happens.
        from app.lifecycle_hooks import get_registry, HookPoint
        from app.error_handler import report_error, ErrorCategory

        # Register a sniffer hook to capture the dispatched context
        captured = {}

        def sniffer(ctx):
            # Wait for failure_classifier (priority 3) to run before capturing
            captured["metadata"] = dict(ctx.metadata)
            captured["errors"] = list(ctx.errors)
            return ctx

        get_registry().register(
            "test_sniffer",
            HookPoint.ON_ERROR,
            sniffer,
            priority=99,  # Run after all classifiers
            description="Test sniffer for integration test",
        )
        try:
            report_error(
                ErrorCategory.LOGIC,
                "hallucination detected: fabricated source citation",
                context={"crew": "researcher", "agent_id": "r1"},
            )
            # The failure_classifier hook (priority 3, immutable) must have
            # populated _failure_classification in metadata
            classification = captured.get("metadata", {}).get("_failure_classification")
            assert classification is not None, (
                f"No MAST classification produced. Captured: {captured}"
            )
            # The error mentions "hallucination" — should classify as such
            assert classification["agent_mode"] == "hallucination"
        finally:
            try:
                get_registry().unregister("test_sniffer", HookPoint.ON_ERROR)
            except Exception:
                pass
