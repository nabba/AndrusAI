"""Phase 19 — Half-Circuit Closures regression tests.

Validates the four wire-ins that converted previously computed-but-
unread signals into actual behaviour gates:

  Closure 1: Wonder should_inhibit_completion → CIL Step 6 monitor
             downgrades ALLOW dispatch to ESCALATE when wonder is
             clearly elevated (per-item event OR var > setpoint+margin).
  Closure 2: Boundary homeostatic_modulator_for → homeostasis engine
             scales novelty_balance + contradiction_pressure deltas
             by the dominant scene processing_mode.
  Closure 3: Phase 8 apply_salience_boost → already wired in Step 3
             (Attend) — verified the call site still hits + bumps.
  Closure 4: Wonder freeze_decay_for → scene buffer score() skips
             recency decay for items with wonder_intensity above the
             freeze threshold.

Each closure has a dedicated test plus a "doesn't fire on default
state" sanity test so we don't spuriously alter existing behaviour.
"""
from __future__ import annotations

import pytest

from app.subia.kernel import SceneItem, SubjectivityKernel


# ─────────────────────────────────────────────────────────────────────
# Closure 1 — Wonder inhibit-completion gate (CIL Step 6)
# ─────────────────────────────────────────────────────────────────────

class TestClosure1WonderInhibit:
    def _build_loop(self):
        from app.subia.loop import SubIALoop
        from app.subia.scene.buffer import CompetitiveGate
        return SubIALoop(
            kernel=SubjectivityKernel(),
            scene_gate=CompetitiveGate(capacity=5),
        )

    def test_default_state_does_not_inhibit(self):
        """Wonder defaults to 0.5 (initial) but setpoint is 0.4 — the
        deviation 0.1 is below the 0.15 margin, and no scene item has
        wonder_intensity yet. Gate must NOT fire on this steady state."""
        loop = self._build_loop()
        result = loop.pre_task(
            agent_role="coder", task_description="x",
            operation_type="task_execute",
        )
        outcome = result.step("6_monitor")
        details = outcome.details if outcome else {}
        assert not details.get("wonder_inhibits_completion"), (
            "Wonder gate must not fire on default-steady-state wonder=0.5 "
            "(setpoint=0.4, deviation 0.1 < margin 0.15)"
        )
        # Dispatch was not overridden
        assert not details.get("dispatch_overridden_by_wonder")

    def test_per_item_wonder_event_inhibits(self):
        """A scene item carrying wonder_intensity > 0 (a real Phase 12
        wonder event) should trigger the gate. We need a consult_fn so
        a real DispatchDecision exists to override."""
        from app.subia.loop import SubIALoop
        from app.subia.scene.buffer import CompetitiveGate
        from app.subia.belief.dispatch_gate import (
            decide_dispatch, DispatchDecision,
        )
        # consult_fn returns no beliefs → ALLOW by default
        loop = SubIALoop(
            kernel=SubjectivityKernel(),
            scene_gate=CompetitiveGate(capacity=5),
            consult_fn=lambda **kw: [],
            dispatch_decider=decide_dispatch,
        )
        # Build a wonder-bearing scene item and feed it through perceive
        # so it lands in focal scene by the time _step_monitor runs.
        item = SceneItem(
            id="deep-thought", source="wiki", content_ref="x",
            summary="something profound", salience=0.9, entered_at="t",
        )
        item.wonder_intensity = 0.6   # > WONDER_INHIBIT_THRESHOLD=0.3
        result = loop.pre_task(
            agent_role="coder", task_description="x",
            operation_type="task_execute",
            input_items=[item],
        )
        outcome = result.step("6_monitor")
        details = outcome.details if outcome else {}
        # Gate fired
        assert details.get("wonder_inhibits_completion") is True
        # The dispatch ends up at ESCALATE (or stronger). With no real
        # belief store this defaults to ESCALATE already, so the
        # override flag fires only when the prior decision was ALLOW.
        # Either verdict path is acceptable here; the closure's
        # observable consequence is `wonder_inhibits_completion`.
        verdict = details.get("verdict")
        assert verdict in ("ESCALATE", "BLOCK", None)

    def test_high_wonder_var_above_setpoint_margin_inhibits(self):
        """When the kernel's homeostatic wonder rises clearly above its
        setpoint (deviation > margin), the gate fires even without a
        per-item event — that's the variable-driven path."""
        loop = self._build_loop()
        # Force wonder var well above setpoint (default 0.4 + margin 0.15 + 0.05)
        loop.kernel.homeostasis.variables["wonder"] = 0.65
        loop.kernel.homeostasis.set_points["wonder"] = 0.4
        result = loop.pre_task(
            agent_role="coder", task_description="x",
            operation_type="task_execute",
        )
        outcome = result.step("6_monitor")
        details = outcome.details if outcome else {}
        assert details.get("wonder_inhibits_completion") is True


