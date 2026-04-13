"""Phase 12 — Six Proposals integration tests.

Exercises every new subpackage and the bridges between them.
Closed-loop discipline asserted: every computed signal must produce
an observable behavioural change (per Phase 2 invariant).
"""
from __future__ import annotations

import random

import pytest

from app.subia.kernel import SceneItem, SubjectivityKernel
from app.subia.config import SUBIA_CONFIG


# ─────────────────────────────────────────────────────────────────────
# Boundary Sense (Proposal 5)
# ─────────────────────────────────────────────────────────────────────

def test_boundary_classifier_table_complete():
    from app.subia.boundary import classifier as c
    for mode in c.PROCESSING_MODES:
        assert mode in (c.INTROSPECTIVE, c.MEMORIAL, c.PERCEPTUAL,
                        c.IMAGINATIVE, c.SOCIAL)


def test_boundary_classify_known_sources():
    from app.subia.boundary import classify_source
    assert classify_source("mem0") == "memorial"
    assert classify_source("firecrawl") == "perceptual"
    assert classify_source("fiction_inspiration") == "imaginative"
    assert classify_source("social_model") == "social"


def test_boundary_classify_prefix_match_self():
    from app.subia.boundary import classify_source
    assert classify_source("wiki/self/identity.md", "wiki/self/identity.md") == "introspective"


def test_boundary_unknown_source_defaults_perceptual():
    from app.subia.boundary import classify_source
    assert classify_source("totally-unknown") == "perceptual"


def test_boundary_classify_scene_item_stamps_in_place():
    from app.subia.boundary import classify_scene_item
    item = SceneItem(id="i1", source="mem0", content_ref="x",
                     summary="memo", salience=0.5, entered_at="2026-01-01T00:00:00Z")
    mode = classify_scene_item(item)
    assert mode == "memorial"
    assert item.processing_mode == "memorial"


def test_boundary_homeostatic_modulator_known_pairs():
    from app.subia.boundary import homeostatic_modulator_for
    assert homeostatic_modulator_for("perceptual", "novelty_balance") > 1.0
    assert homeostatic_modulator_for("introspective", "self_coherence") > 1.0
    assert homeostatic_modulator_for(None, "novelty_balance") == 1.0


def test_boundary_consolidator_route_imaginative_speculative():
    from app.subia.boundary import consolidator_route_for
    r = consolidator_route_for("imaginative")
    assert r["epistemic_tag"] == "speculative"
    assert "reverie" in r["prefer"]


# ─────────────────────────────────────────────────────────────────────
# Wonder Register (Proposal 4)
# ─────────────────────────────────────────────────────────────────────

def test_wonder_no_signal_for_shallow_depth():
    from app.subia.wonder import detect_wonder, UnderstandingDepth
    sig = detect_wonder(UnderstandingDepth(causal_levels=1, cross_references=2))
    assert sig.intensity == 0.0
    assert not sig.inhibits_completion
    assert not sig.is_event


def test_wonder_multi_level_resonance_triggers():
    from app.subia.wonder import detect_wonder, UnderstandingDepth
    # multi-level (0.30) + structural-analogy span (0.15) = 0.45 > inhibit_threshold (0.3)
    sig = detect_wonder(UnderstandingDepth(
        causal_levels=3, cross_references=5, structural_analogies=2,
    ))
    assert sig.intensity > 0.30
    assert sig.inhibits_completion is True


def test_wonder_event_threshold():
    from app.subia.wonder import detect_wonder, UnderstandingDepth
    deep = UnderstandingDepth(
        causal_levels=4,
        cross_references=8,
        cross_domain_contradictions=3,
        recursive_structure_detected=True,
        epistemic_statuses=["creative", "factual"],
        structural_analogies=3,
    )
    sig = detect_wonder(deep)
    assert sig.intensity > 0.7
    assert sig.is_event


