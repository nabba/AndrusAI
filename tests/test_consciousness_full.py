"""
Consciousness & Sentience System — Definitive Test Suite
==========================================================

Tests ALL 27 sentience modules, their wiring, data flows, recursive
self-awareness, safety invariants, consciousness probes, Beautiful Loop,
prosocial learning, emergent infrastructure, and RLIF training.

27 modules · 6,302 lines · All phases verified

Run: docker exec crewai-team-gateway-1 python3 -m pytest /app/tests/test_consciousness_full.py -v
"""

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ════════════════════════════════════════════════════════════════════════════════
# 1. ALL 27 MODULES IMPORT
# ════════════════════════════════════════════════════════════════════════════════

class TestAllModulesImport:
    """Every sentience module must import without errors."""

    @pytest.mark.parametrize("module", [
        "app.self_awareness.internal_state",
        "app.self_awareness.state_logger",
        "app.self_awareness.certainty_vector",
        "app.self_awareness.somatic_marker",
        "app.self_awareness.dual_channel",
        "app.self_awareness.meta_cognitive",
        "app.self_awareness.global_workspace",
        "app.self_awareness.sentience_config",
        "app.self_awareness.consciousness_probe",
        "app.self_awareness.cogito",
        "app.self_awareness.homeostasis",
        "app.self_awareness.agent_state",
        "app.self_awareness.world_model",
        "app.self_awareness.journal",
        "app.self_awareness.self_model",
        "app.self_awareness.grounding",
        "app.self_awareness.query_router",
        "app.self_awareness.inspect_tools",
        "app.self_awareness.knowledge_ingestion",
        "app.self_awareness.somatic_bias",
        "app.self_awareness.reality_model",
        "app.self_awareness.hyper_model",
        "app.self_awareness.precision_weighting",
        "app.self_awareness.inferential_competition",
        "app.self_awareness.behavioral_assessment",
        "app.self_awareness.emergent_infrastructure",
        "app.self_awareness.prosocial_learning",
        "app.training.rlif_certainty",
    ])
    def test_module_imports(self, module):
        __import__(module)


# ════════════════════════════════════════════════════════════════════════════════
# 2. CORE DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════════════════

class TestCertaintyVector:
    def test_fast_path_mean(self):
        from app.self_awareness.internal_state import CertaintyVector
        cv = CertaintyVector(factual_grounding=0.9, tool_confidence=0.6, coherence=0.3)
        assert cv.fast_path_mean == pytest.approx(0.6, abs=0.01)

    def test_adjusted_certainty_meta_discount(self):
        from app.self_awareness.internal_state import CertaintyVector
        cv = CertaintyVector(factual_grounding=0.8, tool_confidence=0.8, coherence=0.8,
                             task_understanding=0.8, value_alignment=0.8, meta_certainty=0.0)
        assert cv.adjusted_certainty == pytest.approx(0.4, abs=0.01)

    def test_slow_path_triggers_on_low(self):
        from app.self_awareness.internal_state import CertaintyVector
        assert CertaintyVector(factual_grounding=0.2).should_trigger_slow_path() is True
        assert CertaintyVector(factual_grounding=0.7, tool_confidence=0.7, coherence=0.7).should_trigger_slow_path() is False

    def test_to_dict_rounded(self):
        from app.self_awareness.internal_state import CertaintyVector
        assert CertaintyVector(factual_grounding=0.123456).to_dict()["factual_grounding"] == 0.123


class TestInternalState:
    def test_unique_ids(self):
        from app.self_awareness.internal_state import InternalState
        assert InternalState().state_id != InternalState().state_id

    def test_context_string_compact(self):
        from app.self_awareness.internal_state import InternalState
        s = InternalState(agent_id="test")
        assert "[Internal State]" in s.to_context_string()
        assert "Disposition=" in s.to_context_string()

    def test_context_string_shows_somatic(self):
        from app.self_awareness.internal_state import InternalState, SomaticMarker
        s = InternalState()
        s.somatic = SomaticMarker(valence=-0.8, intensity=0.9)
        assert "Somatic=negative" in s.to_context_string()

    def test_context_string_shows_hyper_model(self):
        from app.self_awareness.internal_state import InternalState
        s = InternalState()
        s.hyper_model_state = {"predicted_certainty": 0.6, "self_prediction_error": 0.15, "free_energy_trend": "decreasing"}
        ctx = s.to_context_string()
        assert "Self-Model" in ctx
        assert "surprise" in ctx

    def test_beautiful_loop_fields(self):
        from app.self_awareness.internal_state import InternalState
        s = InternalState()
        assert s.hyper_model_state is None
        assert s.reality_model_summary is None
        assert s.competition_result is None
        assert s.precision_weighted_certainty == 0.5
        assert s.free_energy_proxy == 0.0

    def test_json_roundtrip(self):
        from app.self_awareness.internal_state import InternalState
        s = InternalState(agent_id="test", venture="plg")
        d = json.loads(s.to_json())
        assert d["agent_id"] == "test"

    def test_disposition_constants(self):
        from app.self_awareness.internal_state import DISPOSITION_TO_RISK_TIER
        assert DISPOSITION_TO_RISK_TIER["proceed"] < DISPOSITION_TO_RISK_TIER["escalate"]