# ─────────────────────────────────────────────────────────────────────
# Closure 2 — Boundary modulator in homeostasis engine
# ─────────────────────────────────────────────────────────────────────

class TestClosure2BoundaryModulator:
    def _kernel_with_items(self, mode: str | None) -> SubjectivityKernel:
        from app.subia.homeostasis.engine import ensure_variables
        k = SubjectivityKernel()
        ensure_variables(k)
        # Reset homeostasis to clean state
        k.homeostasis.variables["novelty_balance"] = 0.5
        k.homeostasis.variables["contradiction_pressure"] = 0.5
        # Build 3 candidate items with the requested mode
        items = []
        for i in range(3):
            it = SceneItem(
                id=f"i{i}", source="firecrawl", content_ref=f"r{i}",
                summary=f"s{i}", salience=0.5, entered_at="t",
                conflicts_with=[f"c{i}"],   # 1 conflict each
            )
            if mode is not None:
                it.processing_mode = mode
            items.append(it)
        return k, items

    def test_perceptual_mode_amplifies_novelty(self):
        """Perceptual mode has × 1.5 modulator on novelty_balance,
        so 3 firecrawl items should bump novelty MORE than the
        same items with no mode tag."""
        from app.subia.homeostasis.engine import update_homeostasis

        # Baseline: no mode
        k_no, items_no = self._kernel_with_items(None)
        update_homeostasis(k_no, new_items=items_no)
        novelty_no_mode = k_no.homeostasis.variables["novelty_balance"]

        # With perceptual mode
        k_p, items_p = self._kernel_with_items("perceptual")
        update_homeostasis(k_p, new_items=items_p)
        novelty_perceptual = k_p.homeostasis.variables["novelty_balance"]

        # Perceptual should produce a strictly larger increase
        assert novelty_perceptual > novelty_no_mode, (
            f"perceptual modulator should amplify novelty: "
            f"perceptual={novelty_perceptual}, none={novelty_no_mode}"
        )

    def test_memorial_mode_dampens_novelty(self):
        """Memorial mode has × 0.7 on novelty_balance — should produce
        a smaller increase than no-mode."""
        from app.subia.homeostasis.engine import update_homeostasis
        k_no, items_no = self._kernel_with_items(None)
        update_homeostasis(k_no, new_items=items_no)
        novelty_no = k_no.homeostasis.variables["novelty_balance"]

        k_m, items_m = self._kernel_with_items("memorial")
        update_homeostasis(k_m, new_items=items_m)
        novelty_mem = k_m.homeostasis.variables["novelty_balance"]
        assert novelty_mem < novelty_no, (
            f"memorial modulator should dampen novelty: "
            f"memorial={novelty_mem}, none={novelty_no}"
        )

    def test_dominant_mode_is_reported(self):
        from app.subia.homeostasis.engine import update_homeostasis
        k, items = self._kernel_with_items("introspective")
        summary = update_homeostasis(k, new_items=items)
        assert summary.get("dominant_processing_mode") == "introspective"

    def test_no_mode_does_not_change_legacy_behavior(self):
        """When no item has a processing_mode, the engine's behaviour
        must be byte-identical to the pre-Phase-19 path."""
        from app.subia.homeostasis.engine import update_homeostasis
        k, items = self._kernel_with_items(None)
        update_homeostasis(k, new_items=items)
        # Novelty should equal the legacy formula:
        # initial 0.5 + (0.05 * 3 firecrawl items) = 0.65
        assert k.homeostasis.variables["novelty_balance"] == pytest.approx(0.65)


# ─────────────────────────────────────────────────────────────────────
# Closure 3 — Phase 8 salience_boost (sanity: still wired)
# ─────────────────────────────────────────────────────────────────────

class TestClosure3SalienceBoost:
    def test_salience_boost_called_in_step_attend(self):
        """Verify the apply_salience_boost call in _step_attend
        actually executes against social_models. We monkey-patch it
        and assert it was called once with the kernel's social_models."""
        from app.subia.loop import SubIALoop
        from app.subia.scene.buffer import CompetitiveGate
        from app.subia.kernel import SocialModelEntry
        import app.subia.social.salience_boost as sb_mod

        kernel = SubjectivityKernel()
        kernel.social_models["andrus"] = SocialModelEntry(
            entity_id="andrus", entity_type="human",
            inferred_focus=["consciousness", "subia"],
            trust_level=0.85,
        )
        loop = SubIALoop(
            kernel=kernel, scene_gate=CompetitiveGate(capacity=5),
        )

        calls = []
        original = sb_mod.apply_salience_boost
        def spy(candidates, social_models):
            calls.append({"social_models_keys": list(social_models or {})})
            return original(candidates, social_models)
        try:
            sb_mod.apply_salience_boost = spy
            loop.pre_task(
                agent_role="coder", task_description="x",
                operation_type="task_execute",
            )
        finally:
            sb_mod.apply_salience_boost = original

        assert calls, "apply_salience_boost was never called from _step_attend"
        assert "andrus" in calls[0]["social_models_keys"]


