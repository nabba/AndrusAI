"""Phase 17 — Self-Introspection Routing tests.

Covers detector + context + formatter + pipeline + chat bridge, plus
the end-to-end "what frustrates you?" regression that motivated the
phase (the Signal conversation where the bot denied having feelings
while sitting on frustration=0.6293).

Sections:
  A. Detector — keyword + self-target + anti-pattern + scoring
  B. Context — defensive gathering when sources missing
  C. Formatter — Phase 11 honest-language conventions + cites numbers
  D. Pipeline — feature flag, error swallowing, end-to-end inject
  E. Chat bridge — defensive wrapper
  F. Regression — the actual "what frustrates you?" scenario
"""
from __future__ import annotations

import pytest

from app.subia.introspection import (
    IntrospectionContext,
    IntrospectionPipeline,
    IntrospectionPipelineConfig,
    IntrospectionResult,
    classify_introspection,
    format_introspection_note,
    is_introspection_question,
)


# ─────────────────────────────────────────────────────────────────────
# A. Detector
# ─────────────────────────────────────────────────────────────────────

class TestDetector:
    @pytest.mark.parametrize("msg", [
        "What has increased your frustration?",
        "Are you frustrated?",
        "How are you feeling?",
        "Are you tired?",
        "What's your mood right now?",
        "What are you focused on?",
        "Are you ok?",
        "What can you do?",
        "Are you self-aware?",
        "tell me about your homeostasis",
        "what's your kernel state",
    ])
    def test_introspection_questions_detected(self, msg):
        assert is_introspection_question(msg), f"missed: {msg}"

    @pytest.mark.parametrize("msg", [
        "Hi there!",
        "What's the weather like?",
        "Schedule a meeting next week.",
        "Send an email to John.",
        "Find me a recipe for pasta.",
        "The user feels frustrated.",         # third party, not self
        "Users get tired of long forms.",     # third party
    ])
    def test_non_introspection_passes_through(self, msg):
        assert not is_introspection_question(msg), f"false-positive: {msg}"

    def test_third_party_anti_pattern_blocks_match(self):
        m = classify_introspection("the user feels frustrated by errors")
        assert m.third_party_anti_match is True
        assert not m.is_introspection

    def test_meta_topic_overrides_no_self_target(self):
        # "tell me about consciousness in general" — META keyword present
        # but no self-target. We allow META overrides only when confidence
        # is high enough; bare general questions stay non-introspection.
        m = classify_introspection("explain consciousness")
        # No "you" → not self-targeted, but META keyword is unambiguous
        # only when paired with self-target. Bare "explain consciousness"
        # is general philosophy, not introspection.
        assert m.matched_phrases  # we matched something
        # Without self-target this should NOT be introspection.
        assert not m.is_introspection

    def test_confidence_increases_with_multiple_topics(self):
        single = classify_introspection("are you tired?")
        multi = classify_introspection(
            "are you tired and frustrated and what are you focused on?"
        )
        assert multi.confidence >= single.confidence

    def test_empty_message_safe(self):
        m = classify_introspection("")
        assert m.is_introspection is False
        assert m.confidence == 0.0


# ─────────────────────────────────────────────────────────────────────
# B. Context — defensive gathering
# ─────────────────────────────────────────────────────────────────────