# ════════════════════════════════════════════════════════════════════════════════
# 3. DUAL-CHANNEL COMPOSITION
# ════════════════════════════════════════════════════════════════════════════════

class TestDualChannel:
    def _state(self, cert, val):
        from app.self_awareness.internal_state import InternalState, CertaintyVector, SomaticMarker
        s = InternalState()
        s.certainty = CertaintyVector(factual_grounding=cert, tool_confidence=cert, coherence=cert,
                                       task_understanding=cert, value_alignment=cert, meta_certainty=1.0)
        s.somatic = SomaticMarker(valence=val, intensity=0.5)
        return s

    @pytest.mark.parametrize("cert,val,expected", [
        (0.8, 0.5, "proceed"), (0.8, 0.0, "proceed"), (0.8, -0.5, "cautious"),
        (0.55, 0.5, "proceed"), (0.55, 0.0, "cautious"), (0.55, -0.5, "pause"),
        (0.2, 0.5, "cautious"), (0.2, 0.0, "pause"), (0.2, -0.5, "escalate"),
    ])
    def test_all_9_matrix_cells(self, cert, val, expected):
        from app.self_awareness.dual_channel import DualChannelComposer
        assert DualChannelComposer().compose(self._state(cert, val)).action_disposition == expected

    def test_reads_sentience_config(self):
        from app.self_awareness.dual_channel import DualChannelComposer
        c = DualChannelComposer()
        assert hasattr(c, 'certainty_high')

    def test_matrix_completeness(self):
        from app.self_awareness.dual_channel import DISPOSITION_MATRIX
        assert len(DISPOSITION_MATRIX) == 9


# ════════════════════════════════════════════════════════════════════════════════
# 4. CERTAINTY VECTOR COMPUTER
# ════════════════════════════════════════════════════════════════════════════════

class TestCertaintyComputer:
    def test_fast_path(self):
        from app.self_awareness.certainty_vector import CertaintyVectorComputer
        cv = CertaintyVectorComputer().compute_fast_path(agent_id="t", current_output="text")
        assert 0 <= cv.factual_grounding <= 1

    def test_rag_estimation(self):
        from app.self_awareness.certainty_vector import CertaintyVectorComputer
        cv = CertaintyVectorComputer().compute_fast_path(
            agent_id="t", current_output="t", rag_source_count=4, total_claim_count=5)
        assert cv.factual_grounding == pytest.approx(0.8)

    def test_coherence_identical(self):
        from app.self_awareness.certainty_vector import CertaintyVectorComputer
        v = [1.0, 0.0, 0.0]
        assert CertaintyVectorComputer._compute_coherence(v, [v]) == pytest.approx(1.0, abs=0.01)

    def test_reads_config_thresholds(self):
        src = inspect.getsource(__import__("app.self_awareness.certainty_vector", fromlist=["_"])
                                .CertaintyVectorComputer.compute_full)
        assert "sentience_config" in src


# ════════════════════════════════════════════════════════════════════════════════
# 5. SOMATIC MARKER + BIAS + FORECAST
# ════════════════════════════════════════════════════════════════════════════════