def test_wonder_apply_to_kernel_updates_homeostasis_and_scene():
    from app.subia.wonder import detect_wonder, UnderstandingDepth, apply_wonder_to_kernel
    kernel = SubjectivityKernel()
    kernel.scene.append(SceneItem(
        id="s1", source="wiki", content_ref="x",
        summary="deep page", salience=0.5,
        entered_at="2026-01-01T00:00:00Z",
    ))
    kernel.homeostasis.variables["wonder"] = 0.1
    sig = detect_wonder(UnderstandingDepth(causal_levels=3, cross_references=5))
    apply_wonder_to_kernel(kernel, sig, item_id="s1")
    assert kernel.homeostasis.variables["wonder"] > 0.1   # moved toward signal
    assert kernel.scene[0].wonder_intensity > 0.0


def test_wonder_should_inhibit_completion_via_kernel_var():
    from app.subia.wonder import register
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables["wonder"] = 0.6
    assert register.should_inhibit_completion(kernel) is True


def test_wonder_freeze_decay_predicate():
    from app.subia.wonder import register
    item = SceneItem(id="x", source="wiki", content_ref="", summary="",
                     salience=1.0, entered_at="t")
    item.wonder_intensity = 0.6
    assert register.freeze_decay_for(item) is True


# ─────────────────────────────────────────────────────────────────────
# Value Resonance (Proposal 6)
# ─────────────────────────────────────────────────────────────────────

def test_value_resonance_score_zero_for_neutral():
    from app.subia.values import score_item
    item = SceneItem(id="n", source="wiki", content_ref="x",
                     summary="schedule a meeting next week", salience=0.3,
                     entered_at="t")
    assert score_item(item).overall == 0.0


def test_value_resonance_dignity_keyword_fires():
    from app.subia.values import score_item, DIGNITY
    item = SceneItem(id="d", source="wiki", content_ref="x",
                     summary="protect human dignity and autonomy",
                     salience=0.4, entered_at="t")
    vr = score_item(item)
    assert DIGNITY in vr.channels
    assert vr.overall > 0.5


def test_value_resonance_apply_boosts_salience():
    from app.subia.values import apply_resonance_to_scene
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables = {
        v: 0.5 for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
    }
    item = SceneItem(id="i", source="wiki", content_ref="x",
                     summary="quality elegant work that respects user autonomy",
                     salience=0.4, entered_at="t")
    kernel.scene.append(item)
    n = apply_resonance_to_scene(kernel)
    assert n == 1
    assert item.salience > 0.4


def test_phronesis_lenses_socratic_emits_known_unknown():
    from app.subia.values.perceptual_lens import apply_lenses_to_homeostasis
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables = {
        v: 0.5 for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
    }
    kernel.scene.append(SceneItem(
        id="a", source="wiki", content_ref="",
        summary="this is obviously the case",
        salience=0.5, entered_at="t",
    ))
    agg = apply_lenses_to_homeostasis(kernel)
    assert agg.get("unexamined_assumption_alert", 0) >= 1
    assert any("socratic" in u for u in kernel.meta_monitor.known_unknowns)


# ─────────────────────────────────────────────────────────────────────
# Reverie Engine (Proposal 1)
# ─────────────────────────────────────────────────────────────────────

def _stub_reverie_adapters(*, find_resonance: bool = True):
    from app.subia.reverie import ReverieAdapters
    walk_nodes = [
        {"id": "p1", "title": "C2PA Provenance", "section": "archibal"},
        {"id": "p2", "title": "TikTok Resilience", "section": "kaicart"},
        {"id": "p3", "title": "Ticket Auth",      "section": "plg"},
    ]
    written: list = []
    return ReverieAdapters(
        pick_random_wiki_page=lambda: walk_nodes[0],
        walk_neo4j=lambda start, n: walk_nodes,
        fiction_search=lambda q: [{"chunk": "fiction!"}],
        philosophical_search=lambda q: [],
        mem0_full_search=lambda q, k: [{"id": "m1", "summary": "old memo"}],
        llm_resonance=(
            lambda a, b: f"{a} and {b} share a layered-verification pattern"
            if find_resonance else "no resonance"
        ),
        llm_synthesis=lambda concepts: "Speculative synthesis body.",
        write_reverie_page=lambda slug, body, fm: written.append((slug, body, fm)) or f"wiki/meta/reverie/{slug}.md",
        write_neo4j_analogy=lambda *a, **k: None,
    ), written


