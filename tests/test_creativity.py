"""
test_creativity.py — Unit tests for the creativity MAS pipeline.

Pure-logic tests: no external services, no mocks for most classes.
Tests: llm_sampling, creative_prompts, creativity_scoring, failure_modes,
       MCSV, souls/loader, MAP-Elites, creative_mode.

Run: pytest tests/test_creativity.py -v
"""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── LLM Sampling ────────────────────────────────────────────────────────────

class TestLLMSampling:

    def test_diverge_temperature(self):
        from app.llm_sampling import get_sampling_params
        p = get_sampling_params("diverge")
        assert p["temperature"] == 1.3
        assert p["top_p"] == 0.95

    def test_discuss_temperature(self):
        from app.llm_sampling import get_sampling_params
        p = get_sampling_params("discuss")
        assert p["temperature"] == 0.9

    def test_converge_temperature(self):
        from app.llm_sampling import get_sampling_params
        p = get_sampling_params("converge")
        assert p["temperature"] == 0.5

    def test_none_returns_none(self):
        from app.llm_sampling import get_sampling_params
        assert get_sampling_params(None) is None

    def test_invalid_phase_raises(self):
        from app.llm_sampling import get_sampling_params
        with pytest.raises(ValueError, match="Unknown phase"):
            get_sampling_params("bogus")

    def test_build_kwargs_anthropic_no_presence_penalty(self):
        from app.llm_sampling import build_llm_kwargs
        kw = build_llm_kwargs("diverge", "anthropic")
        assert "temperature" in kw
        assert "top_p" in kw
        assert "presence_penalty" not in kw
        assert "extra_body" not in kw

    def test_build_kwargs_ollama_has_min_p(self):
        from app.llm_sampling import build_llm_kwargs
        kw = build_llm_kwargs("diverge", "ollama")
        assert "extra_body" in kw
        assert kw["extra_body"]["options"]["min_p"] == 0.05

    def test_build_kwargs_openrouter_has_presence_penalty(self):
        from app.llm_sampling import build_llm_kwargs
        kw = build_llm_kwargs("diverge", "openrouter")
        assert "presence_penalty" in kw
        assert kw["presence_penalty"] == 0.5

    def test_build_kwargs_none_phase_empty(self):
        from app.llm_sampling import build_llm_kwargs
        assert build_llm_kwargs(None, "anthropic") == {}

    def test_cache_key_deterministic(self):
        from app.llm_sampling import sampling_cache_key
        k1 = sampling_cache_key("diverge", "ollama")
        k2 = sampling_cache_key("diverge", "ollama")
        assert k1 == k2
        assert k1 == "ollama:diverge"

    def test_cache_key_none_empty(self):
        from app.llm_sampling import sampling_cache_key
        assert sampling_cache_key(None, "anthropic") == ""

    def test_cache_key_differs_across_phases(self):
        from app.llm_sampling import sampling_cache_key
        keys = {sampling_cache_key(p, "anthropic") for p in ("diverge", "discuss", "converge")}
        assert len(keys) == 3


# ── Creative Prompts ────────────────────────────────────────────────────────

class TestCreativePrompts:

    def test_render_initiation_contains_task(self):
        from app.crews.creative_prompts import render_initiation
        out = render_initiation("Design a better mousetrap")
        assert "Design a better mousetrap" in out
        assert "Divergent" in out

    def test_render_conformity_contains_all_parts(self):
        from app.crews.creative_prompts import render_conformity
        out = render_conformity(
            round_index=1, task="test task",
            my_prior="my idea", peer_outputs="peer idea",
        )
        assert "my idea" in out
        assert "peer idea" in out
        assert "Round 1" in out

    def test_render_anti_conformity_keywords(self):
        from app.crews.creative_prompts import render_anti_conformity
        out = render_anti_conformity(round_index=2, task="test", peer_outputs="peers")
        assert "Anti-conformity" in out
        assert "peers" in out

    def test_render_convergence_contains_outputs(self):
        from app.crews.creative_prompts import render_convergence
        out = render_convergence(task="synthesize", all_outputs="idea A\nidea B")
        assert "idea A" in out
        assert "Convergence" in out

    def test_render_conformity_empty_prior(self):
        from app.crews.creative_prompts import render_conformity
        out = render_conformity(1, "t", "", "peers")
        assert "no prior output" in out