class TestSomatic:
    def test_no_experience_neutral(self):
        from app.self_awareness.somatic_marker import SomaticMarkerComputer
        sm = SomaticMarkerComputer().compute(agent_id="new", decision_context="Novel")
        assert sm.valence == 0.0 and sm.match_count == 0

    def test_record_no_crash(self):
        from app.self_awareness.somatic_marker import record_experience_sync
        record_experience_sync(agent_id="test_s", context_summary="Test", outcome_score=0.5)

    def test_forecast_returns_somatic(self):
        from app.self_awareness.somatic_marker import SomaticMarkerComputer
        sm = SomaticMarkerComputer().forecast(agent_id="t", proposed_action="Deploy code")
        assert "forecast" in sm.source

    def test_somatic_bias_negative_sets_floor(self):
        from app.self_awareness.somatic_bias import SomaticBiasInjector
        from app.self_awareness.internal_state import SomaticMarker
        inj = SomaticBiasInjector()
        bias = inj._compute_bias(SomaticMarker(valence=-0.7, intensity=0.9))
        assert bias and bias["disposition_floor"] == "cautious"

    def test_somatic_bias_positive_no_floor(self):
        from app.self_awareness.somatic_bias import SomaticBiasInjector
        from app.self_awareness.internal_state import SomaticMarker
        bias = SomaticBiasInjector()._compute_bias(SomaticMarker(valence=0.8, intensity=0.9))
        assert bias is None or bias.get("disposition_floor") is None

    def test_somatic_bias_injects_context(self):
        from app.self_awareness.somatic_bias import SomaticBiasInjector
        from app.self_awareness.internal_state import SomaticMarker
        ctx = {"description": "Original task"}
        result = SomaticBiasInjector().inject(ctx, SomaticMarker(valence=-0.7, intensity=0.9))
        assert "[Somatic note:" in result.get("description", "")


# ════════════════════════════════════════════════════════════════════════════════
# 6. META-COGNITIVE LAYER
# ════════════════════════════════════════════════════════════════════════════════

class TestMetaCognitive:
    def test_first_step(self):
        from app.self_awareness.meta_cognitive import MetaCognitiveLayer
        _, meta = MetaCognitiveLayer(agent_id="t").pre_reasoning_hook({"description": "Test"}, None)
        assert meta.compute_phase in ("early", "mid", "late")

    def test_no_reassess_late(self):
        from app.self_awareness.meta_cognitive import MetaCognitiveLayer
        assert MetaCognitiveLayer(agent_id="t")._should_reassess(None, "late") is False

    def test_context_modification_append_only(self):
        from app.self_awareness.meta_cognitive import MetaCognitiveLayer
        r = MetaCognitiveLayer._apply_context_modification(
            {"description": "Orig"}, {"type": "add_strategy_hint", "content": "Hint"})
        assert "Orig" in r["description"]

    def test_compute_phase(self):
        from app.self_awareness.meta_cognitive import MetaCognitiveLayer
        assert MetaCognitiveLayer._compute_phase({"remaining_pct": 0.8}) == "early"
        assert MetaCognitiveLayer._compute_phase({"remaining_pct": 0.1}) == "late"


# ════════════════════════════════════════════════════════════════════════════════
# 7. BEAUTIFUL LOOP (Phase 7)
# ════════════════════════════════════════════════════════════════════════════════

class TestRealityModel:
    def test_build(self):
        from app.self_awareness.reality_model import RealityModelBuilder
        rm = RealityModelBuilder().build(agent_id="t", step_number=0, task_description="Test task")
        assert len(rm.elements) >= 1
        assert rm.elements[0].category == "task"

    def test_precision_sorted(self):
        from app.self_awareness.reality_model import RealityModel, WorldModelElement
        rm = RealityModel(agent_id="t", step_number=0)
        rm.add_element(WorldModelElement(element_id="a", category="fact", content="A", precision=0.3, source="rag"))
        rm.add_element(WorldModelElement(element_id="b", category="fact", content="B", precision=0.9, source="rag"))
        assert rm.high_precision_elements[0].precision == 0.9
        assert rm.low_precision_elements[0].precision == 0.3

    def test_context_string(self):
        from app.self_awareness.reality_model import RealityModelBuilder
        rm = RealityModelBuilder().build(agent_id="t", step_number=0, task_description="Test")
        ctx = rm.to_context_string()
        assert "[World Model]" in ctx


class TestHyperModel:
    def test_predict_default(self):
        from app.self_awareness.hyper_model import HyperModel
        hm = HyperModel(agent_id="t")
        assert hm.predict_next_step() == 0.5

    def test_update_computes_error(self):
        from app.self_awareness.hyper_model import HyperModel
        hm = HyperModel(agent_id="t")
        hm.predict_next_step()
        state = hm.update(0.8)
        assert state.self_prediction_error == pytest.approx(0.3, abs=0.01)
        assert state.actual_certainty == 0.8

    def test_context_injection(self):
        from app.self_awareness.hyper_model import HyperModel
        hm = HyperModel(agent_id="t")
        ctx = hm.get_context_injection()
        assert "[Self-Model]" in ctx