def test_reverie_cycle_writes_when_resonance_found():
    from app.subia.reverie import ReverieEngine
    adapters, written = _stub_reverie_adapters(find_resonance=True)
    engine = ReverieEngine(adapters, rng=random.Random(0))
    result = engine.run_cycle()
    assert result.synthesis_page is not None
    assert len(written) == 1
    assert result.resonances


def test_reverie_no_resonance_no_page():
    from app.subia.reverie import ReverieEngine
    adapters, written = _stub_reverie_adapters(find_resonance=False)
    engine = ReverieEngine(adapters, rng=random.Random(0))
    result = engine.run_cycle()
    assert result.synthesis_page is None
    assert written == []


def test_reverie_token_budget_within_proposal_estimate():
    """Proposal 1 §1.3: average ~700 tokens/cycle; cap below 2000."""
    from app.subia.reverie import ReverieEngine
    adapters, _ = _stub_reverie_adapters(find_resonance=True)
    engine = ReverieEngine(adapters, rng=random.Random(0),
                           fiction_probability=1.0,
                           philosophical_probability=1.0)
    result = engine.run_cycle()
    assert result.tokens_spent < 2000


# ─────────────────────────────────────────────────────────────────────
# Understanding Layer (Proposal 2)
# ─────────────────────────────────────────────────────────────────────

def _stub_understanding_adapters():
    from app.subia.understanding import UnderstandingAdapters
    written: list = []
    return UnderstandingAdapters(
        read_wiki_page=lambda p: {"body": "Truepic raised Series C.",
                                  "frontmatter": {"related_pages": ["a", "b", "c", "d"],
                                                   "epistemic_status": "factual"}},
        raw_chunks_for=lambda q: [],
        similar_pages=lambda txt: ["wiki/archibal/similar.md"],
        neo4j_traverse=lambda ents, h: [],
        llm_causal_chain=lambda body: {
            "text": "Series C BECAUSE compliance demand BECAUSE EU AI Act.",
            "levels": 3,
            "open_questions": ["why pivot away from media verification?"],
            "contradictions": 1,
            "recursive": False,
        },
        llm_implications=lambda body, chain: ["impl-1", "impl-2"],
        llm_analogy=lambda a, b: "layered-verification-pattern",
        write_wiki_update=lambda path, why, fm: written.append((path, why, fm)),
        write_neo4j_relation=lambda *a, **k: None,
    ), written


def test_understanding_pass_produces_depth_descriptor():
    from app.subia.understanding import UnderstandingPassRunner
    adapters, written = _stub_understanding_adapters()
    runner = UnderstandingPassRunner(adapters)
    result = runner.run_pass("wiki/archibal/truepic.md")
    assert result.depth.causal_levels == 3
    assert result.depth.cross_references == 4
    assert result.depth.implications_generated == 2
    assert result.depth.structural_analogies >= 1
    assert result.depth.deep_questions == 1
    assert written, "wiki update side-effect must fire"


def test_understanding_no_body_no_pass():
    from app.subia.understanding import UnderstandingPassRunner, UnderstandingAdapters
    adapters = UnderstandingAdapters(
        read_wiki_page=lambda p: {"body": "", "frontmatter": {}},
        raw_chunks_for=lambda q: [], similar_pages=lambda t: [],
        neo4j_traverse=lambda e, h: [], llm_causal_chain=lambda b: {},
        llm_implications=lambda b, c: [], llm_analogy=lambda a, b: None,
        write_wiki_update=lambda *a: None,
    )
    runner = UnderstandingPassRunner(adapters)
    res = runner.run_pass("wiki/empty.md")
    assert res.depth.causal_levels == 0
    assert res.tokens_spent == 0


# ─────────────────────────────────────────────────────────────────────
# Shadow Self (Proposal 3)
# ─────────────────────────────────────────────────────────────────────

def test_shadow_attentional_bias_detected():
    from app.subia.shadow.biases import detect_attentional_bias
    history = [{"archibal": 5, "plg": 1}, {"archibal": 4, "plg": 1}]
    findings = detect_attentional_bias(history)
    names = {f.name for f in findings}
    assert any("over_attention:archibal" in n for n in names)