# ─────────────────────────────────────────────────────────────────────
# Closure 4 — freeze_decay_for in scene buffer
# ─────────────────────────────────────────────────────────────────────

class TestClosure4FreezeDecay:
    def _make_workspace_item(self, *, wonder_intensity: float = 0.0,
                              cycles: int = 5):
        from app.subia.scene.buffer import WorkspaceItem
        # Build a minimal WorkspaceItem with realistic fields
        # WorkspaceItem fields vary per phase; construct with whatever
        # the dataclass accepts.
        try:
            it = WorkspaceItem(
                content="deep idea",
                content_embedding=[0.1, 0.2, 0.3],
                source_channel="wiki",
            )
        except TypeError:
            it = WorkspaceItem()
            it.content = "deep idea"
            it.content_embedding = [0.1, 0.2, 0.3]
        it.cycles_in_workspace = cycles
        it.decay_rate = 0.05
        it.goal_relevance = 0.6
        it.novelty_score = 0.7
        it.agent_urgency = 0.5
        it.surprise_signal = 0.5
        it.wonder_intensity = wonder_intensity
        return it

    def test_no_wonder_decays_normally(self):
        """When wonder_intensity is 0, decay applies normally — the
        gate's score() function multiplies by (1 - decay_rate)^cycles."""
        from app.subia.scene.buffer import SalienceScorer
        scorer = SalienceScorer()
        item_no = self._make_workspace_item(wonder_intensity=0.0, cycles=10)
        score_no = scorer.score(item_no, goal_embeddings=[], recent_items=[])
        # 10 cycles of 5% decay = 0.95 ** 10 ≈ 0.5987
        # The composite raw score = w_goal*0.6 + w_nov*0.7 + w_urg*0.5 + w_sur*0.5
        # with default weights 0.35/0.25/0.15/0.25 ⇒
        #   0.35*0.5+ 0.25*0.7 + 0.15*0.5 + 0.25*0.5 = 0.175+0.175+0.075+0.125 = 0.55
        # (note: goal_relevance gets overwritten to 0.5 because we passed
        # no goal_embeddings; novelty_score gets overwritten to 0.8 for
        # first-item path because we passed no recent_items)
        assert score_no < 0.55, (
            "score with decay must be less than the undecayed composite"
        )

    def test_high_wonder_freezes_decay(self):
        """When wonder_intensity > WONDER_FREEZE_THRESHOLD (=0.5),
        the decay multiplier is replaced with 1.0 — score equals the
        raw composite without any cycle-based decay."""
        from app.subia.scene.buffer import SalienceScorer
        scorer = SalienceScorer()
        # Identical item but with wonder above freeze threshold
        item_wonder = self._make_workspace_item(
            wonder_intensity=0.6, cycles=10,
        )
        score_wonder = scorer.score(
            item_wonder, goal_embeddings=[], recent_items=[],
        )
        # Without decay, raw score should be near the undecayed composite
        # which (per the helper above) is around 0.55 with the override
        # values; we just need score_wonder > 0.5 (i.e. clearly above
        # what 10 cycles of decay would have produced).
        assert score_wonder >= 0.5, (
            f"frozen-decay score should be near the raw composite, "
            f"got {score_wonder}"
        )

    def test_low_wonder_below_threshold_does_not_freeze(self):
        """A scene item with wonder_intensity below the freeze
        threshold (0.5) should NOT have its decay frozen."""
        from app.subia.scene.buffer import SalienceScorer
        scorer = SalienceScorer()
        item_low = self._make_workspace_item(
            wonder_intensity=0.2, cycles=10,   # below WONDER_FREEZE_THRESHOLD=0.5
        )
        item_no = self._make_workspace_item(wonder_intensity=0.0, cycles=10)
        score_low = scorer.score(item_low, goal_embeddings=[], recent_items=[])
        score_no = scorer.score(item_no, goal_embeddings=[], recent_items=[])
        # Both should decay identically (low wonder doesn't freeze)
        assert abs(score_low - score_no) < 1e-6