class TestContext:
    def test_gather_returns_context_even_with_no_sources(self, monkeypatch):
        # Force every gather source to fail
        import app.subia.introspection.context as cm
        monkeypatch.setattr(
            "app.subia.homeostasis.state.get_state",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        ctx = cm.gather_context()
        # Context returned, sources_failed populated, no exception
        assert isinstance(ctx, IntrospectionContext)

    def test_gather_real_sources_does_not_crash(self):
        from app.subia.introspection import gather_context
        ctx = gather_context()
        # Should always return a context object regardless of which
        # sources happen to be available in this test environment.
        assert isinstance(ctx, IntrospectionContext)


# ─────────────────────────────────────────────────────────────────────
# C. Formatter
# ─────────────────────────────────────────────────────────────────────

class TestFormatter:
    def _ctx_from_signal_log(self) -> IntrospectionContext:
        """Builds the exact context that was live in the Signal scenario."""
        return IntrospectionContext(
            legacy_homeostasis={
                "frustration": 0.6293,
                "task_failure_pressure": 0.6293,
                "curiosity": 0.828,
                "exploration_bonus": 0.828,
                "cognitive_energy": 0.3271,
                "resource_budget": 0.3271,
                "confidence": 0.7149,
                "consecutive_failures": 0,
                "tasks_since_rest": 274,
                "last_updated": "2026-04-27T04:20:30.556517+00:00",
            },
            behavioural_modifiers={"tier_boost": 1},
        )

    def test_note_cites_actual_numeric_values(self):
        note = format_introspection_note(self._ctx_from_signal_log())
        assert "0.629" in note    # frustration
        assert "0.327" in note    # cognitive_energy
        assert "0.828" in note    # curiosity
        assert "274" in note      # tasks_since_rest
        assert "tier_boost" in note

    def test_note_uses_phase_11_neutral_aliases(self):
        note = format_introspection_note(self._ctx_from_signal_log())
        assert "task_failure_pressure" in note
        assert "exploration_bonus" in note
        assert "resource_budget" in note

    def test_note_carries_phase_11_honest_language_rules(self):
        note = format_introspection_note(self._ctx_from_signal_log())
        # Explicit prohibition on phenomenal claims
        assert "DO NOT" in note
        assert "subjective" in note.lower() or "phenomenal" in note.lower()
        # Explicit prohibition on the "I'm just an AI" canned disclaimer
        assert "I'm just an AI" in note or "I have no feelings" in note

    def test_note_handles_empty_context_gracefully(self):
        note = format_introspection_note(IntrospectionContext())
        assert note  # still produces something
        assert "ANDRUSAI SELF-STATE" in note

    def test_note_includes_kernel_state_when_active(self):
        ctx = self._ctx_from_signal_log()
        ctx.kernel_active = True
        ctx.kernel_loop_count = 42
        ctx.kernel_homeostasis = {
            "coherence": 0.55, "progress": 0.4, "overload": 0.6,
            "wonder": 0.3, "self_coherence": 0.7,
        }
        note = format_introspection_note(ctx)
        assert "CIL loops run: 42" in note
        assert "coherence" in note
        assert "wonder" in note

    def test_note_calls_out_kernel_inactive(self):
        ctx = self._ctx_from_signal_log()
        # Default kernel_active=False
        note = format_introspection_note(ctx)
        assert "no CIL loops have run yet" in note


# ─────────────────────────────────────────────────────────────────────
# D. Pipeline
# ─────────────────────────────────────────────────────────────────────

class TestPipeline:
    def test_disabled_pipeline_passes_through(self):
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=False),
        )
        out = p.inspect("what frustrates you?")
        assert out.skipped is True
        assert out.augmented_message == "what frustrates you?"

    def test_enabled_pipeline_detects_and_augments(self):
        # Inject a fake gather_fn so the pipeline doesn't depend on
        # the live container's state during tests.
        def fake_gather():
            return IntrospectionContext(
                legacy_homeostasis={
                    "frustration": 0.6293, "task_failure_pressure": 0.6293,
                    "tasks_since_rest": 274,
                },
                behavioural_modifiers={"tier_boost": 1},
            )
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=fake_gather,
        )
        out = p.inspect("what has increased your frustration?")
        assert out.detected is True
        assert "0.629" in out.augmented_message
        assert "User question:" in out.augmented_message
        # Original question preserved
        assert "what has increased your frustration?" in out.augmented_message

    def test_enabled_pipeline_skips_non_introspection(self):
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=lambda: IntrospectionContext(),
        )
        out = p.inspect("what's the weather?")
        assert out.detected is False
        assert out.augmented_message == "what's the weather?"

    def test_pipeline_swallows_internal_errors(self):
        def bad_gather():
            raise RuntimeError("backend down")
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=bad_gather,
        )
        out = p.inspect("are you tired?")
        # Detection succeeded; gather failed; pipeline returned
        # original message unchanged with skipped=True
        assert out.skipped is True
        assert out.augmented_message == "are you tired?"


