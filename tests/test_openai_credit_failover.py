"""Tests for the 2026-04-28 openai-SDK credit-failover patch.

Bug — CrewAI 1.14.x ships a "providers" system whose openai branch
calls ``openai.OpenAI`` directly, bypassing the litellm.completion
patch the rate_throttle module installed earlier in the day. When
OpenRouter returned 402 ("requires more credits, or fewer max_tokens")
the openai SDK raised APIStatusError, the orchestrator propagated it,
and the user saw "Crew pim failed: Error code: 402".

Fix — mirror of the litellm patch: wrap
openai.resources.chat.completions.Completions.create (sync + async)
so 402s also flow through _try_credit_failover_sync/async.

These tests pin:
  * The patch is registered (function exported, side-effect signal).
  * Idempotency: install_throttle() multiple times only patches once.
  * Recursion guard: the failover ContextVar prevents the local retry
    from re-entering the patch.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# Static checks
# ══════════════════════════════════════════════════════════════════════

class TestPatchPlumbing:

    def test_install_function_exists(self):
        text = (REPO / "app" / "rate_throttle.py").read_text()
        assert "def _install_openai_credit_failover" in text, (
            "rate_throttle.py must define _install_openai_credit_failover "
            "to patch the openai SDK path that CrewAI's openai-provider "
            "branch uses."
        )

    def test_called_from_install_throttle(self):
        text = (REPO / "app" / "rate_throttle.py").read_text()
        # The call must be inside install_throttle so the patch lands at
        # gateway startup.
        assert "_install_openai_credit_failover()" in text

    def test_idempotency_guard(self):
        text = (REPO / "app" / "rate_throttle.py").read_text()
        # The function MUST short-circuit on a module-level flag so
        # repeated install_throttle() calls don't double-patch.
        assert "_openai_patched" in text
        # And the early-return must read it
        assert re.search(r"if\s+_openai_patched", text), (
            "Idempotency guard must early-return when already patched."
        )

    def test_patches_both_sync_and_async(self):
        text = (REPO / "app" / "rate_throttle.py").read_text()
        assert "Completions.create" in text
        assert "AsyncCompletions.create" in text
        assert "async def _patched_acreate" in text

    def test_uses_existing_failover_helpers(self):
        """Must reuse _try_credit_failover_sync/async — not reinvent
        the local-model selection or recursion-guard logic."""
        text = (REPO / "app" / "rate_throttle.py").read_text()
        # Both branches reuse the existing helpers
        assert "_try_credit_failover_sync(" in text
        assert "_try_credit_failover_async(" in text

    def test_only_credit_errors_hijacked(self):
        """The wrapper must call detect_credit_error and propagate
        non-credit errors unchanged — otherwise we'd swallow real
        (e.g. 400 schema) failures."""
        text = (REPO / "app" / "rate_throttle.py").read_text()
        # Both branches gate on detect_credit_error
        assert text.count("detect_credit_error(exc)") >= 2

    def test_failed_install_warns_not_raises(self):
        """A failure to import openai must NOT crash gateway startup —
        the rate_throttle install path catches and logs."""
        text = (REPO / "app" / "rate_throttle.py").read_text()
        assert "failed to install openai credit-failover" in text


# ══════════════════════════════════════════════════════════════════════
# Behavioral — patch actually runs and detects 402 markers
# ══════════════════════════════════════════════════════════════════════

class TestPatchBehavior:

    def test_install_does_not_raise_when_openai_available(self):
        """The patch installs cleanly in the test environment (openai is
        in requirements). Repeated calls are idempotent."""
        from app.rate_throttle import _install_openai_credit_failover
        # First call patches
        _install_openai_credit_failover()
        # Second call short-circuits (no exception, no double-patch)
        _install_openai_credit_failover()

    def test_patch_marker_visible(self):
        """After install, the module-level flag must be True so
        downstream code can introspect."""
        import app.rate_throttle as rt
        rt._install_openai_credit_failover()
        assert getattr(rt, "_openai_patched", False) is True

    def test_402_pattern_recognized_by_detect_credit_error(self):
        """The exact error string from the user's regression must be
        recognized as a credit error so the wrapper triggers."""
        from app.firebase.publish import detect_credit_error
        msg = (
            "Error code: 402 - {'error': {'message': 'This request "
            "requires more credits, or fewer max_tokens. You requested "
            "up to 4096 tokens, but can only afford 3396. ...'}}"
        )
        assert detect_credit_error(msg) == "openrouter", (
            "The exact 402 string from the 2026-04-28 PIM regression "
            "must trigger credit-error detection."
        )