class TestPrecisionWeighting:
    def test_task_profiles_exist(self):
        from app.self_awareness.precision_weighting import TASK_TYPE_PRECISION_PROFILES
        assert "research" in TASK_TYPE_PRECISION_PROFILES
        assert "coding" in TASK_TYPE_PRECISION_PROFILES
        assert "writing" in TASK_TYPE_PRECISION_PROFILES

    def test_apply_weights(self):
        from app.self_awareness.precision_weighting import PrecisionWeighting
        from app.self_awareness.internal_state import CertaintyVector
        cv = CertaintyVector(factual_grounding=0.9, tool_confidence=0.3)
        pw = PrecisionWeighting()
        result = pw.apply_weights(cv, "research")
        assert isinstance(result, (float, dict, CertaintyVector)) or result is not None


class TestInferentialCompetition:
    def test_should_compete_low_certainty(self):
        from app.self_awareness.inferential_competition import InferentialCompetition
        ic = InferentialCompetition()
        assert ic.should_compete(0.2, 0.1, 1) is True

    def test_should_not_compete_high_certainty(self):
        from app.self_awareness.inferential_competition import InferentialCompetition
        ic = InferentialCompetition()
        assert ic.should_compete(0.8, 0.1, 5) is False

    def test_should_compete_first_step(self):
        from app.self_awareness.inferential_competition import InferentialCompetition
        assert InferentialCompetition().should_compete(0.8, 0.1, 0) is True


# ════════════════════════════════════════════════════════════════════════════════
# 8. GLOBAL WORKSPACE (GWT)
# ════════════════════════════════════════════════════════════════════════════════

class TestGlobalWorkspace:
    def test_singleton(self):
        from app.self_awareness.global_workspace import get_workspace
        assert get_workspace() is get_workspace()

    def test_broadcast_receive(self):
        from app.self_awareness.global_workspace import get_workspace, broadcast
        broadcast("GWT test msg", importance="high", source_agent="test")
        unread = get_workspace().check_broadcasts("gwt_receiver")
        assert len(unread) >= 1

    def test_read_marks_as_read(self):
        from app.self_awareness.global_workspace import get_workspace, broadcast
        broadcast("Unique mark test", importance="high", source_agent="t")
        ws = get_workspace()
        ws.check_broadcasts("mark_agent")
        assert len(ws.check_broadcasts("mark_agent")) == 0


# ════════════════════════════════════════════════════════════════════════════════
# 9. SENTIENCE CONFIG
# ════════════════════════════════════════════════════════════════════════════════

class TestSentienceConfig:
    def test_load(self):
        from app.self_awareness.sentience_config import load_config, DEFAULTS
        cfg = load_config()
        assert len(cfg) >= len(DEFAULTS)

    def test_bounds_enforced(self):
        from app.self_awareness.sentience_config import propose_change
        assert propose_change("certainty_low_threshold", 0.0)[0] is False

    def test_valid_change(self):
        from app.self_awareness.sentience_config import propose_change
        assert propose_change("certainty_low_threshold", 0.42)[0] is True

    def test_7_params(self):
        from app.self_awareness.sentience_config import PARAM_BOUNDS
        assert len(PARAM_BOUNDS) == 7


# ════════════════════════════════════════════════════════════════════════════════
# 10. CONSCIOUSNESS PROBES
# ════════════════════════════════════════════════════════════════════════════════

class TestConsciousnessProbes:
    def test_all_7_probes(self):
        from app.self_awareness.consciousness_probe import run_consciousness_probes
        r = run_consciousness_probes()
        assert len(r.probes) == 7

    def test_indicators(self):
        from app.self_awareness.consciousness_probe import run_consciousness_probes
        indicators = {p.indicator for p in run_consciousness_probes().probes}
        assert indicators == {"HOT-2", "HOT-3", "GWT", "SM-A", "WM-A", "SOM", "INT"}

    def test_scores_bounded(self):
        from app.self_awareness.consciousness_probe import run_consciousness_probes
        for p in run_consciousness_probes().probes:
            assert 0.0 <= p.score <= 1.0

    def test_composite_bounded(self):
        from app.self_awareness.consciousness_probe import run_consciousness_probes
        assert 0.0 <= run_consciousness_probes().composite_score <= 1.0


# ════════════════════════════════════════════════════════════════════════════════
# 11. BEHAVIORAL ASSESSMENT (Phase 8)
# ════════════════════════════════════════════════════════════════════════════════