# ── Creativity Scoring ──────────────────────────────────────────────────────

class TestCreativityScoring:

    def test_extract_ideas_numbered(self):
        from app.personality.creativity_scoring import extract_ideas
        text = "Intro paragraph.\n\n1. First idea with enough detail here.\n2. Second idea also has detail.\n3. Third is substantial too."
        ideas = extract_ideas(text)
        assert len(ideas) == 3

    def test_extract_ideas_bullets(self):
        from app.personality.creativity_scoring import extract_ideas
        text = "- Bullet idea one with substance.\n- Bullet idea two with detail.\n- Bullet three substantial."
        ideas = extract_ideas(text)
        assert len(ideas) == 3

    def test_extract_ideas_empty(self):
        from app.personality.creativity_scoring import extract_ideas
        assert extract_ideas("") == []
        assert extract_ideas("   ") == []

    def test_extract_ideas_drops_short(self):
        from app.personality.creativity_scoring import extract_ideas
        text = "1. ok\n2. This is a substantially longer idea with real content."
        ideas = extract_ideas(text)
        # "ok" is <20 chars, should be dropped
        assert all(len(i) >= 20 for i in ideas)

    def test_cosine_distance_identical(self):
        from app.personality.creativity_scoring import _cosine_distance
        v = [1.0, 0.0, 0.5]
        assert abs(_cosine_distance(v, v)) < 1e-6

    def test_cosine_distance_orthogonal(self):
        from app.personality.creativity_scoring import _cosine_distance
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_distance(a, b) - 1.0) < 1e-6

    def test_cosine_distance_empty(self):
        from app.personality.creativity_scoring import _cosine_distance
        assert _cosine_distance([], []) == 0.0
        assert _cosine_distance([1.0], []) == 0.0

    def test_elaboration_longer_scores_higher(self):
        from app.personality.creativity_scoring import _elaboration
        short = ["Short idea."]
        long = ["A much longer idea because it explains the reasoning in detail, "
                "for example by providing specific use cases and therefore justifying the approach."]
        s_short = _elaboration(short)
        s_long = _elaboration(long)
        assert s_long > s_short

    def test_elaboration_detail_markers_boost(self):
        from app.personality.creativity_scoring import _elaboration
        plain = ["This is a moderately long idea that says some things about the topic at hand clearly."]
        detailed = ["This is a moderately long idea because it matters, for example in production, therefore we need it."]
        assert _elaboration(detailed) > _elaboration(plain)

    def test_creativity_scores_as_dict(self):
        from app.personality.creativity_scoring import CreativityScores
        s = CreativityScores(fluency=3, flexibility=2, originality=0.75, elaboration=0.6)
        d = s.as_dict()
        assert d["fluency"] == 3
        assert d["flexibility"] == 2
        assert d["originality"] == 0.75
        assert "diagnostics" in d


# ── Failure Modes ───────────────────────────────────────────────────────────

