"""
Tests for the app/subia/ skeleton.

These tests verify:
  - The package is importable.
  - SUBIA_CONFIG contains the expected keys and shape.
  - The SubjectivityKernel dataclass round-trips through construction,
    scene tiering, and metadata updates.

Behavior (CIL loop, salience scoring, homeostasis, prediction) is
intentionally NOT tested yet — it will be added phase-by-phase as
the subpackages are populated.
"""

from __future__ import annotations

import re


class TestConfig:
    def test_import(self):
        from app.subia.config import SUBIA_CONFIG, get_config
        assert isinstance(SUBIA_CONFIG, dict)
        assert isinstance(get_config(), dict)

    def test_required_keys_present(self):
        from app.subia.config import SUBIA_CONFIG
        required = {
            "SCENE_CAPACITY", "SCENE_MIN_SALIENCE", "PERIPHERAL_CAPACITY",
            "SALIENCE_WEIGHTS", "EPISTEMIC_WEIGHT_MAP", "PLANNING_WEIGHTS",
            "HOMEOSTATIC_VARIABLES", "HOMEOSTATIC_DEFAULT_SETPOINT",
            "HOMEOSTATIC_DEVIATION_THRESHOLD",
            "PREDICTION_CONFIDENCE_THRESHOLD", "PREDICTION_HISTORY_WINDOW",
            "MONITOR_ANOMALY_THRESHOLD", "MONITOR_KNOWN_UNKNOWNS_LIMIT",
            "CONSOLIDATION_EPISODE_THRESHOLD", "CONSOLIDATION_RELATION_THRESHOLD",
            "FULL_LOOP_OPERATIONS", "COMPRESSED_LOOP_OPERATIONS",
            "SETPOINT_MODIFICATION_ALLOWED", "AUDIT_SUPPRESSION_ALLOWED",
        }
        missing = required - set(SUBIA_CONFIG.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_salience_weights_sum_to_one(self):
        from app.subia.config import SUBIA_CONFIG
        weights = SUBIA_CONFIG["SALIENCE_WEIGHTS"]
        assert abs(sum(weights.values()) - 1.0) < 1e-9, sum(weights.values())

    def test_safety_defaults_closed(self):
        """SETPOINT_MODIFICATION_ALLOWED and AUDIT_SUPPRESSION_ALLOWED must default False."""
        from app.subia.config import SUBIA_CONFIG
        assert SUBIA_CONFIG["SETPOINT_MODIFICATION_ALLOWED"] is False
        assert SUBIA_CONFIG["AUDIT_SUPPRESSION_ALLOWED"] is False

    def test_get_config_returns_copy(self):
        """get_config() returns a copy so callers cannot mutate the source."""
        from app.subia.config import SUBIA_CONFIG, get_config
        cp = get_config()
        cp["SCENE_CAPACITY"] = 999
        assert SUBIA_CONFIG["SCENE_CAPACITY"] == 5


class TestKernelDataclass:
    def test_construction_with_defaults(self):
        from app.subia.kernel import SubjectivityKernel
        k = SubjectivityKernel()
        assert k.loop_count == 0
        assert k.scene == []
        assert k.self_state.identity["name"] == "AndrusAI"
        assert k.homeostasis.variables == {}
        assert k.meta_monitor.confidence == 0.5
        assert k.predictions == []
        assert k.social_models == {}
        assert k.consolidation_buffer.pending_episodes == []

    def test_scene_item_fields(self):
        from app.subia.kernel import SceneItem
        item = SceneItem(
            id="i1",
            source="wiki",
            content_ref="archibal/landscape.md",
            summary="Truepic Series C analysis",
            salience=0.8,
            entered_at="2026-04-13T12:00:00Z",
        )
        assert item.ownership == "self"
        assert item.tier == "focal"
        assert item.valence == 0.0
        assert item.conflicts_with == []

    def test_focal_vs_peripheral_separation(self):
        from app.subia.kernel import SubjectivityKernel, SceneItem
        k = SubjectivityKernel()
        k.scene.append(SceneItem(id="a", source="wiki", content_ref="x",
                                 summary="focal item", salience=0.8,
                                 entered_at="2026-04-13T00:00:00Z",
                                 tier="focal"))
        k.scene.append(SceneItem(id="b", source="wiki", content_ref="y",
                                 summary="peripheral item", salience=0.3,
                                 entered_at="2026-04-13T00:00:00Z",
                                 tier="peripheral"))
        assert len(k.focal_scene()) == 1
        assert len(k.peripheral_scene()) == 1
        assert k.focal_scene()[0].id == "a"
        assert k.peripheral_scene()[0].id == "b"

    def test_touch_sets_iso_timestamp(self):
        from app.subia.kernel import SubjectivityKernel
        k = SubjectivityKernel()
        k.touch()
        # Basic ISO 8601 check (YYYY-MM-DDTHH:MM:SS…)
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", k.last_loop_at)

    def test_commitment_structure(self):
        from app.subia.kernel import Commitment
        c = Commitment(id="c1", description="Draft Q2 plan", venture="plg",
                       created_at="2026-04-13T00:00:00Z")
        assert c.status == "active"
        assert c.related_wiki_pages == []
        assert c.homeostatic_impact == {}

    def test_prediction_structure(self):
        from app.subia.kernel import Prediction
        p = Prediction(
            id="p1", operation="ingest",
            predicted_outcome={"wiki_pages_affected": ["a.md"]},
            predicted_self_change={"confidence_change": 0.1},
            predicted_homeostatic_effect={"novelty_balance": 0.2},
            confidence=0.7,
            created_at="2026-04-13T00:00:00Z",
        )
        assert p.resolved is False
        assert p.prediction_error is None
        assert p.cached is False

    def test_default_factories_are_independent(self):
        """Mutating one kernel's fields must not affect another."""
        from app.subia.kernel import SubjectivityKernel
        k1 = SubjectivityKernel()
        k2 = SubjectivityKernel()
        k1.self_state.current_goals.append("goal A")
        assert k2.self_state.current_goals == []


class TestSubpackages:
    def test_subpackages_importable(self):
        from app.subia import scene, self as subself, homeostasis, belief
        from app.subia import prediction, social, memory, safety, probes
        from app.subia import wiki_surface
        # Just verify the modules exist — implementations arrive per phase.
        for mod in (scene, subself, homeostasis, belief, prediction,
                    social, memory, safety, probes, wiki_surface):
            assert mod is not None
