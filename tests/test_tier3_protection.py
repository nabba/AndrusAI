"""
Tests for Tier-3 protection coverage.

These tests enforce the CLAUDE.md invariant: "Evaluation functions and
safety constraints live at INFRASTRUCTURE level — must NEVER be in
agent-modifiable code paths."

If a consciousness evaluator, belief store, or homeostatic config is
added to the codebase, it should also be added to TIER3_FILES. The
tests here fail loudly when that invariant is violated.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _repo_root_str() -> str:
    """Return the repo root the way safety_guardian expects as app_root."""
    # In production the app lives at /app; here we pass the real repo root
    # so the test works on the host and in the container.
    return str(REPO_ROOT)


class TestTier3Coverage:
    def test_original_infrastructure_still_listed(self):
        """Regression guard: the original Tier-3 list must not shrink."""
        from app.safety_guardian import TIER3_FILES
        for path in [
            "app/eval_sandbox.py",
            "app/safety_guardian.py",
            "app/feedback_pipeline.py",
            "app/security.py",
            "app/sanitize.py",
            "app/vetting.py",
            "app/version_manifest.py",
            "app/sandbox_runner.py",
            "app/health_monitor.py",
            "app/self_healer.py",
            "app/reference_tasks.py",
        ]:
            assert path in TIER3_FILES, f"regressed: {path} removed from Tier 3"

    def test_consciousness_evaluators_protected(self):
        """New Tier-3 coverage: consciousness evaluators + homeostatic config."""
        from app.safety_guardian import TIER3_FILES
        required = [
            "app/self_awareness/consciousness_probe.py",
            "app/self_awareness/behavioral_assessment.py",
            "app/self_awareness/certainty_vector.py",
            "app/self_awareness/somatic_marker.py",
            "app/self_awareness/meta_cognitive.py",
            "app/self_awareness/sentience_config.py",
            "app/consciousness/adversarial_probes.py",
            "app/consciousness/belief_store.py",
            "app/consciousness/metacognitive_monitor.py",
            "app/consciousness/config.py",
            "app/consciousness/workspace_buffer.py",
            "app/consciousness/attention_schema.py",
            "app/consciousness/global_broadcast.py",
            "app/consciousness/prediction_hierarchy.py",
            "app/consciousness/predictive_layer.py",
        ]
        missing = [p for p in required if p not in TIER3_FILES]
        assert not missing, f"Consciousness evaluators NOT protected: {missing}"

    def test_governance_protected(self):
        """governance.py enforces promotion gates — it must be immutable."""
        from app.safety_guardian import TIER3_FILES
        assert "app/governance.py" in TIER3_FILES

    def test_subia_infrastructure_protected(self):
        """SubIA config + kernel are infrastructure per PROGRAM.md Phase 3."""
        from app.safety_guardian import TIER3_FILES
        assert "app/subia/config.py" in TIER3_FILES
        assert "app/subia/kernel.py" in TIER3_FILES

    def test_phase2_pp1_closure_protected(self):
        """Phase 2 PP-1 half-circuit closure: surprise_routing must be
        Tier-3 because it is the only path by which computed prediction
        errors actually reach the scene. If it were agent-modifiable,
        the Self-Improver could silently re-open the half-circuit.
        """
        from app.safety_guardian import TIER3_FILES
        assert "app/subia/prediction/surprise_routing.py" in TIER3_FILES

    def test_phase2_hot3_closure_protected(self):
        """Phase 2 HOT-3 half-circuit closure: dispatch_gate must be
        Tier-3. It is the policy module that decides whether consulted
        beliefs ALLOW, ESCALATE, or BLOCK crew dispatch. Agent-modifiable
        gating policy would let the Self-Improver weaken its own
        refusal criteria.
        """
        from app.safety_guardian import TIER3_FILES
        assert "app/subia/belief/dispatch_gate.py" in TIER3_FILES

    def test_phase2_certainty_hedging_protected(self):
        """Phase 2 certainty → response-hedging closure: the
        post-processor that applies [Inferred]/[Uncertain] tags must be
        Tier-3. An agent-modifiable hedger could be rewritten to skip
        hedging, which would silently re-open the half-circuit.
        """
        from app.safety_guardian import TIER3_FILES
        assert "app/subia/belief/response_hedging.py" in TIER3_FILES

    def test_phase1_migrations_protected(self):
        """Migrated modules (Phase 1) are protected at the NEW canonical path.
        Old shim paths remain in TIER3_FILES to protect the redirection.
        """
        from app.safety_guardian import TIER3_FILES
        phase1_pairs = [
            ("app/consciousness/workspace_buffer.py",      "app/subia/scene/buffer.py"),
            ("app/consciousness/attention_schema.py",      "app/subia/scene/attention_schema.py"),
            ("app/consciousness/global_broadcast.py",      "app/subia/scene/broadcast.py"),
            ("app/consciousness/meta_workspace.py",        "app/subia/scene/meta_workspace.py"),
            ("app/consciousness/personality_workspace.py", "app/subia/scene/personality_workspace.py"),
            ("app/consciousness/belief_store.py",          "app/subia/belief/store.py"),
            ("app/consciousness/metacognitive_monitor.py", "app/subia/belief/metacognition.py"),
            ("app/consciousness/prediction_hierarchy.py",  "app/subia/prediction/hierarchy.py"),
            ("app/consciousness/predictive_layer.py",      "app/subia/prediction/layer.py"),
            ("app/consciousness/adversarial_probes.py",    "app/subia/probes/adversarial.py"),
            # self_awareness batch
            ("app/self_awareness/self_model.py",            "app/subia/self/model.py"),
            ("app/self_awareness/hyper_model.py",           "app/subia/self/hyper_model.py"),
            ("app/self_awareness/temporal_identity.py",     "app/subia/self/temporal_identity.py"),
            ("app/self_awareness/agent_state.py",           "app/subia/self/agent_state.py"),
            ("app/self_awareness/loop_closure.py",          "app/subia/self/loop_closure.py"),
            ("app/self_awareness/homeostasis.py",           "app/subia/homeostasis/state.py"),
            ("app/self_awareness/somatic_marker.py",        "app/subia/homeostasis/somatic_marker.py"),
            ("app/self_awareness/somatic_bias.py",          "app/subia/homeostasis/somatic_bias.py"),
            ("app/self_awareness/certainty_vector.py",      "app/subia/belief/certainty.py"),
            ("app/self_awareness/consciousness_probe.py",   "app/subia/probes/consciousness_probe.py"),
            ("app/self_awareness/behavioral_assessment.py", "app/subia/probes/behavioral_assessment.py"),
            # batch 4 (triage-pass migrations)
            ("app/self_awareness/cogito.py",                  "app/subia/belief/cogito.py"),
            ("app/self_awareness/dual_channel.py",            "app/subia/belief/dual_channel.py"),
            ("app/self_awareness/global_workspace.py",        "app/subia/scene/global_workspace.py"),
            ("app/self_awareness/grounding.py",               "app/subia/self/grounding.py"),
            ("app/self_awareness/inferential_competition.py", "app/subia/prediction/inferential_competition.py"),
            ("app/self_awareness/internal_state.py",          "app/subia/belief/internal_state.py"),
            ("app/self_awareness/meta_cognitive.py",          "app/subia/belief/meta_cognitive_layer.py"),
            ("app/self_awareness/precision_weighting.py",     "app/subia/prediction/precision_weighting.py"),
            ("app/self_awareness/query_router.py",            "app/subia/self/query_router.py"),
            ("app/self_awareness/reality_model.py",           "app/subia/prediction/reality_model.py"),
            ("app/self_awareness/sentience_config.py",        "app/subia/sentience_config.py"),
            ("app/self_awareness/state_logger.py",            "app/subia/belief/state_logger.py"),
            ("app/self_awareness/world_model.py",             "app/subia/belief/world_model.py"),
        ]
        for old, new in phase1_pairs:
            assert old in TIER3_FILES, f"shim not protected: {old}"
            assert new in TIER3_FILES, f"target not protected: {new}"

    def test_all_listed_files_exist_on_disk(self):
        """Every declared Tier-3 file must actually exist. Otherwise the
        checksum machinery silently tracks a non-existent path.
        """
        from app.safety_guardian import TIER3_FILES
        missing = []
        for path in TIER3_FILES:
            if not (REPO_ROOT / path).exists():
                missing.append(path)
        assert not missing, f"Declared but missing: {missing}"


class TestTier3Status:
    def test_status_reports_all_present(self):
        from app.safety_guardian import tier3_status, TIER3_FILES
        status = tier3_status(_repo_root_str())
        assert status["total"] == len(TIER3_FILES)
        assert status["missing"] == []
        assert len(status["present"]) == len(TIER3_FILES)

    def test_status_checksums_are_sha256_hex(self):
        from app.safety_guardian import tier3_status
        status = tier3_status(_repo_root_str())
        for path, digest in status["checksums"].items():
            assert len(digest) == 64, f"{path}: not 64-char hex ({digest})"
            int(digest, 16)  # raises if not valid hex

    def test_status_detects_missing(self, tmp_path):
        """Pointing at an empty directory yields all missing, no checksums."""
        from app.safety_guardian import tier3_status, TIER3_FILES
        status = tier3_status(str(tmp_path))
        assert status["present"] == []
        assert set(status["missing"]) == set(TIER3_FILES)
        assert status["checksums"] == {}

    def test_checksum_changes_on_content_change(self, tmp_path):
        """Modifying a tracked file changes its SHA-256 digest."""
        from app.safety_guardian import tier3_status, TIER3_FILES
        # Build a fake app root with the first tier-3 file.
        target = TIER3_FILES[0]
        (tmp_path / target).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / target).write_text("content-A")

        status_a = tier3_status(str(tmp_path))
        digest_a = status_a["checksums"][target]

        (tmp_path / target).write_text("content-B")
        status_b = tier3_status(str(tmp_path))
        digest_b = status_b["checksums"][target]

        assert digest_a != digest_b, "SHA-256 failed to detect content change"