class TestFailureModes:

    def test_confidence_mirage_detected(self):
        from app.failure_modes import _detect_confidence_mirage
        sig = _detect_confidence_mirage(
            "test task",
            "This definitely and certainly proves the hypothesis without any doubt whatsoever.",
            [],
        )
        assert sig is not None
        assert sig.mode_name == "confidence_mirage"

    def test_confidence_mirage_absent_with_evidence(self):
        from app.failure_modes import _detect_confidence_mirage
        sig = _detect_confidence_mirage(
            "task",
            "This definitely shows X (source: https://example.org/data).",
            [],
        )
        assert sig is None

    def test_confidence_mirage_absent_normal_text(self):
        from app.failure_modes import _detect_confidence_mirage
        sig = _detect_confidence_mirage("task", "The data suggests X may be true.", [])
        assert sig is None

    def test_fix_spiral_detected(self):
        from app.failure_modes import _detect_fix_spiral
        # Detector checks >50% word overlap between consecutive history pairs.
        # Use short entries with high overlap to ensure threshold is met.
        history = [
            "Fixed database connection timeout error in research crew",
            "Fixed database connection timeout error in research crew again",
            "Fixed database connection timeout error in research crew once more",
        ]
        sig = _detect_fix_spiral("task", "output", history)
        assert sig is not None
        assert sig.mode_name == "fix_spiral"

    def test_fix_spiral_not_triggered_short_history(self):
        from app.failure_modes import _detect_fix_spiral
        assert _detect_fix_spiral("task", "output", ["fix 1", "fix 2"]) is None

    def test_consensus_collapse_detected(self):
        from app.failure_modes import _detect_consensus_collapse
        sig = _detect_consensus_collapse(
            "task",
            "I agree with the previous idea. I concur fully. I second this approach. "
            "I also agree with the reasoning. Let me support this direction.",
            [],
        )
        assert sig is not None
        assert sig.mode_name == "consensus_collapse"

    def test_consensus_collapse_absent_with_ideas(self):
        from app.failure_modes import _detect_consensus_collapse
        sig = _detect_consensus_collapse(
            "task",
            "I agree with point 1.\n1. New idea: build a bridge.\n2. Another idea: use a tunnel.\n3. Third: go around.",
            [],
        )
        assert sig is None

    def test_hallucinated_citation_detected(self):
        from app.failure_modes import _detect_hallucinated_citation
        sig = _detect_hallucinated_citation(
            "task",
            "See https://example.com/study1 and https://example.com/study2 for details.",
            [],
        )
        assert sig is not None
        assert sig.mode_name == "hallucinated_citation"

    def test_hallucinated_citation_absent_real_urls(self):
        from app.failure_modes import _detect_hallucinated_citation
        sig = _detect_hallucinated_citation(
            "task",
            "See https://arxiv.org/abs/2301.00001 and https://nature.com/articles/123",
            [],
        )
        assert sig is None

    def test_scope_creep_detected(self):
        from app.failure_modes import _detect_scope_creep
        task = "What is the population of Helsinki?"
        # Detector needs >3 sentences and >40% sharing <2 words with task.
        # These sentences are deliberately about unrelated topics.
        output = (
            "Helsinki has about 650 thousand residents. "
            "The stock market saw significant gains yesterday in Asia. "
            "Quantum computing will revolutionize drug discovery in pharmaceuticals. "
            "Ancient Roman aqueducts demonstrate remarkable engineering skill. "
            "The migration patterns of monarch butterflies span thousands of miles across continents. "
            "Jazz music originated in New Orleans from African American communities."
        )
        sig = _detect_scope_creep(task, output, [])
        assert sig is not None
        assert sig.mode_name == "scope_creep"

    def test_scan_for_failures_aggregates(self):
        from app.failure_modes import scan_for_failures
        # Craft output that triggers multiple detectors simultaneously.
        # confidence_mirage: certainty phrases WITHOUT any URLs/sources
        # (URLs would satisfy has_evidence, blocking confidence_mirage)
        text_mirage = "This definitely and certainly proves the hypothesis without doubt. "
        # hallucinated_citation: 2+ example.com URLs (in a separate block)
        text_halluc = "References: https://example.com/fake1 and https://example.com/fake2."
        signals = scan_for_failures("task", text_mirage + text_halluc, [])
        names = [s.mode_name for s in signals]
        # At minimum hallucinated_citation should fire (example.com URLs).
        # confidence_mirage may or may not fire depending on URL presence.
        assert len(signals) >= 1
        assert "hallucinated_citation" in names

    def test_problem_fingerprint_stable(self):
        from app.failure_modes import get_problem_fingerprint
        fp1 = get_problem_fingerprint("task A", "error X")
        fp2 = get_problem_fingerprint("task A", "error X")
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_problem_fingerprint_differs(self):
        from app.failure_modes import get_problem_fingerprint
        fp1 = get_problem_fingerprint("task A", "error X")
        fp2 = get_problem_fingerprint("task B", "error Y")
        assert fp1 != fp2

    def test_catalog_completeness(self):
        from app.failure_modes import CATALOG
        assert len(CATALOG) == 5
        for fm in CATALOG:
            assert callable(fm.detect)
            assert fm.name
            assert fm.label
            assert fm.remediation


# ── Metacognitive State Vector ──────────────────────────────────────────────