def test_shadow_prediction_bias_detected():
    from app.subia.shadow.biases import detect_prediction_bias
    errs = [{"domain": "ci", "error": 0.2} for _ in range(6)]
    findings = detect_prediction_bias(errs)
    assert findings and "ci" in findings[0].name


def test_shadow_avoidance_detected():
    from app.subia.shadow.biases import detect_avoidance
    rq_log = [["social_alignment"]] * 4
    actions = [{"variable_addressed": "progress"}]
    findings = detect_avoidance(rq_log, actions)
    assert any("never_addressed:social_alignment" in f.name for f in findings)


def test_shadow_miner_assembles_report_and_calls_writers():
    from app.subia.shadow import ShadowMiner, ShadowAdapters
    appended: list = []
    discovered: list = []
    adapters = ShadowAdapters(
        fetch_scene_history=lambda d: [{"archibal": 5, "plg": 1}] * 2,
        fetch_prediction_errors=lambda d: [{"domain": "ci", "error": 0.2}] * 6,
        fetch_restoration_queue_log=lambda d: [["social_alignment"]] * 4,
        fetch_action_log=lambda d: [{"variable_addressed": "progress"}],
        fetch_affect_log=lambda d: [],
        fetch_normalize_by=lambda: {},
        append_to_shadow_wiki=lambda md: appended.append(md),
        add_to_self_state_discovered=lambda items: discovered.extend(items),
    )
    miner = ShadowMiner(adapters)
    rep = miner.run_analysis(days=30)
    assert rep.findings
    assert appended and "Shadow analysis" in appended[0]
    assert discovered


# ─────────────────────────────────────────────────────────────────────
# Cross-feed bridges
# ─────────────────────────────────────────────────────────────────────

def test_understanding_to_wonder_closes_loop():
    from app.subia.connections.six_proposals_bridges import understanding_to_wonder
    from app.subia.wonder.detector import UnderstandingDepth
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables["wonder"] = 0.3
    kernel.scene.append(SceneItem(
        id="x", source="wiki", content_ref="", summary="",
        salience=0.5, entered_at="t",
    ))
    sig = understanding_to_wonder(
        kernel,
        UnderstandingDepth(causal_levels=4, cross_references=8,
                            cross_domain_contradictions=3,
                            recursive_structure_detected=True,
                            epistemic_statuses=["creative", "factual"],
                            structural_analogies=3),
        triggering_topic="layered-verification",
        triggering_item_id="x",
    )
    assert sig.is_event
    assert kernel.homeostasis.variables["wonder"] > 0.3
    assert kernel.scene[0].wonder_intensity > 0.0


def test_wonder_to_reverie_queues_priority_topic():
    from app.subia.connections.six_proposals_bridges import (
        wonder_to_reverie, drain_reverie_priority_topics,
    )
    drain_reverie_priority_topics()  # clear
    wonder_to_reverie("layered-verification")
    wonder_to_reverie("layered-verification")  # dedup
    wonder_to_reverie("strange-loop")
    topics = drain_reverie_priority_topics()
    assert topics == ["layered-verification", "strange-loop"]


def test_shadow_findings_to_self_state_appends_and_drops_coherence():
    from app.subia.connections.six_proposals_bridges import shadow_findings_to_self_state
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables["self_coherence"] = 0.8
    findings = [
        {"name": "over_attention:archibal", "kind": "attentional",
         "detail": "x", "quantitative": {}},
        {"name": "systematic_bias:ci", "kind": "prediction",
         "detail": "y", "quantitative": {}},
    ]
    n = shadow_findings_to_self_state(kernel, findings)
    assert n == 2
    assert len(kernel.self_state.discovered_limitations) == 2
    # Re-running with same findings must not duplicate
    assert shadow_findings_to_self_state(kernel, findings) == 0
    assert kernel.homeostasis.variables["self_coherence"] < 0.8


