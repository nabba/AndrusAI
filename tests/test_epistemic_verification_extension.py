"""Tests for the verification-extension chain (2026-05-20).

Covers:
  * runtime_settings overlay (3 new switches + 3 thresholds + budget)
  * is_enabled() / is_blocking_mode_enabled() priority order
  * action precedence aggregator (_max_action)
  * zone resolver (default + FIFO eviction)
  * per-task retrieval budget tracker
  * claim-source consistency evaluator (5 cases)
  * retrieval-on-low-confidence evaluator (8 cases)
  * apply_verification_extension aggregator (4 cases)
  * gate_output end-to-end integration (5 cases)

Safety invariants pinned by these tests:
  * master switch off → bit-identical to pre-extension behaviour
  * evaluators only ESCALATE, never weaken the calibration verdict
  * any internal failure falls through to "no escalation"
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ── Stubs (mirror test_epistemic_phase7.py, defensive variant) ──────
# Only stub crewai when the real package isn't importable — preserves
# downstream tests like test_wiki_index_reconciler that need
# ``crewai.tools.BaseTool``. When stubbing IS needed, include BaseTool
# so the stub is shape-complete.
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    import crewai.tools as _real_crewai_tools  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})  # shape-complete stub
            sys.modules[_mod] = m

for _mod in ("langchain_anthropic", "docker"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)


from app import runtime_settings  # noqa: E402
from app.epistemic import is_enabled  # noqa: E402
from app.epistemic.biases import BiasMatch, Severity  # noqa: E402
from app.epistemic.calibration import CalibrationVerdict  # noqa: E402
from app.epistemic.orchestrator_hook import (  # noqa: E402
    is_blocking_mode_enabled,
)
from app.epistemic import verification_extension as ve  # noqa: E402


# ── Test helpers ─────────────────────────────────────────────────────


def _ship_verdict() -> CalibrationVerdict:
    return CalibrationVerdict(proceed=True, suggested_action="ship")


def _hedge_verdict() -> CalibrationVerdict:
    return CalibrationVerdict(
        proceed=True,
        suggested_action="hedge",
        biases_detected=(BiasMatch(
            bias_id="anchoring",
            matched_claim_ids=("c1",),
            severity=Severity.MEDIUM,
        ),),
        note_for_post_mortem="anchoring×1",
    )


def _verify_verdict() -> CalibrationVerdict:
    return CalibrationVerdict(
        proceed=True,
        suggested_action="verify",
        biases_detected=(BiasMatch(
            bias_id="inference_as_fact",
            matched_claim_ids=("c1",),
            severity=Severity.HIGH,
        ),),
        note_for_post_mortem="inference_as_fact×1",
    )


def _peer_review_verdict() -> CalibrationVerdict:
    return CalibrationVerdict(
        proceed=False,
        suggested_action="peer_review",
        biases_detected=(BiasMatch(
            bias_id="destructive_recommendation",
            matched_claim_ids=("c1",),
            severity=Severity.CRITICAL,
        ),),
        note_for_post_mortem="destructive_recommendation×1",
    )


def _reset_runtime_settings() -> None:
    """Force a clean read on next access."""
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    """Inject a fully-loaded cache without touching disk."""
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# Runtime-settings overlay
# ============================================================================


class TestRuntimeSettingsOverlay(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_epistemic_enabled_override_defaults_none(self):
        with _patch_runtime_settings():
            self.assertIsNone(
                runtime_settings.get_epistemic_enabled_override(),
            )

    def test_epistemic_enabled_override_set_and_read(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            runtime_settings.set_epistemic_enabled_override(True)
            self.assertTrue(runtime_settings.get_epistemic_enabled_override())
            runtime_settings.set_epistemic_enabled_override(False)
            self.assertFalse(runtime_settings.get_epistemic_enabled_override())
            runtime_settings.set_epistemic_enabled_override(None)
            self.assertIsNone(
                runtime_settings.get_epistemic_enabled_override(),
            )

    def test_blocking_mode_override_set_and_read(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            runtime_settings.set_epistemic_blocking_mode_override(True)
            self.assertTrue(
                runtime_settings.get_epistemic_blocking_mode_override(),
            )
            runtime_settings.set_epistemic_blocking_mode_override(None)
            self.assertIsNone(
                runtime_settings.get_epistemic_blocking_mode_override(),
            )

    def test_verification_extension_defaults_off(self):
        with _patch_runtime_settings():
            self.assertFalse(
                runtime_settings.get_verification_extension_enabled(),
            )

    def test_threshold_defaults_match_zone(self):
        with _patch_runtime_settings():
            self.assertAlmostEqual(
                runtime_settings.get_verification_threshold("chat"), 0.60,
            )
            self.assertAlmostEqual(
                runtime_settings.get_verification_threshold("autonomous"), 0.90,
            )
            self.assertAlmostEqual(
                runtime_settings.get_verification_threshold("financial"), 0.95,
            )

    def test_threshold_unknown_zone_falls_back_to_chat(self):
        with _patch_runtime_settings():
            self.assertAlmostEqual(
                runtime_settings.get_verification_threshold("bogus"), 0.60,
            )

    def test_set_threshold_rejects_unknown_zone(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_verification_threshold("bogus", 0.5)

    def test_set_threshold_rejects_out_of_range(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_verification_threshold("chat", -0.1)
            with self.assertRaises(ValueError):
                runtime_settings.set_verification_threshold("chat", 1.5)

    def test_retrieval_budget_default_is_one(self):
        with _patch_runtime_settings():
            self.assertEqual(
                runtime_settings.get_verification_retrieval_budget_per_task(),
                1,
            )

    def test_retrieval_budget_rejects_negative_and_overrange(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_verification_retrieval_budget_per_task(-1)
            with self.assertRaises(ValueError):
                runtime_settings.set_verification_retrieval_budget_per_task(11)


# ============================================================================
# is_enabled() and is_blocking_mode_enabled() priority order
# ============================================================================


class TestIsEnabledPriority(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_env_var_works_when_no_override(self):
        with _patch_runtime_settings(epistemic_enabled_override=None), \
                patch.dict(os.environ, {"EPISTEMIC_ENABLED": "true"}):
            self.assertTrue(is_enabled())
        with _patch_runtime_settings(epistemic_enabled_override=None), \
                patch.dict(os.environ, {"EPISTEMIC_ENABLED": ""}):
            self.assertFalse(is_enabled())

    def test_override_true_wins_over_env_false(self):
        with _patch_runtime_settings(epistemic_enabled_override=True), \
                patch.dict(os.environ, {"EPISTEMIC_ENABLED": ""}):
            self.assertTrue(is_enabled())

    def test_override_false_wins_over_env_true(self):
        with _patch_runtime_settings(epistemic_enabled_override=False), \
                patch.dict(os.environ, {"EPISTEMIC_ENABLED": "true"}):
            self.assertFalse(is_enabled())


class TestBlockingModePriority(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_env_var_works_when_no_override(self):
        with _patch_runtime_settings(
                epistemic_blocking_mode_override=None), \
                patch.dict(os.environ, {"EPISTEMIC_BLOCKING_MODE": "true"}):
            self.assertTrue(is_blocking_mode_enabled())
        with _patch_runtime_settings(
                epistemic_blocking_mode_override=None), \
                patch.dict(os.environ, {"EPISTEMIC_BLOCKING_MODE": ""}):
            self.assertFalse(is_blocking_mode_enabled())

    def test_override_true_wins_over_env(self):
        with _patch_runtime_settings(
                epistemic_blocking_mode_override=True), \
                patch.dict(os.environ, {"EPISTEMIC_BLOCKING_MODE": ""}):
            self.assertTrue(is_blocking_mode_enabled())

    def test_override_false_wins_over_env(self):
        with _patch_runtime_settings(
                epistemic_blocking_mode_override=False), \
                patch.dict(os.environ, {"EPISTEMIC_BLOCKING_MODE": "true"}):
            self.assertFalse(is_blocking_mode_enabled())


# ============================================================================
# Action precedence aggregator
# ============================================================================


class TestMaxAction(unittest.TestCase):
    def test_empty_list_returns_ship(self):
        self.assertEqual(ve._max_action([]), "ship")

    def test_picks_highest(self):
        self.assertEqual(
            ve._max_action(["ship", "hedge", "verify", "ship"]),
            "verify",
        )

    def test_peer_review_dominates(self):
        self.assertEqual(
            ve._max_action(["ship", "peer_review", "hedge"]),
            "peer_review",
        )

    def test_unknown_actions_treated_as_ship(self):
        # Defensive: an unrecognised action does not win precedence.
        self.assertEqual(
            ve._max_action(["bogus", "hedge", "another_bogus"]),
            "hedge",
        )

    def test_only_unknown_actions(self):
        # All unknowns → falls back to "ship" (escape hatch is the
        # default "ship" return value).
        self.assertEqual(ve._max_action(["bogus", "other"]), "ship")


# ============================================================================
# Zone resolver
# ============================================================================


class TestZoneResolver(unittest.TestCase):
    def setUp(self) -> None:
        ve.clear_zone_hints_for_tests()

    def test_default_is_chat(self):
        self.assertEqual(ve._resolve_zone("any-task-id"), "chat")

    def test_empty_task_id_is_chat(self):
        self.assertEqual(ve._resolve_zone(""), "chat")

    def test_registered_zone_returned(self):
        ve.register_zone_for_task("task-1", "autonomous")
        self.assertEqual(ve._resolve_zone("task-1"), "autonomous")

    def test_register_unknown_zone_raises(self):
        with self.assertRaises(ValueError):
            ve.register_zone_for_task("task-1", "bogus")

    def test_register_is_idempotent(self):
        ve.register_zone_for_task("task-1", "financial")
        ve.register_zone_for_task("task-1", "financial")
        self.assertEqual(ve._resolve_zone("task-1"), "financial")

    def test_register_can_overwrite(self):
        ve.register_zone_for_task("task-1", "chat")
        ve.register_zone_for_task("task-1", "financial")
        self.assertEqual(ve._resolve_zone("task-1"), "financial")


# ============================================================================
# Per-task retrieval budget
# ============================================================================


class TestRetrievalBudget(unittest.TestCase):
    def setUp(self) -> None:
        ve.clear_retrieval_budget_for_tests()

    def test_first_claim_succeeds(self):
        self.assertTrue(ve._claim_retrieval_budget("t1", 1))

    def test_second_claim_within_budget(self):
        self.assertTrue(ve._claim_retrieval_budget("t1", 2))
        self.assertTrue(ve._claim_retrieval_budget("t1", 2))
        self.assertFalse(ve._claim_retrieval_budget("t1", 2))

    def test_budget_zero_always_fails(self):
        self.assertFalse(ve._claim_retrieval_budget("t1", 0))

    def test_empty_task_id_always_fails(self):
        self.assertFalse(ve._claim_retrieval_budget("", 5))


# ============================================================================
# Claim-source consistency evaluator
# ============================================================================


class _FakeRegistry:
    """Drop-in replacement for SourceRegistry to keep tests
    deterministic and offline."""

    def __init__(self, sources=None):
        self._sources = sources or {}

    def get(self, topic, key="default"):
        return self._sources.get((topic, key)) or self._sources.get(
            (topic, "default"),
        )


class TestClaimSourceEvaluator(unittest.TestCase):
    def test_no_claims_returns_none(self):
        action, note = ve._evaluate_claim_source_consistency(
            "just chitchat with no numeric claims",
            threshold=0.6,
            registry=_FakeRegistry(),
        )
        self.assertIsNone(action)
        self.assertEqual(note, "")

    def test_claim_with_empty_topic_is_skipped(self):
        # A high-stakes claim with no topic_hint can't be looked up;
        # evaluator stays silent (conservative).
        action, _ = ve._evaluate_claim_source_consistency(
            "TSLA traded at $250.00 on 2024-12-31",
            threshold=0.6,
            registry=_FakeRegistry(),
        )
        # Topic hint is "" because no _TOPIC_HINTS pattern matches.
        # Evaluator declines to escalate without a topic.
        self.assertIsNone(action)

    def test_missing_source_escalates_to_hedge(self):
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        with patch.object(ve, "extract_claims", return_value=[fake_claim]):
            action, note = ve._evaluate_claim_source_consistency(
                "some draft text",
                threshold=0.6,
                registry=_FakeRegistry(),  # empty registry
            )
        self.assertEqual(action, "hedge")
        self.assertIn("lack a registered source", note)

    def test_low_confidence_source_escalates_to_verify(self):
        from app.subia.grounding.source_registry import RegisteredSource
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        registry = _FakeRegistry({
            ("share_price", "default"): RegisteredSource(
                topic="share_price", key="default",
                url="https://example.com",
                learned_from="config",
                learned_at="2026-01-01T00:00:00Z",
                confidence=0.40,  # below threshold 0.60
            ),
        })
        with patch.object(ve, "extract_claims", return_value=[fake_claim]):
            action, note = ve._evaluate_claim_source_consistency(
                "draft", threshold=0.6, registry=registry,
            )
        self.assertEqual(action, "verify")
        self.assertIn("low-confidence", note)

    def test_high_confidence_source_no_escalation(self):
        from app.subia.grounding.source_registry import RegisteredSource
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        registry = _FakeRegistry({
            ("share_price", "default"): RegisteredSource(
                topic="share_price", key="default",
                url="https://example.com",
                learned_from="config",
                learned_at="2026-01-01T00:00:00Z",
                confidence=0.95,  # well above threshold 0.60
            ),
        })
        with patch.object(ve, "extract_claims", return_value=[fake_claim]):
            action, note = ve._evaluate_claim_source_consistency(
                "draft", threshold=0.6, registry=registry,
            )
        self.assertIsNone(action)

    def test_extract_claims_exception_is_swallowed(self):
        with patch.object(ve, "extract_claims",
                          side_effect=RuntimeError("boom")):
            action, _ = ve._evaluate_claim_source_consistency(
                "draft", threshold=0.6, registry=_FakeRegistry(),
            )
        self.assertIsNone(action)


# ============================================================================
# Retrieval-on-low-confidence evaluator
# ============================================================================


class TestRetrievalEvaluator(unittest.TestCase):
    def setUp(self) -> None:
        ve.clear_retrieval_budget_for_tests()

    def test_skipped_when_verdict_is_ship(self):
        action, _ = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_ship_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=lambda t: True,
        )
        self.assertIsNone(action)

    def test_skipped_when_verdict_is_peer_review(self):
        action, _ = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_peer_review_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=lambda t: True,
        )
        self.assertIsNone(action)

    def test_no_retriever_returns_none(self):
        action, _ = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=None,
        )
        self.assertIsNone(action)

    def test_budget_zero_returns_none(self):
        action, _ = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=0, retriever=lambda t: True,
        )
        self.assertIsNone(action)

    def test_retriever_true_returns_verify(self):
        action, note = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=lambda t: True,
        )
        self.assertEqual(action, "verify")
        self.assertIn("grounded", note)

    def test_retriever_false_strict_zone_escalates_to_peer_review(self):
        action, note = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.95, budget=1, retriever=lambda t: False,
        )
        self.assertEqual(action, "peer_review")
        self.assertIn("strict zone", note)

    def test_retriever_false_lax_zone_returns_verify(self):
        action, note = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=lambda t: False,
        )
        self.assertEqual(action, "verify")
        self.assertIn("no supporting evidence", note)

    def test_retriever_raises_is_swallowed(self):
        def _boom(t):
            raise RuntimeError("retriever exploded")

        action, note = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=_boom,
        )
        self.assertIsNone(action)
        self.assertIn("retrieval failed", note)

    def test_budget_consumed_across_calls(self):
        calls = []

        def _retriever(t):
            calls.append(t)
            return True

        # First call within budget — succeeds.
        action1, _ = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=_retriever,
        )
        self.assertEqual(action1, "verify")
        # Second call exhausts budget — returns None with note.
        action2, note2 = ve._evaluate_retrieval_on_low_confidence(
            "text", verdict=_hedge_verdict(), task_id="t1",
            threshold=0.6, budget=1, retriever=_retriever,
        )
        self.assertIsNone(action2)
        self.assertIn("budget exhausted", note2)
        self.assertEqual(len(calls), 1)  # retriever invoked exactly once


# ============================================================================
# apply_verification_extension aggregator
# ============================================================================


class TestApplyVerificationExtension(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()
        ve.clear_zone_hints_for_tests()
        ve.clear_retrieval_budget_for_tests()

    def test_master_switch_off_is_noop(self):
        with _patch_runtime_settings(verification_extension_enabled=False):
            verdict = _hedge_verdict()
            extended, notes = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="anything",
                task_id="t1",
            )
        self.assertIs(extended, verdict)
        self.assertEqual(notes, [])

    def test_master_switch_on_no_claims_returns_unchanged(self):
        with _patch_runtime_settings(verification_extension_enabled=True):
            verdict = _ship_verdict()
            extended, notes = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="hi there",
                task_id="t1",
            )
        self.assertEqual(extended.suggested_action, "ship")
        # No notes when nothing fires.
        self.assertEqual(notes, [])

    def test_missing_source_escalates_verdict(self):
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        with _patch_runtime_settings(verification_extension_enabled=True), \
                patch.object(ve, "extract_claims", return_value=[fake_claim]):
            verdict = _ship_verdict()  # calibration says ship
            extended, notes = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="draft",
                task_id="t1",
            )
        # Verdict escalated from ship → hedge.
        self.assertEqual(extended.suggested_action, "hedge")
        # Notes record what the evaluator said + the escalation tail.
        self.assertTrue(any("claim-source" in n for n in notes))
        self.assertTrue(any("escalated" in n for n in notes))

    def test_extension_never_weakens_verdict(self):
        # Even with no claims and master switch on, a peer_review
        # verdict from calibration is preserved.
        with _patch_runtime_settings(verification_extension_enabled=True):
            verdict = _peer_review_verdict()
            extended, _ = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="nothing high-stakes here",
                task_id="t1",
            )
        self.assertEqual(extended.suggested_action, "peer_review")

    def test_extension_preserves_verdict_metadata(self):
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        with _patch_runtime_settings(verification_extension_enabled=True), \
                patch.object(ve, "extract_claims", return_value=[fake_claim]):
            verdict = _hedge_verdict()
            extended, _ = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="draft",
                task_id="t1",
            )
        # biases_detected, forced_verifier_claim_ids, note_for_post_mortem
        # all preserved.
        self.assertEqual(extended.biases_detected, verdict.biases_detected)
        self.assertEqual(
            extended.note_for_post_mortem, verdict.note_for_post_mortem,
        )

    def test_runtime_settings_unavailable_is_safe(self):
        # Simulate runtime_settings raising on read.
        with patch.object(
            runtime_settings,
            "get_verification_extension_enabled",
            side_effect=RuntimeError("boom"),
        ):
            verdict = _hedge_verdict()
            extended, notes = ve.apply_verification_extension(
                verdict=verdict,
                proposal_text="draft",
                task_id="t1",
            )
        # Safe fallthrough: original verdict, no notes.
        self.assertIs(extended, verdict)
        self.assertEqual(notes, [])


# ============================================================================
# End-to-end gate_output integration
# ============================================================================


class TestGateOutputIntegration(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()
        ve.clear_zone_hints_for_tests()
        ve.clear_retrieval_budget_for_tests()

    def _gate(self):
        from app.epistemic.orchestrator_hook import gate_output
        return gate_output

    def test_extension_off_matches_pre_extension_behaviour(self):
        # With extension off + no biases, gate ships exactly as before.
        gate_output = self._gate()
        with _patch_runtime_settings(
                verification_extension_enabled=False,
                epistemic_enabled_override=True), \
                patch("app.epistemic.orchestrator_hook.load_ledger_for_task"), \
                patch(
                    "app.epistemic.orchestrator_hook.calibration_check",
                    return_value=_ship_verdict(),
                ):
            result = gate_output(
                proposal_text="hello world",
                task_id="task-A",
            )
        self.assertEqual(result.action, "ship")
        # No extension note in the diagnostic.
        self.assertNotIn("escalated", result.diagnostic_note or "")

    def test_extension_on_escalates_ship_to_hedge_on_missing_source(self):
        from types import SimpleNamespace
        fake_claim = SimpleNamespace(
            text="EUR 0.595", topic_hint="share_price",
            is_high_stakes=lambda: True,
        )
        gate_output = self._gate()
        with _patch_runtime_settings(
                verification_extension_enabled=True,
                epistemic_enabled_override=True), \
                patch("app.epistemic.orchestrator_hook.load_ledger_for_task"), \
                patch(
                    "app.epistemic.orchestrator_hook.calibration_check",
                    return_value=_ship_verdict(),
                ), \
                patch.object(ve, "extract_claims",
                             return_value=[fake_claim]):
            result = gate_output(
                proposal_text="draft mentioning share_price",
                task_id="task-B",
            )
        # Extension promoted verdict from ship → hedge. With blocking
        # mode off (default), _gate_for_non_critical ships with a
        # diagnostic note.
        self.assertEqual(result.action, "ship")
        self.assertIn("hedge", result.diagnostic_note)
        self.assertIn("claim-source", result.diagnostic_note)

    def test_extension_disabled_when_master_kill_switch_off(self):
        gate_output = self._gate()
        # Master kill switch off — gate bypasses calibration entirely.
        with _patch_runtime_settings(epistemic_enabled_override=False), \
                patch.dict(os.environ, {"EPISTEMIC_ENABLED": ""}):
            result = gate_output(
                proposal_text="anything",
                task_id="t",
            )
        self.assertEqual(result.action, "ship")
        self.assertIn("disabled", result.diagnostic_note)

    def test_extension_failure_falls_through_safely(self):
        gate_output = self._gate()
        with _patch_runtime_settings(
                verification_extension_enabled=True,
                epistemic_enabled_override=True), \
                patch(
                    "app.epistemic.orchestrator_hook.load_ledger_for_task",
                ), \
                patch(
                    "app.epistemic.orchestrator_hook.calibration_check",
                    return_value=_hedge_verdict(),
                ), \
                patch(
                    "app.epistemic.verification_extension."
                    "apply_verification_extension",
                    side_effect=RuntimeError("boom"),
                ):
            # Should not raise — gate must never propagate exceptions.
            result = gate_output(
                proposal_text="draft",
                task_id="task-C",
            )
        # Gate continues with the original verdict (hedge → non-critical).
        self.assertIn(result.action, ("ship", "revise"))

    def test_calibration_failure_short_circuits_before_extension(self):
        gate_output = self._gate()
        with _patch_runtime_settings(
                verification_extension_enabled=True,
                epistemic_enabled_override=True), \
                patch("app.epistemic.orchestrator_hook.load_ledger_for_task"), \
                patch(
                    "app.epistemic.orchestrator_hook.calibration_check",
                    side_effect=RuntimeError("boom"),
                ):
            result = gate_output(
                proposal_text="draft",
                task_id="task-D",
            )
        self.assertEqual(result.action, "ship")
        self.assertIn("calibration_check failed", result.diagnostic_note)


if __name__ == "__main__":
    unittest.main()