class TestBehavioralAssessment:
    def test_import(self):
        from app.self_awareness.behavioral_assessment import run_behavioral_assessment, BehavioralAssessor
        assert callable(run_behavioral_assessment)

    def test_assessor_has_markers(self):
        from app.self_awareness.behavioral_assessment import BehavioralAssessor
        a = BehavioralAssessor()
        assert hasattr(a, 'assess_agent')

    def test_scheduled(self):
        src = inspect.getsource(__import__("app.idle_scheduler", fromlist=["_"]))
        assert "behavioral-assessment" in src


# ════════════════════════════════════════════════════════════════════════════════
# 12. EMERGENT INFRASTRUCTURE (Phase 9)
# ════════════════════════════════════════════════════════════════════════════════

class TestEmergentInfra:
    def test_safety_scan_catches_forbidden(self):
        from app.self_awareness.emergent_infrastructure import EmergentInfrastructureManager
        issues = EmergentInfrastructureManager()._safety_scan("import subprocess")
        assert len(issues) > 0

    def test_clean_code_passes(self):
        from app.self_awareness.emergent_infrastructure import EmergentInfrastructureManager
        assert len(EmergentInfrastructureManager()._safety_scan("def f(x): return x*2")) == 0

    def test_proposal_submission(self):
        from app.self_awareness.emergent_infrastructure import EmergentInfrastructureManager, ToolProposal, ProposalStatus
        p = ToolProposal(proposal_id="test_p", agent_id="t", tool_name="helper",
                          tool_description="Test", justification="Test",
                          tool_code="def helper(x): return x*2")
        r = EmergentInfrastructureManager().submit_proposal(p)
        assert r.status == ProposalStatus.SENT_FOR_REVIEW

    def test_forbidden_patterns(self):
        from app.self_awareness.emergent_infrastructure import FORBIDDEN_PATTERNS
        assert "subprocess" in FORBIDDEN_PATTERNS
        assert "os.system" in FORBIDDEN_PATTERNS


# ════════════════════════════════════════════════════════════════════════════════
# 13. PROSOCIAL LEARNING (Phase 10)
# ════════════════════════════════════════════════════════════════════════════════

class TestProsocialLearning:
    def test_5_game_types(self):
        from app.self_awareness.prosocial_learning import GameType
        assert len(GameType) == 5

    def test_profile_update(self):
        from app.self_awareness.prosocial_learning import ProsocialProfile, GameType
        p = ProsocialProfile(agent_id="t")
        p.update_from_outcome(GameType.RESOURCE_SHARING, 1.0)
        assert p.generosity > 0.5

    def test_scoring(self):
        from app.self_awareness.prosocial_learning import ProsocialSimulator, GameType
        sim = ProsocialSimulator(rounds_per_game=1)
        ind, coll, pros = sim._score_round(
            GameType.RESOURCE_SHARING, {"a": "share_equal", "b": "keep_all"}, ["a", "b"])
        assert pros["a"] > pros["b"]  # Sharing is more prosocial

    def test_scenarios_complete(self):
        from app.self_awareness.prosocial_learning import SCENARIOS, GameType
        for gt in GameType:
            assert gt in SCENARIOS

    def test_scheduled(self):
        src = inspect.getsource(__import__("app.idle_scheduler", fromlist=["_"]))
        assert "prosocial-learning" in src


# ════════════════════════════════════════════════════════════════════════════════
# 14. RLIF + TRAJECTORY ENTROPY (Phase 6+)
# ════════════════════════════════════════════════════════════════════════════════