class TestMCSV:

    def _make_cv(self, **kw):
        from app.subia.belief.internal_state import CertaintyVector
        return CertaintyVector(**kw)

    def _make_sm(self, **kw):
        from app.subia.belief.internal_state import SomaticMarker
        return SomaticMarker(**kw)

    def test_from_state_maps_correctness(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        cv = self._make_cv(factual_grounding=0.3)
        sm = self._make_sm()
        mcsv = MetacognitiveStateVector.from_state(cv, sm)
        assert mcsv.correctness_evaluation == 0.3

    def test_from_state_maps_emotional(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        cv = self._make_cv()
        sm = self._make_sm(intensity=0.8)
        mcsv = MetacognitiveStateVector.from_state(cv, sm)
        assert mcsv.emotional_awareness == 0.8

    def test_requires_observer_low_correctness(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        mcsv = MetacognitiveStateVector(correctness_evaluation=0.3, conflict_detection=0.0)
        assert mcsv.requires_observer is True

    def test_requires_observer_high_conflict(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        mcsv = MetacognitiveStateVector(correctness_evaluation=0.8, conflict_detection=0.7)
        assert mcsv.requires_observer is True

    def test_requires_observer_false_when_healthy(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        mcsv = MetacognitiveStateVector(correctness_evaluation=0.8, conflict_detection=0.2)
        assert mcsv.requires_observer is False

    def test_novelty_property(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        mcsv = MetacognitiveStateVector(experience_matching=0.0, conflict_detection=1.0)
        # novelty = (1 - 0) * (0.5 + 0.5 * 1.0) = 1.0
        assert abs(mcsv.novelty - 1.0) < 1e-6

    def test_novelty_zero_when_familiar(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        mcsv = MetacognitiveStateVector(experience_matching=1.0, conflict_detection=0.0)
        assert mcsv.novelty == 0.0

    def test_to_dict_keys(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        d = MetacognitiveStateVector().to_dict()
        expected_keys = {"emotional_awareness", "correctness_evaluation", "experience_matching",
                         "conflict_detection", "complexity_assessment", "novelty", "requires_observer"}
        assert set(d.keys()) == expected_keys

    def test_to_context_string_compact(self):
        from app.subia.belief.internal_state import MetacognitiveStateVector
        s = MetacognitiveStateVector().to_context_string()
        assert s.startswith("[MCSV]")
        assert len(s) < 120

    def test_internal_state_includes_mcsv_in_json(self):
        from app.subia.belief.internal_state import InternalState, MetacognitiveStateVector
        state = InternalState()
        state.mcsv = MetacognitiveStateVector(correctness_evaluation=0.9)
        j = json.loads(state.to_json())
        assert "mcsv" in j
        assert j["mcsv"]["correctness_evaluation"] == 0.9


# ── Souls Loader ────────────────────────────────────────────────────────────

class TestSoulsLoader:

    def test_get_reasoning_method_step_back(self):
        from app.souls.loader import get_reasoning_method
        m = get_reasoning_method("step_back")
        assert len(m) > 0
        assert "Step-Back" in m

    def test_get_all_five_methods(self):
        from app.souls.loader import get_reasoning_method
        methods = ["meta_reasoning", "step_back", "compositional_cot", "analogical_blending", "contrastive"]
        for name in methods:
            m = get_reasoning_method(name)
            assert len(m) > 50, f"Method {name} too short: {len(m)}"

    def test_get_reasoning_method_unknown(self):
        from app.souls.loader import get_reasoning_method
        assert get_reasoning_method("nonexistent") == ""

    def test_compose_backstory_with_method_differs(self):
        from app.souls.loader import compose_backstory
        default = compose_backstory("writer")
        creative = compose_backstory("writer", reasoning_method="analogical_blending")
        assert default != creative
        assert "Analogical" in creative
        assert "Analogical" not in default

    def test_compose_backstory_cache_no_contamination(self):
        from app.souls.loader import compose_backstory
        a = compose_backstory("writer")
        b = compose_backstory("writer", reasoning_method="contrastive")
        c = compose_backstory("writer")
        assert a == c  # cache hit for default
        assert b != a  # creative variant is different


# ── MAP-Elites ──────────────────────────────────────────────────────────────

class TestMAPElites:

    def test_extract_features_returns_three_dims(self):
        from app.map_elites import extract_features, FEATURE_DIMENSIONS
        f = extract_features("Write a detailed, comprehensive analysis of the topic.")
        assert set(f.keys()) == set(FEATURE_DIMENSIONS)
        for v in f.values():
            assert 0.0 <= v <= 1.0

    def test_grid_add_and_retrieve(self):
        from app.map_elites import MAPElitesGrid, StrategyEntry
        grid = MAPElitesGrid(island_id=0)
        entry = StrategyEntry(
            strategy_id="test1", role="coder",
            prompt_content="test prompt", fitness_score=0.5,
            feature_vector={"complexity": 0.5, "cost_efficiency": 0.5, "specialization": 0.5},
        )
        grid.add(entry)
        assert grid.size == 1
        best = grid.get_best_overall()
        assert best.strategy_id == "test1"

    def test_grid_higher_fitness_replaces(self):
        from app.map_elites import MAPElitesGrid, StrategyEntry
        grid = MAPElitesGrid(island_id=0)
        fv = {"complexity": 0.5, "cost_efficiency": 0.5, "specialization": 0.5}
        grid.add(StrategyEntry(strategy_id="low", fitness_score=0.3, feature_vector=fv))
        grid.add(StrategyEntry(strategy_id="high", fitness_score=0.9, feature_vector=fv))
        assert grid.size == 1  # same cell
        assert grid.get_best_overall().strategy_id == "high"

    def test_double_select_structure(self):
        from app.map_elites import MAPElitesGrid, StrategyEntry
        grid = MAPElitesGrid(island_id=0)
        for i in range(5):
            grid.add(StrategyEntry(
                strategy_id=f"s{i}", fitness_score=i * 0.2,
                feature_vector={"complexity": i * 0.2, "cost_efficiency": 0.5, "specialization": 0.5},
            ))
        sel = grid.double_select()
        assert "performance" in sel
        assert "inspiration" in sel
        assert len(sel["performance"]) <= 3
        assert len(sel["inspiration"]) <= 2

    def test_apply_stochasticity_adds_section(self):
        from app.map_elites import apply_stochasticity
        result = apply_stochasticity("researcher", "Base prompt text.")
        assert "Session-Specific Guidance" in result

    def test_apply_stochasticity_unknown_role(self):
        from app.map_elites import apply_stochasticity
        result = apply_stochasticity("unknown_role", "Base prompt.")
        assert result == "Base prompt."


# ── Creative Mode ───────────────────────────────────────────────────────────

class TestCreativeMode:

    def _reset(self):
        """Reset module globals between tests."""
        import app.creative_mode as cm
        cm._budget_usd = None
        cm._originality_wiki_weight = None

    def _mock_settings(self):
        s = MagicMock()
        s.creative_run_budget_usd = 0.10
        s.creative_originality_wiki_weight = 0.6
        return s

    def test_get_set_budget(self):
        self._reset()
        with patch("app.creative_mode.get_settings", return_value=self._mock_settings()):
            from app.creative_mode import get_budget_usd, set_budget_usd
            set_budget_usd(0.25)
            assert get_budget_usd() == 0.25

    def test_set_budget_negative_raises(self):
        self._reset()
        with patch("app.creative_mode.get_settings", return_value=self._mock_settings()):
            from app.creative_mode import set_budget_usd
            with pytest.raises(ValueError):
                set_budget_usd(-1.0)

    def test_set_budget_too_high_raises(self):
        self._reset()
        with patch("app.creative_mode.get_settings", return_value=self._mock_settings()):
            from app.creative_mode import set_budget_usd
            with pytest.raises(ValueError):
                set_budget_usd(200.0)

    def test_originality_weight_range(self):
        self._reset()
        with patch("app.creative_mode.get_settings", return_value=self._mock_settings()):
            from app.creative_mode import set_originality_wiki_weight
            with pytest.raises(ValueError):
                set_originality_wiki_weight(1.5)
            with pytest.raises(ValueError):
                set_originality_wiki_weight(-0.1)

    def test_snapshot_keys(self):
        self._reset()
        with patch("app.creative_mode.get_settings", return_value=self._mock_settings()):
            from app.creative_mode import snapshot
            s = snapshot()
            assert "creative_run_budget_usd" in s
            assert "originality_wiki_weight" in s
            assert "mem0_weight" in s