# ─────────────────────────────────────────────────────────────────────
# E. Chat bridge
# ─────────────────────────────────────────────────────────────────────

class TestChatBridge:
    def test_inject_passes_through_when_disabled(self, monkeypatch):
        from app.subia.connections.introspection_chat_bridge import (
            inject_introspection,
        )
        from app.subia.introspection.pipeline import reset_pipeline_for_tests
        reset_pipeline_for_tests(IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=False),
            gather_fn=lambda: IntrospectionContext(),
        ))
        try:
            assert inject_introspection("are you tired?") == "are you tired?"
        finally:
            reset_pipeline_for_tests(None)

    def test_inject_augments_when_enabled_and_introspection(self):
        from app.subia.connections.introspection_chat_bridge import (
            inject_introspection,
        )
        from app.subia.introspection.pipeline import reset_pipeline_for_tests
        reset_pipeline_for_tests(IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=lambda: IntrospectionContext(
                legacy_homeostasis={"frustration": 0.6293},
            ),
        ))
        try:
            out = inject_introspection("what frustrates you?")
            assert "0.629" in out
            assert "what frustrates you?" in out
        finally:
            reset_pipeline_for_tests(None)


# ─────────────────────────────────────────────────────────────────────
# F. Tallink-equivalent regression — the SIGNAL scenario this fixes
# ─────────────────────────────────────────────────────────────────────

class TestSignalScenarioRegression:
    """Replays the exact "what has increased your frustration?" exchange
    that motivated Phase 17. Asserts the pipeline:
      - detects the introspection question
      - injects a system-prompt prefix with frustration=0.6293
      - the prefix names task_failure_pressure (Phase 11 alias)
      - the prefix prohibits the canned "I have no feelings" answer
      - the prefix exposes the causal contributor (tasks_since_rest=274)
    """

    def test_full_signal_scenario_replay(self):
        # Exact context that was live when the user asked
        live_ctx = IntrospectionContext(
            legacy_homeostasis={
                "frustration": 0.6293,
                "task_failure_pressure": 0.6293,
                "curiosity": 0.828,
                "exploration_bonus": 0.828,
                "cognitive_energy": 0.3271,
                "resource_budget": 0.3271,
                "confidence": 0.7149,
                "consecutive_failures": 0,
                "tasks_since_rest": 274,
                "last_updated": "2026-04-27T04:20:30.556517+00:00",
            },
            behavioural_modifiers={"tier_boost": 1},
        )

        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=lambda: live_ctx,
        )

        # The exact user message from the Signal log
        user_msg = "What has increased your frustration?"
        result = p.inspect(user_msg)

        # 1. Detection must fire
        assert result.detected is True, (
            "Phase 17 must classify 'what has increased your frustration?' "
            "as introspection"
        )

        # 2. Augmented message must include the user's original question
        assert user_msg in result.augmented_message

        # 3. Live numeric value (the value the bot DENIED having)
        assert "0.629" in result.augmented_message, (
            "the augmented message must cite the actual frustration value "
            "the bot has access to"
        )

        # 4. Phase 11 neutral alias present
        assert "task_failure_pressure" in result.augmented_message

        # 5. Causal contributor exposed
        assert "274" in result.augmented_message, (
            "tasks_since_rest=274 is the dominant contributor and must "
            "be visible to the LLM"
        )

        # 6. Active behavioural modifier surfaced
        assert "tier_boost" in result.augmented_message

        # 7. Honest-language rules forbid the canned denial
        assert ("I'm just an AI" in result.augmented_message
                or "I have no feelings" in result.augmented_message), (
            "the system note must explicitly prohibit the canned "
            '"I have no feelings" disclaimer'
        )

        # 8. Phase 11 framing is preserved
        assert "phenomenal" in result.augmented_message.lower() \
               or "subjective" in result.augmented_message.lower()