class TestRLIF:
    def test_curation_weight(self):
        from app.training.rlif_certainty import SelfCertaintyScorer
        assert SelfCertaintyScorer.compute_curation_weight(0.9, 0.9) > 0.7
        assert SelfCertaintyScorer.compute_curation_weight(0.1, 0.9) < 0.5

    def test_entropy_collapse(self):
        from app.training.rlif_certainty import EntropyCollapseMonitor
        m = EntropyCollapseMonitor(window_size=15, variance_threshold=0.01, mean_ceiling=0.85)
        warning = None
        for _ in range(20):
            warning = m.check_batch([0.88])
        assert warning and "ENTROPY_COLLAPSE" in warning

    def test_trajectory_entropy_identical(self):
        from app.training.rlif_certainty import TrajectoryEntropyScorer
        e = TrajectoryEntropyScorer.compute_trajectory_entropy_from_embeddings([[1, 0, 0]] * 3)
        assert e == pytest.approx(0.0, abs=0.01)

    def test_trajectory_entropy_diverse(self):
        from app.training.rlif_certainty import TrajectoryEntropyScorer
        e = TrajectoryEntropyScorer.compute_trajectory_entropy_from_embeddings(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        assert e > 0.3

    def test_combine_signals(self):
        from app.training.rlif_certainty import TrajectoryEntropyScorer
        high = TrajectoryEntropyScorer.combine_signals(0.8, 0.2)
        low = TrajectoryEntropyScorer.combine_signals(0.3, 0.8)
        assert high > low

    def test_wired_into_training(self):
        src = inspect.getsource(__import__("app.training_collector", fromlist=["_"]))
        assert "SelfCertaintyScorer" in src


# ════════════════════════════════════════════════════════════════════════════════
# 15. COGITO FEEDBACK LOOP
# ════════════════════════════════════════════════════════════════════════════════

class TestCogitoFeedback:
    def test_apply_proposals_covers_all_params(self):
        src = inspect.getsource(__import__("app.self_awareness.cogito", fromlist=["CogitoCycle"])
                                .CogitoCycle._apply_proposals)
        for param in ("certainty_low", "certainty_high", "slow_path_trigger",
                       "slow_path_variance", "valence_negative", "reassessment_cooldown"):
            assert param in src, f"Missing {param}"

    def test_calls_apply_change(self):
        src = inspect.getsource(__import__("app.self_awareness.cogito", fromlist=["CogitoCycle"])
                                .CogitoCycle._apply_proposals)
        assert src.count("apply_change(") >= 8


# ════════════════════════════════════════════════════════════════════════════════
# 16. HOOK REGISTRATION
# ════════════════════════════════════════════════════════════════════════════════

class TestHookRegistration:
    def test_inject_internal_state_p5(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        hooks = get_registry()._hooks.get(HookPoint.PRE_TASK, [])
        h = [h for h in hooks if h.name == "inject_internal_state"]
        assert len(h) == 1 and h[0].priority == 5

    def test_meta_cognitive_p15(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        hooks = get_registry()._hooks.get(HookPoint.PRE_TASK, [])
        h = [h for h in hooks if h.name == "meta_cognitive"]
        assert len(h) == 1 and h[0].priority == 15

    def test_internal_state_p8(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        hooks = get_registry()._hooks.get(HookPoint.POST_LLM_CALL, [])
        h = [h for h in hooks if h.name == "internal_state"]
        assert len(h) == 1 and h[0].priority == 8

    def test_training_data_registered(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        hooks = get_registry()._hooks.get(HookPoint.POST_LLM_CALL, [])
        assert any(h.name == "training_data" for h in hooks)


# ════════════════════════════════════════════════════════════════════════════════
# 17. HOOK EXECUTION (ACTUALLY FIRES)
# ════════════════════════════════════════════════════════════════════════════════

class TestHookExecution:
    def test_pre_task_fires(self):
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        ctx = HookContext(hook_point=HookPoint.PRE_TASK, agent_id="fire_test",
                          task_description="Test")
        r = get_registry().execute(HookPoint.PRE_TASK, ctx)
        assert r.metadata.get("_meta_cognitive_state") is not None

    def test_post_llm_fires(self):
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        ctx = HookContext(
            hook_point=HookPoint.POST_LLM_CALL, agent_id="fire_test",
            data={"llm_response": "Result with https://source.com and according to experts."},
            metadata={"crew": "test"},
        )
        r = get_registry().execute(HookPoint.POST_LLM_CALL, ctx)
        state = r.metadata.get("_internal_state")
        assert state is not None
        assert state.certainty.factual_grounding > 0.5  # Has source markers

    def test_post_llm_produces_hyper_model(self):
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        ctx = HookContext(
            hook_point=HookPoint.POST_LLM_CALL, agent_id="hm_test",
            data={"llm_response": "Some output"},
            metadata={"crew": "test"},
        )
        r = get_registry().execute(HookPoint.POST_LLM_CALL, ctx)
        state = r.metadata.get("_internal_state")
        assert state.hyper_model_state is not None

    def test_post_llm_produces_precision_weighted(self):
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        ctx = HookContext(
            hook_point=HookPoint.POST_LLM_CALL, agent_id="pw_test",
            data={"llm_response": "Output"},
            metadata={"crew": "research"},
        )
        r = get_registry().execute(HookPoint.POST_LLM_CALL, ctx)
        state = r.metadata.get("_internal_state")
        assert 0.0 <= state.precision_weighted_certainty <= 1.0


# ════════════════════════════════════════════════════════════════════════════════
# 18. RECURSIVE SELF-AWARENESS
# ════════════════════════════════════════════════════════════════════════════════

class TestRecursive:
    def test_state_carries(self):
        """Verify inject_internal_state hook (priority 5) injects previous state."""
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        from app.self_awareness.internal_state import InternalState, CertaintyVector

        state = InternalState(agent_id="rec", action_disposition="proceed")
        state.certainty = CertaintyVector(factual_grounding=0.9, tool_confidence=0.9, coherence=0.9)
        state.hyper_model_state = {"predicted_certainty": 0.8, "self_prediction_error": 0.05}

        # Only fire the inject hook (priority 5), skip meta_cognitive (priority 15)
        # to avoid triggering slow local LLM calls
        inject_hooks = [h for h in get_registry()._hooks.get(HookPoint.PRE_TASK, [])
                        if h.name == "inject_internal_state"]
        assert len(inject_hooks) == 1

        ctx = HookContext(hook_point=HookPoint.PRE_TASK, agent_id="rec",
                          task_description="Step 2", metadata={"_internal_state": state})
        result = inject_hooks[0].fn(ctx)
        assert "[Internal State]" in result.modified_data.get("task_description", "")


# ════════════════════════════════════════════════════════════════════════════════
# 19. PRE_TASK HOOK FEATURES (Phase 3R + 7)
# ════════════════════════════════════════════════════════════════════════════════

class TestPreTaskFeatures:
    def test_has_somatic_bias(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        h = [h for h in get_registry()._hooks.get(HookPoint.PRE_TASK, []) if h.name == "meta_cognitive"][0]
        src = inspect.getsource(h.fn)
        assert "SomaticBiasInjector" in src

    def test_has_reality_model(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        h = [h for h in get_registry()._hooks.get(HookPoint.PRE_TASK, []) if h.name == "meta_cognitive"][0]
        assert "RealityModelBuilder" in inspect.getsource(h.fn)

    def test_has_inferential_competition(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        h = [h for h in get_registry()._hooks.get(HookPoint.PRE_TASK, []) if h.name == "meta_cognitive"][0]
        assert "InferentialCompetition" in inspect.getsource(h.fn)

    def test_has_should_compete_gating(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        h = [h for h in get_registry()._hooks.get(HookPoint.PRE_TASK, []) if h.name == "meta_cognitive"][0]
        assert "should_compete" in inspect.getsource(h.fn)


# ════════════════════════════════════════════════════════════════════════════════
# 20. POST_LLM_CALL HOOK FEATURES
# ════════════════════════════════════════════════════════════════════════════════

class TestPostLLMFeatures:
    def _hook_src(self):
        from app.lifecycle_hooks import get_registry, HookPoint
        h = [h for h in get_registry()._hooks.get(HookPoint.POST_LLM_CALL, []) if h.name == "internal_state"][0]
        return inspect.getsource(h.fn)

    def test_rag_estimation(self): assert "source_markers" in self._hook_src()
    def test_shared_embedding(self): assert "shared_embedding" in self._hook_src()
    def test_hyper_model(self): assert "HyperModel" in self._hook_src()
    def test_precision_weighting(self): assert "PrecisionWeighting" in self._hook_src()
    def test_reality_model_stored(self): assert "reality_model_summary" in self._hook_src()
    def test_competition_stored(self): assert "competition_result" in self._hook_src()
    def test_gwt_broadcast(self): assert "broadcast" in self._hook_src()
    def test_state_logged(self): assert "sl.log" in self._hook_src()


# ════════════════════════════════════════════════════════════════════════════════
# 21. ORCHESTRATOR WIRING
# ════════════════════════════════════════════════════════════════════════════════

class TestOrchestratorWiring:
    def _src(self):
        return inspect.getsource(__import__("app.agents.commander.orchestrator", fromlist=["Commander"]))

    def test_pre_task_execute(self): assert "execute(HookPoint.PRE_TASK" in self._src()
    def test_post_llm_execute(self): assert "execute(HookPoint.POST_LLM_CALL" in self._src()
    def test_state_carry(self): assert "_last_internal_state" in self._src()
    def test_experience_recording(self): assert "record_experience_sync" in self._src()
    def test_gwt_broadcast_injection(self): assert "_load_global_workspace_broadcasts" in self._src()
    def test_context_injection(self): assert "to_context_string" in self._src()


# ════════════════════════════════════════════════════════════════════════════════
# 22. SAFETY INVARIANTS
# ════════════════════════════════════════════════════════════════════════════════

class TestSafety:
    def test_p0_immutable(self):
        from app.lifecycle_hooks import get_registry
        for hp, hooks in get_registry()._hooks.items():
            for h in hooks:
                if h.priority <= 1:
                    assert h.immutable, f"{h.name} p={h.priority} must be immutable"

    def test_low_certainty_never_proceeds(self):
        from app.self_awareness.dual_channel import DISPOSITION_MATRIX
        assert all(v != "proceed" for k, v in DISPOSITION_MATRIX.items() if k[0] == "low")

    def test_risk_tier_monotonic(self):
        from app.self_awareness.internal_state import DISPOSITION_TO_RISK_TIER as D
        assert D["proceed"] < D["cautious"] < D["pause"] < D["escalate"]

    def test_meta_cognitive_context_only(self):
        src = inspect.getsource(__import__("app.self_awareness.meta_cognitive", fromlist=["_"]))
        assert "NEVER modifies agent code" in src

    def test_config_bounds(self):
        from app.self_awareness.sentience_config import PARAM_BOUNDS
        for p, (lo, hi) in PARAM_BOUNDS.items():
            assert lo < hi

    def test_state_logging_non_fatal(self):
        src = inspect.getsource(__import__("app.self_awareness.state_logger", fromlist=["_"])
                                .InternalStateLogger.log)
        assert "except Exception" in src

    def test_entropy_collapse_hard_stop(self):
        from app.training.rlif_certainty import EntropyCollapseMonitor
        m = EntropyCollapseMonitor(window_size=15, variance_threshold=0.01, mean_ceiling=0.85)
        assert any(m.check_batch([0.88]) for _ in range(20))

    def test_emergent_infra_forbidden(self):
        from app.self_awareness.emergent_infrastructure import EmergentInfrastructureManager
        assert len(EmergentInfrastructureManager()._safety_scan("import subprocess; os.system('rm')")) >= 2


# ════════════════════════════════════════════════════════════════════════════════
# 23. DASHBOARD + BATCH JOBS
# ════════════════════════════════════════════════════════════════════════════════

class TestDashboardAndBatch:
    def test_heartbeat_reports_internal_state(self):
        src = inspect.getsource(__import__("app.firebase.publish", fromlist=["heartbeat"]).heartbeat)
        assert "report_internal_state" in src

    def test_consciousness_probes_callable(self):
        from app.firebase.publish import report_consciousness_probes
        assert callable(report_consciousness_probes)

    @pytest.mark.parametrize("job", [
        "cogito-cycle", "consciousness-probe", "behavioral-assessment", "prosocial-learning",
    ])
    def test_batch_job_registered(self, job):
        from app.idle_scheduler import _default_jobs
        assert any(n == job for n, _ in _default_jobs())


# ════════════════════════════════════════════════════════════════════════════════
# 24. INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_state_logged_to_db(self):
        from app.self_awareness.state_logger import get_state_logger
        from app.self_awareness.internal_state import InternalState, CertaintyVector
        sl = get_state_logger()
        s = InternalState(agent_id="integ_db_test")
        s.certainty = CertaintyVector(factual_grounding=0.9)
        sl.log(s)
        from app.control_plane.db import execute
        r = execute("SELECT 1 FROM internal_states WHERE agent_id=%s LIMIT 1",
                    ("integ_db_test",), fetch=True)
        assert r is not None

    def test_full_pipeline(self):
        from app.lifecycle_hooks import get_registry, HookPoint, HookContext
        pre = HookContext(hook_point=HookPoint.PRE_TASK, agent_id="pipeline",
                          task_description="Test pipeline")
        get_registry().execute(HookPoint.PRE_TASK, pre)
        post = HookContext(
            hook_point=HookPoint.POST_LLM_CALL, agent_id="pipeline",
            data={"llm_response": "According to https://arxiv.org study, the findings show."},
            metadata={"crew": "research"},
        )
        r = get_registry().execute(HookPoint.POST_LLM_CALL, post)
        s = r.metadata.get("_internal_state")
        assert s is not None
        assert s.certainty.factual_grounding > 0
        assert s.action_disposition in ("proceed", "cautious", "pause", "escalate")
        assert s.hyper_model_state is not None
        assert 0.0 <= s.precision_weighted_certainty <= 1.0

    def test_consciousness_probe_run(self):
        from app.self_awareness.consciousness_probe import run_consciousness_probes
        r = run_consciousness_probes()
        assert 0.0 <= r.composite_score <= 1.0 and len(r.probes) == 7

    def test_postgresql_tables(self):
        from app.control_plane.db import execute
        for t in ("internal_states", "agent_experiences", "tool_proposals",
                   "prosocial_profiles", "prosocial_game_outcomes"):
            r = execute(f"SELECT 1 FROM {t} LIMIT 1", fetch=True)
            assert r is not None  # Table exists


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