def test_reverie_analogy_to_understanding_drains():
    from app.subia.connections.six_proposals_bridges import (
        reverie_analogy_to_understanding, drain_understanding_queue,
    )
    drain_understanding_queue(limit=999)  # clear
    class _FakeResult:
        cycle_id = "c1"
        resonances = [{"a": "X", "b": "Y", "note": "structural"}]
    n = reverie_analogy_to_understanding(_FakeResult())
    assert n == 1
    drained = drain_understanding_queue(limit=10)
    assert drained and drained[0]["concept_a"] == "X"
    assert drain_understanding_queue() == []


def test_boundary_route_for_kernel_returns_per_item_routes():
    from app.subia.connections.six_proposals_bridges import boundary_route_for_kernel
    kernel = SubjectivityKernel()
    item = SceneItem(id="m1", source="mem0", content_ref="", summary="",
                     salience=0.5, entered_at="t")
    item.processing_mode = "memorial"
    kernel.scene.append(item)
    routes = boundary_route_for_kernel(kernel)
    assert routes["m1"]["mem0_tier"] == "full"


# ─────────────────────────────────────────────────────────────────────
# Idle scheduler
# ─────────────────────────────────────────────────────────────────────

def test_idle_scheduler_throttle_and_priority():
    from app.subia.idle import IdleScheduler, IdleJob
    sched = IdleScheduler(total_token_budget=10_000)
    runs: list = []
    sched.register(IdleJob(
        name="reverie",
        fn=lambda: runs.append("reverie") or {"tokens_spent": 700},
        min_interval_seconds=60.0, priority=20, token_budget=2000,
    ))
    sched.register(IdleJob(
        name="understanding",
        fn=lambda: runs.append("understanding") or {"tokens_spent": 1600},
        min_interval_seconds=300.0, priority=30, token_budget=2000,
    ))
    rep = sched.tick(now=1000.0)
    assert "reverie" in rep and "understanding" in rep
    assert runs == ["reverie", "understanding"]
    # Re-tick immediately (within both intervals): no new runs
    rep2 = sched.tick(now=1010.0)
    assert rep2 == {}


def test_idle_scheduler_swallows_job_failures():
    from app.subia.idle import IdleScheduler, IdleJob
    sched = IdleScheduler()
    def boom():
        raise RuntimeError("intentional")
    sched.register(IdleJob(name="bad", fn=boom, min_interval_seconds=1.0))
    rep = sched.tick(now=10.0)
    assert rep["bad"]["ok"] is False
    assert sched.jobs()[0].failures == 1


# ─────────────────────────────────────────────────────────────────────
# CIL hot-path hooks
# ─────────────────────────────────────────────────────────────────────

def test_phase12_hooks_tag_and_apply_resonance():
    from app.subia.phase12_hooks import (
        tag_scene_processing_modes, apply_value_resonance_and_lenses,
    )
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables = {
        v: 0.5 for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
    }
    kernel.scene.append(SceneItem(
        id="i", source="firecrawl", content_ref="",
        summary="quality work that respects human dignity and truth",
        salience=0.4, entered_at="t",
    ))
    n = tag_scene_processing_modes(kernel)
    assert n == 1
    assert kernel.scene[0].processing_mode == "perceptual"
    out = apply_value_resonance_and_lenses(kernel)
    assert out["items_modulated"] == 1
    assert kernel.scene[0].salience > 0.4


# ─────────────────────────────────────────────────────────────────────
# New homeostatic variables present
# ─────────────────────────────────────────────────────────────────────

def test_new_homeostatic_variables_initialise():
    from app.subia.homeostasis.engine import ensure_variables
    kernel = SubjectivityKernel()
    ensure_variables(kernel)
    assert "wonder" in kernel.homeostasis.variables
    assert "self_coherence" in kernel.homeostasis.variables
    # Setpoint overrides honoured
    assert kernel.homeostasis.set_points["self_coherence"] == 0.75
    assert kernel.homeostasis.set_points["wonder"] == 0.4


def test_scene_item_phase12_fields_defaulted():
    item = SceneItem(id="x", source="wiki", content_ref="",
                     summary="", salience=0.5, entered_at="t")
    assert item.processing_mode is None
    assert item.wonder_intensity == 0.0


def test_self_state_discovered_limitations_defaulted():
    kernel = SubjectivityKernel()
    assert kernel.self_state.discovered_limitations == []
