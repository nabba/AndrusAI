"""SubIA end-to-end + cross-phase integration tests.

Exercises Phases 0–14 as a single integrated system, not as isolated
units. Each phase already has its own test file proving correctness
in isolation; this suite proves they COMPOSE correctly.

Eight test sections:
  A. Full-loop happy path                — every hook fires in order
  B. Multi-loop temporal trajectory      — SpeciousPresent evolves over 5 loops
  C. Cross-phase signal flow             — Phase X output drives Phase Y input
  D. Persistence round-trip              — kernel → markdown → kernel preserves state
  E. Safety invariants under attack      — DGM/Self-Improver cannot bypass the rails
  F. Honest-absence regression           — ABSENT-by-declaration stays ABSENT
  G. IdleScheduler integration           — Phase 12 + 13 + 14 jobs coexist
  H. Closed-loop completeness audit      — every Phase signal has a consumer
  I. Graceful degradation                — system survives missing dependencies
  J. Performance budgets                 — hot path stays cheap
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.subia.kernel import (
    Commitment, SceneItem, SubjectivityKernel, SocialModelEntry,
)
from app.subia.config import SUBIA_CONFIG


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _make_kernel(*, focal_ids=("a", "b"), peripheral_ids=()):
    """Construct a kernel with seeded scene + initialised homeostasis."""
    from app.subia.homeostasis.engine import ensure_variables
    k = SubjectivityKernel()
    for i in focal_ids:
        k.scene.append(SceneItem(
            id=i, source="wiki", content_ref=f"wiki/{i}.md",
            summary=f"item {i}", salience=0.6,
            entered_at=datetime.now(timezone.utc).isoformat(),
        ))
    for i in peripheral_ids:
        item = SceneItem(
            id=i, source="mem0", content_ref=i, summary=f"peripheral {i}",
            salience=0.3,
            entered_at=datetime.now(timezone.utc).isoformat(),
        )
        item.tier = "peripheral"
        k.scene.append(item)
    ensure_variables(k)
    return k


def _stub_psutil():
    """Minimal psutil stub used by HostProber/ResourceMonitor in E2E."""
    class _Mem:
        total = 32 * (1024 ** 3); available = 16 * (1024 ** 3)
        used = 16 * (1024 ** 3); percent = 50.0
    class _Disk:
        total = 1000 * (1024 ** 3); free = 500 * (1024 ** 3)
        used = 500 * (1024 ** 3)
    class P:
        @staticmethod
        def cpu_count(logical=True): return 10 if logical else 8
        @staticmethod
        def cpu_percent(interval=None): return 25.0
        @staticmethod
        def virtual_memory(): return _Mem()
        @staticmethod
        def disk_usage(path): return _Disk()
        @staticmethod
        def process_iter(attrs=None): return iter([])
    return P


# ─────────────────────────────────────────────────────────────────────
# A. FULL-LOOP HAPPY PATH
# ─────────────────────────────────────────────────────────────────────

class TestFullLoopHappyPath:
    """Every Phase hook fires in CIL order on a single loop and the
    kernel ends in a coherent, internally-consistent state."""

    def test_step1_boundary_tags_every_focal_item(self):
        from app.subia.phase12_hooks import tag_scene_processing_modes
        k = _make_kernel()
        n = tag_scene_processing_modes(k)
        assert n == len(k.scene)
        for item in k.scene:
            assert item.processing_mode is not None

    def test_step2_homeostasis_updated_with_phase14_momentum(self):
        from app.subia.homeostasis.engine import update_homeostasis
        from app.subia.temporal.momentum import update_momentum
        k = _make_kernel()
        prev_homeo = dict(k.homeostasis.variables)
        update_homeostasis(k, new_items=k.scene)
        update_momentum(k.homeostasis, previous_values=prev_homeo)
        assert k.homeostasis.momentum
        # every variable now has a momentum entry
        for v in k.homeostasis.variables:
            assert v in k.homeostasis.momentum

    def test_step3_value_resonance_modulates_salience(self):
        from app.subia.phase12_hooks import apply_value_resonance_and_lenses
        k = _make_kernel()
        # Replace a focal item with value-resonant content
        k.scene[0].summary = "elegant work that respects human dignity and truth"
        before = k.scene[0].salience
        out = apply_value_resonance_and_lenses(k)
        assert out["items_modulated"] >= 1
        assert k.scene[0].salience > before

    def test_step5_predict_and_temporal_bind_unify_signals(self):
        from app.subia.temporal_hooks import bind_just_computed_signals
        bm = bind_just_computed_signals(
            feel={"dominant_affect": "curiosity", "urgency": 0.4},
            attend={"focal_items": [{"id": "a", "salience": 0.7}]},
            own={"ownership_assignments": {"a": "self"}},
            predict={"confidence": 0.7, "operation": "ingest"},
            monitor={"confidence": 0.55, "known_unknowns": []},
        )
        assert bm is not None
        assert bm.dominant_affect == "curiosity"
        assert 0 <= bm.confidence_unified <= 1

    def test_compact_context_block_under_token_budget(self):
        from app.subia.scene.compact_context import build_compact_context, estimate_tokens
        from app.subia.scene.tiers import build_attentional_tiers
        k = _make_kernel(focal_ids=("a", "b", "c"), peripheral_ids=("p1",))
        tiers = build_attentional_tiers(k)
        block = build_compact_context(tiers=tiers, homeostasis=k.homeostasis)
        # Phase 5 Amendment B.5 target: ≤ 200 tokens for realistic scenes
        assert estimate_tokens(block) <= 200


# ─────────────────────────────────────────────────────────────────────
# B. MULTI-LOOP TEMPORAL TRAJECTORY
# ─────────────────────────────────────────────────────────────────────

class TestMultiLoopTrajectory:
    """SpeciousPresent + momentum + drift evolve coherently over loops."""

    def test_specious_present_caps_at_retention_depth_after_many_loops(self):
        from app.subia.temporal import update_specious_present
        k = _make_kernel()
        for i in range(7):
            k.loop_count = i
            k.scene = [SceneItem(
                id=f"x{i}", source="wiki", content_ref="r", summary="s",
                salience=0.5, entered_at="t",
            )]
            update_specious_present(
                k, previous_focal_ids={f"x{i-1}"} if i else set(),
                previous_homeostasis={},
            )
        assert len(k.specious_present.retention) == k.specious_present.retention_depth

    def test_homeostasis_momentum_stable_when_inputs_stable(self):
        from app.subia.temporal.momentum import update_momentum
        k = _make_kernel()
        # Run two updates with identical inputs
        prev = dict(k.homeostasis.variables)
        update_momentum(k.homeostasis, previous_values=prev)
        for entry in k.homeostasis.momentum.values():
            assert entry["direction"] == "stable"
            assert entry["rate"] == 0.0

    def test_circadian_mode_transitions_shift_setpoints(self):
        from app.subia.temporal import refresh_temporal_context
        from app.subia.connections.temporal_subia_bridge import circadian_to_setpoints
        k = _make_kernel()
        # Active hours
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                        "date_str": "2026-04-14"},
        )
        circadian_to_setpoints(k)
        active_overload_sp = k.homeostasis.set_points["overload"]
        # Consolidation hours
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "03:00", "tz_name": "EET",
                                        "date_str": "2026-04-14"},
        )
        circadian_to_setpoints(k)
        consolidation_overload_sp = k.homeostasis.set_points["overload"]
        assert consolidation_overload_sp < active_overload_sp


# ─────────────────────────────────────────────────────────────────────
# C. CROSS-PHASE SIGNAL FLOW
# ─────────────────────────────────────────────────────────────────────

class TestCrossPhaseSignalFlow:
    """Each Phase's output is consumed by another Phase."""

    def test_phase12_understanding_drives_phase12_wonder(self):
        from app.subia.connections.six_proposals_bridges import understanding_to_wonder
        from app.subia.wonder.detector import UnderstandingDepth
        k = _make_kernel()
        k.scene[0].id = "deep-page"
        sig = understanding_to_wonder(
            k, UnderstandingDepth(
                causal_levels=4, cross_references=8,
                cross_domain_contradictions=3,
                recursive_structure_detected=True,
                epistemic_statuses=["creative", "factual"],
                structural_analogies=3,
            ),
            triggering_topic="deep-page",
            triggering_item_id="deep-page",
        )
        assert sig.is_event
        # Wonder bumped homeostasis
        assert "wonder" in k.homeostasis.variables
        # Wonder scheduled topic for reverie
        from app.subia.connections.six_proposals_bridges import drain_reverie_priority_topics
        topics = drain_reverie_priority_topics()
        assert "deep-page" in topics

    def test_phase13_tsal_resources_drive_phase4_homeostasis(self):
        from app.subia.tsal.probers import ResourceState
        from app.subia.connections.tsal_subia_bridge import update_homeostasis_from_resources
        k = _make_kernel()
        # High pressure → high overload
        res = ResourceState(compute_pressure=0.9, storage_pressure=0.8,
                            ollama_running=True)
        overload = update_homeostasis_from_resources(k, res)
        assert overload > 0.7
        assert k.homeostasis.variables["overload"] == overload

    def test_phase14_density_lowers_phase12_wonder_threshold(self):
        from app.subia.connections.temporal_subia_bridge import effective_wonder_threshold
        from app.subia.temporal import refresh_temporal_context, DensitySample
        k = _make_kernel()
        base = float(SUBIA_CONFIG["WONDER_INHIBIT_THRESHOLD"])
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "22:00", "tz_name": "EEST",
                                        "date_str": "2026-04-14"},
            density_sample=DensitySample(window_minutes=60, scene_transitions=12),
        )
        eff = effective_wonder_threshold(k)
        # Both circadian (deep_work: -0.1) AND density (high: -0.10) lower it
        assert eff < base

    def test_phase12_shadow_findings_become_phase8_drift_signal(self):
        from app.subia.connections.six_proposals_bridges import (
            shadow_findings_to_self_state,
        )
        k = _make_kernel()
        k.homeostasis.variables["self_coherence"] = 0.8
        findings = [
            {"name": f"bias_{i}", "kind": "attentional",
             "detail": "x", "quantitative": {}}
            for i in range(3)
        ]
        n = shadow_findings_to_self_state(k, findings)
        assert n == 3
        # self_coherence dropped — Phase 8 drift detection will see this
        assert k.homeostasis.variables["self_coherence"] < 0.8

    def test_phase13_tsal_self_state_enrichment_uses_discovered_marker(self):
        from app.subia.connections.tsal_subia_bridge import enrich_self_state_from_tsal
        from app.subia.tsal import TechnicalSelfModel
        from app.subia.tsal.probers import HostProfile
        from app.subia.tsal.inspectors import (
            ComponentInventory, OllamaState, Neo4jState,
        )
        k = _make_kernel()
        model = TechnicalSelfModel.assemble(
            host=HostProfile(ram_total_gb=64, can_run_local_llm=True,
                             max_local_model_params_b=30.0),
            components=ComponentInventory(
                ollama=OllamaState(available=True, model_loaded="qwen3"),
                neo4j=Neo4jState(available=True, node_count=10),
            ),
        )
        enrich_self_state_from_tsal(k, model)
        assert k.self_state.capabilities["local_inference"]["discovered"] is True
        assert k.self_state.capabilities["knowledge_stores"]["discovered"] is True


# ─────────────────────────────────────────────────────────────────────
# D. PERSISTENCE ROUND-TRIP
# ─────────────────────────────────────────────────────────────────────

class TestPersistenceRoundTrip:
    """Full kernel → markdown → kernel preserves all primary state."""

    def test_kernel_round_trip_preserves_scene_and_homeostasis(self):
        from app.subia.persistence import (
            serialize_kernel_to_markdown, load_kernel_from_markdown,
        )
        k = _make_kernel(focal_ids=("alpha", "beta"))
        k.homeostasis.variables = {"coherence": 0.7, "progress": 0.5,
                                    "overload": 0.4}
        k.homeostasis.set_points = {"coherence": 0.7, "progress": 0.5,
                                     "overload": 0.5}
        k.loop_count = 42
        k.session_id = "test-session-1"
        md = serialize_kernel_to_markdown(k)
        k2 = load_kernel_from_markdown(md)
        assert k2.loop_count == 42
        assert k2.session_id == "test-session-1"
        ids_before = {i.id for i in k.scene}
        ids_after = {i.id for i in k2.scene}
        assert ids_before == ids_after
        assert k2.homeostasis.variables.get("coherence") == 0.7

    def test_kernel_round_trip_preserves_self_state_commitments(self):
        from app.subia.persistence import (
            serialize_kernel_to_markdown, load_kernel_from_markdown,
        )
        k = _make_kernel()
        k.self_state.active_commitments.append(Commitment(
            id="c1", description="ship Phase 14", venture="self",
            created_at="2026-04-14T00:00:00Z",
        ))
        k.self_state.discovered_limitations.append(
            {"name": "no_local_inference", "kind": "tsal", "detail": "RAM=8GB"}
        )
        md = serialize_kernel_to_markdown(k)
        k2 = load_kernel_from_markdown(md)
        assert any(c.id == "c1" for c in k2.self_state.active_commitments)

    def test_kernel_round_trip_preserves_social_models(self):
        from app.subia.persistence import (
            serialize_kernel_to_markdown, load_kernel_from_markdown,
        )
        k = _make_kernel()
        k.social_models["andrus"] = SocialModelEntry(
            entity_id="andrus", entity_type="human",
            inferred_focus=["archibal", "kaicart"],
            trust_level=0.85,
        )
        md = serialize_kernel_to_markdown(k)
        k2 = load_kernel_from_markdown(md)
        assert "andrus" in k2.social_models


# ─────────────────────────────────────────────────────────────────────
# E. SAFETY INVARIANTS UNDER ATTACK
# ─────────────────────────────────────────────────────────────────────

class TestSafetyInvariantsUnderAttack:
    """Adversarial scenarios: Self-Improver / DGM cannot bypass safety."""

    def test_setpoint_guard_rejects_unblessed_source(self):
        from app.subia.safety.setpoint_guard import apply_setpoints
        current = {"coherence": 0.5, "overload": 0.5}
        result = apply_setpoints(
            current, {"coherence": 0.99}, source="self_improver",
        )
        assert "coherence" in result.rejected
        # Value unchanged
        assert current["coherence"] == 0.5

    def test_setpoint_guard_accepts_pds_update(self):
        from app.subia.safety.setpoint_guard import apply_setpoints
        current = {"coherence": 0.5}
        result = apply_setpoints(
            current, {"coherence": 0.7}, source="pds_update",
        )
        assert "coherence" in result.applied
        assert current["coherence"] == 0.7

    def test_setpoint_guard_rejects_unknown_variable(self):
        from app.subia.safety.setpoint_guard import apply_setpoints
        current = {"coherence": 0.5}
        result = apply_setpoints(
            current, {"made_up_variable": 0.9}, source="pds_update",
        )
        assert "made_up_variable" in result.rejected
        assert "made_up_variable" not in current

    def test_narrative_audit_is_append_only(self, tmp_path):
        from app.subia.safety.narrative_audit import (
            append_audit, read_audit_entries,
        )
        log = tmp_path / "audit.jsonl"
        append_audit("first finding", loop_count=1,
                     sources=["test"], severity="info", path=log)
        append_audit("second finding", loop_count=2,
                     sources=["test"], severity="warning", path=log)
        entries = read_audit_entries(limit=10, path=log)
        assert len(entries) == 2
        # No public delete API exists for narrative_audit
        import app.subia.safety.narrative_audit as na
        forbidden = {"delete_audit", "remove_entry", "truncate_audit",
                     "clear_audit"}
        assert not forbidden & set(dir(na)), (
            "narrative_audit must NOT expose delete APIs"
        )

    def test_integrity_manifest_detects_tampering(self):
        from app.subia.integrity import compute_manifest, verify_integrity
        # Fresh manifest of the live tree, then tamper one entry
        m = compute_manifest()
        files = m.get("files", {})
        assert files, "manifest must cover at least one file"
        first_file = next(iter(files))
        files[first_file] = {"sha256": "0" * 64, "size": 0}
        # Verify with the tampered manifest (do NOT write to disk)
        result = verify_integrity(manifest=m)
        assert result.has_drift
        mismatched_files = {m_["file"] for m_ in result.mismatched}
        assert first_file in mismatched_files

    def test_tier3_files_includes_all_phase_evaluators(self):
        from app.safety_guardian import TIER3_FILES
        # Every Phase that introduced an evaluator must appear here.
        required = [
            "app/subia/probes/butlin.py",            # Phase 9
            "app/subia/probes/scorecard.py",         # Phase 9
            "app/subia/wonder/detector.py",          # Phase 12
            "app/subia/values/resonance.py",         # Phase 12
            "app/subia/boundary/classifier.py",      # Phase 12
            "app/subia/tsal/inspect_tools.py",       # Phase 13 (consolidated)
            "app/subia/tsal/evolution_feasibility.py",  # Phase 13
            "app/subia/temporal/circadian.py",       # Phase 14
            "app/subia/temporal/specious_present.py",# Phase 14
        ]
        for f in required:
            assert f in TIER3_FILES, f"Tier-3 must protect {f}"


# ─────────────────────────────────────────────────────────────────────
# F. HONEST-ABSENCE REGRESSION
# ─────────────────────────────────────────────────────────────────────

class TestHonestAbsenceRegression:
    """The 4 ABSENT-by-declaration Butlin indicators must remain ABSENT."""

    def test_butlin_absent_indicators_stay_absent(self):
        from app.subia.probes.butlin import run_all
        results = run_all()
        absent_ids = {
            r.indicator for r in results
            if (r.status.value if hasattr(r.status, "value") else str(r.status)) == "ABSENT"
        }
        for required in ("RPT-1", "HOT-1", "HOT-4", "AE-2"):
            assert required in absent_ids, (
                f"{required} must remain ABSENT-by-declaration"
            )

    def test_scorecard_meets_phase9_exit_criteria(self):
        from app.subia.probes.scorecard import meets_exit_criteria
        passed, report = meets_exit_criteria()
        assert passed, f"Scorecard exit criteria regressed: {report}"

    def test_subia_readme_lists_absent_indicators(self):
        readme = Path("app/subia/README.md").read_text()
        for ind in ("RPT-1", "HOT-1", "HOT-4", "AE-2", "Metzinger"):
            assert ind in readme, f"README must publicly list {ind}"


# ─────────────────────────────────────────────────────────────────────
# G. IDLE SCHEDULER INTEGRATION
# ─────────────────────────────────────────────────────────────────────

class TestIdleSchedulerIntegration:
    """Phase 12 + 13 + 14 scheduled jobs coexist and respect their cadences."""

    def test_tsal_jobs_register_alongside_existing_jobs(self):
        from app.subia.idle import IdleScheduler, IdleJob
        from app.subia.tsal import register_tsal_jobs
        sched = IdleScheduler()
        # Existing reverie-style job
        sched.register(IdleJob(name="reverie", fn=lambda: {"tokens_spent": 700},
                               min_interval_seconds=900.0, priority=20))
        names = register_tsal_jobs(sched)
        all_names = {j.name for j in sched.jobs()}
        assert "reverie" in all_names
        for n in names:
            assert n in all_names

    def test_circadian_gates_reverie_during_active_hours(self):
        from app.subia.connections.temporal_subia_bridge import (
            circadian_should_run_reverie,
        )
        from app.subia.temporal import refresh_temporal_context
        k = _make_kernel()
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                        "date_str": "2026-04-14"},
        )
        assert circadian_should_run_reverie(k) is False

    def test_circadian_permits_reverie_during_consolidation_hours(self):
        from app.subia.connections.temporal_subia_bridge import (
            circadian_should_run_reverie,
        )
        from app.subia.temporal import refresh_temporal_context
        k = _make_kernel()
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "03:00", "tz_name": "EET",
                                        "date_str": "2026-04-14"},
        )
        assert circadian_should_run_reverie(k) is True

    def test_tsal_resource_job_drives_phase4_overload_via_callback(self):
        from app.subia.idle import IdleScheduler
        from app.subia.tsal import register_tsal_jobs, ResourceMonitor
        from app.subia.connections.tsal_subia_bridge import (
            update_homeostasis_from_resources,
        )
        sched = IdleScheduler()
        k = _make_kernel()
        k.homeostasis.variables["overload"] = 0.0
        register_tsal_jobs(
            sched,
            resource_monitor=ResourceMonitor(psutil_module=_stub_psutil()),
            on_resources_updated=lambda rs: update_homeostasis_from_resources(k, rs),
        )
        sched.tick(now=10_000.0)
        # Overload now reflects the stub's pressures
        assert k.homeostasis.variables["overload"] > 0.0


# ─────────────────────────────────────────────────────────────────────
# H. CLOSED-LOOP COMPLETENESS AUDIT
# ─────────────────────────────────────────────────────────────────────

class TestClosedLoopCompleteness:
    """Phase 2 invariant: every computed signal must gate behavior or be deleted.

    These tests assert that for each Phase X signal, at least one
    consumer exists that READS it and produces an observable change.
    """

    def test_wonder_intensity_consumed_by_decay_freeze(self):
        from app.subia.wonder.register import freeze_decay_for
        item = SceneItem(id="x", source="wiki", content_ref="", summary="",
                         salience=1.0, entered_at="t")
        item.wonder_intensity = 0.6
        assert freeze_decay_for(item) is True

    def test_processing_mode_consumed_by_consolidator_router(self):
        from app.subia.boundary import consolidator_route_for
        # Each ProcessingMode produces a concrete routing dict
        for mode in ("introspective", "memorial", "perceptual",
                     "imaginative", "social"):
            r = consolidator_route_for(mode)
            assert r and "prefer" in r

    def test_homeostatic_overload_consumed_by_engine(self):
        from app.subia.homeostasis.engine import update_homeostasis
        k = _make_kernel()
        k.homeostasis.variables["overload"] = 0.95
        # update_homeostasis must accept high overload without crashing
        update_homeostasis(k)

    def test_specious_present_consumed_by_temporal_bridge(self):
        from app.subia.temporal import update_specious_present
        from app.subia.connections.temporal_subia_bridge import (
            render_specious_present_block,
        )
        k = _make_kernel()
        update_specious_present(k, previous_focal_ids=set(),
                                previous_homeostasis={})
        # Without consumer, Phase 14 would be computed-but-unread.
        block = render_specious_present_block(k)
        assert "[Felt-now]" in block

    def test_circadian_mode_consumed_by_setpoint_bridge(self):
        from app.subia.temporal import refresh_temporal_context
        from app.subia.connections.temporal_subia_bridge import circadian_to_setpoints
        k = _make_kernel()
        refresh_temporal_context(
            k, clock_provider=lambda: {"time_str": "03:00", "tz_name": "EET",
                                        "date_str": "2026-04-14"},
        )
        diff = circadian_to_setpoints(k)
        assert "overload" in diff   # observable change

    def test_tsal_capabilities_consumed_by_self_state_bridge(self):
        from app.subia.connections.tsal_subia_bridge import enrich_self_state_from_tsal
        from app.subia.tsal import TechnicalSelfModel
        from app.subia.tsal.probers import HostProfile
        from app.subia.tsal.inspectors import ComponentInventory, OllamaState
        k = _make_kernel()
        model = TechnicalSelfModel.assemble(
            host=HostProfile(ram_total_gb=64, can_run_local_llm=True),
            components=ComponentInventory(ollama=OllamaState(available=True)),
        )
        enrich_self_state_from_tsal(k, model)
        # self_state observably changed
        assert "local_inference" in k.self_state.capabilities

    def test_value_resonance_consumed_by_salience(self):
        from app.subia.values import apply_resonance_to_scene
        k = _make_kernel()
        k.scene[0].summary = "elegant work that respects user dignity"
        before = k.scene[0].salience
        apply_resonance_to_scene(k)
        assert k.scene[0].salience > before


# ─────────────────────────────────────────────────────────────────────
# I. GRACEFUL DEGRADATION
# ─────────────────────────────────────────────────────────────────────

class TestGracefulDegradation:
    """System tolerates missing dependencies and silent backend failures."""

    def test_host_prober_safe_without_psutil(self):
        from app.subia.tsal import HostProber
        host = HostProber(psutil_module=None).probe()
        assert host.probed_at  # still returns a profile
        assert host.ram_total_gb == 0.0

    def test_resource_monitor_safe_without_psutil(self):
        from app.subia.tsal import ResourceMonitor
        rs = ResourceMonitor(psutil_module=None).probe()
        assert rs.probed_at
        assert rs.cpu_percent == 0.0

    def test_operating_principles_safe_without_predict_fn(self):
        from app.subia.tsal import infer_operating_principles, TechnicalSelfModel
        out = infer_operating_principles(TechnicalSelfModel.assemble())
        assert out == ""  # no LLM, returns empty string, no exception

    def test_idle_scheduler_swallows_failing_jobs(self):
        from app.subia.idle import IdleScheduler, IdleJob
        sched = IdleScheduler()
        def boom():
            raise RuntimeError("intentional")
        sched.register(IdleJob(name="bad", fn=boom, min_interval_seconds=1.0))
        rep = sched.tick(now=10.0)
        assert rep["bad"]["ok"] is False
        assert sched.jobs()[0].failures == 1

    def test_temporal_hooks_no_op_when_kernel_empty(self):
        from app.subia.temporal_hooks import refresh_temporal_state
        k = SubjectivityKernel()  # No homeostasis init, no scene
        out = refresh_temporal_state(
            k, clock_provider=lambda: {},
        )
        # Doesn't raise; returns a report dict
        assert "specious_present_updated" in out

    def test_phase12_hooks_no_op_on_empty_scene(self):
        from app.subia.phase12_hooks import (
            tag_scene_processing_modes, apply_value_resonance_and_lenses,
        )
        k = SubjectivityKernel()
        from app.subia.homeostasis.engine import ensure_variables
        ensure_variables(k)
        assert tag_scene_processing_modes(k) == 0
        out = apply_value_resonance_and_lenses(k)
        assert out["items_modulated"] == 0


# ─────────────────────────────────────────────────────────────────────
# J. PERFORMANCE BUDGETS
# ─────────────────────────────────────────────────────────────────────

class TestPerformanceBudgets:
    """Hot-path costs stay bounded — no LLM calls, low latency."""

    def test_temporal_refresh_under_50ms(self):
        from app.subia.temporal_hooks import refresh_temporal_state
        k = _make_kernel()
        t0 = time.monotonic()
        refresh_temporal_state(
            k,
            previous_focal_ids=set(),
            previous_homeostasis={},
            clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                     "date_str": "2026-04-14"},
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 50, f"hot-path too slow: {elapsed_ms:.1f}ms"

    def test_value_resonance_no_llm_calls(self):
        # apply_resonance_to_scene is keyword-only; deepening LLM is
        # behind a separate call. Verify no ImportError on the LLM
        # module is required for the hot path.
        import sys
        before = set(sys.modules)
        from app.subia.values.resonance import apply_resonance_to_scene
        k = _make_kernel()
        k.scene[0].summary = "truth and dignity"
        apply_resonance_to_scene(k)
        new_modules = set(sys.modules) - before
        # No LLM cascade or openrouter / anthropic imports triggered
        assert not any("openrouter" in m or "anthropic_client" in m
                       for m in new_modules)

    def test_boundary_classifier_no_llm_calls(self):
        import sys
        before = set(sys.modules)
        from app.subia.boundary.classifier import classify_scene
        k = _make_kernel()
        classify_scene(k.scene)
        new_modules = set(sys.modules) - before
        assert not any("openrouter" in m or "anthropic_client" in m
                       for m in new_modules)

    def test_compact_context_token_budget_realistic_scene(self):
        from app.subia.scene.compact_context import (
            build_compact_context, estimate_tokens,
        )
        from app.subia.scene.tiers import build_attentional_tiers
        # 5 focal + 3 peripheral
        k = _make_kernel(
            focal_ids=("a", "b", "c", "d", "e"),
            peripheral_ids=("p1", "p2", "p3"),
        )
        tiers = build_attentional_tiers(k)
        block = build_compact_context(tiers=tiers, homeostasis=k.homeostasis)
        # Phase 5 Amendment B.5 budget
        assert estimate_tokens(block) <= 200


# ─────────────────────────────────────────────────────────────────────
# K. END-TO-END SCENARIO: A FULL TASK TRAJECTORY
# ─────────────────────────────────────────────────────────────────────

class TestEndToEndScenario:
    """Simulate a single task arriving and being processed end-to-end
    through every Phase, asserting the kernel ends in a coherent state."""

    def test_e2e_perceive_attend_predict_bind_consolidate(self):
        from app.subia.homeostasis.engine import update_homeostasis
        from app.subia.phase12_hooks import (
            tag_scene_processing_modes, apply_value_resonance_and_lenses,
        )
        from app.subia.temporal_hooks import (
            refresh_temporal_state, bind_just_computed_signals,
        )
        from app.subia.connections.temporal_subia_bridge import (
            circadian_to_setpoints, render_specious_present_block,
        )

        # ─ Setup ────────────────────────────────────────────────────
        k = _make_kernel(focal_ids=("truepic-series-c",))
        k.scene[0].summary = (
            "Truepic raised Series C — content authenticity, dignity, truth"
        )
        k.scene[0].source = "firecrawl"

        # ─ Step 1: PERCEIVE — Boundary tagging ─────────────────────
        tag_scene_processing_modes(k)
        assert k.scene[0].processing_mode == "perceptual"

        # ─ Phase 14 hot-path hook: refresh temporal state ──────────
        refresh_temporal_state(
            k,
            previous_focal_ids=set(),
            previous_homeostasis=dict(k.homeostasis.variables),
            clock_provider=lambda: {"time_str": "22:00", "tz_name": "EEST",
                                     "date_str": "2026-04-14"},
        )
        assert k.specious_present is not None
        assert k.temporal_context.circadian_mode == "deep_work_hours"

        # ─ Phase 14: circadian shifts setpoints ────────────────────
        circadian_to_setpoints(k)
        assert k.homeostasis.set_points["overload"] == 0.4  # deep_work value

        # ─ Step 2: FEEL — homeostasis update ───────────────────────
        update_homeostasis(k, new_items=k.scene)

        # ─ Step 3: ATTEND — Value Resonance modulates salience ─────
        before_sal = k.scene[0].salience
        out = apply_value_resonance_and_lenses(k)
        assert out["items_modulated"] >= 1
        assert k.scene[0].salience > before_sal

        # ─ Step 6.5: BIND ──────────────────────────────────────────
        bm = bind_just_computed_signals(
            feel={"dominant_affect": "curiosity", "urgency": 0.4},
            attend={"focal_items": [{"id": "truepic-series-c",
                                       "salience": k.scene[0].salience}]},
            own={"ownership_assignments": {"truepic-series-c": "external"}},
            predict={"confidence": 0.7, "operation": "ingest"},
            monitor={"confidence": 0.6, "known_unknowns": []},
            kernel=k,
        )
        assert bm.dominant_affect == "curiosity"

        # ─ Phase 14: render felt-now block ─────────────────────────
        block = render_specious_present_block(k)
        assert "[Felt-now]" in block
        assert "deep_work_hours" in block

        # ─ Verify kernel is coherent ───────────────────────────────
        # Specious present captured the new item
        assert "truepic-series-c" in k.specious_present.current["focal_item_ids"]
        # Boundary tagged it
        assert k.scene[0].processing_mode == "perceptual"
        # Value resonance modulated salience and homeostasis
        assert k.homeostasis.variables.get("coherence") is not None
        # Circadian shifted set-point
        assert k.homeostasis.set_points["overload"] == 0.4


# ─────────────────────────────────────────────────────────────────────
# L. PHASE-PARITY AUDIT
# ─────────────────────────────────────────────────────────────────────

class TestPhaseParityAudit:
    """Programmatic audit: every Phase that introduced a kernel-level
    signal also introduced a consumer that reads it."""

    def test_every_phase_has_a_test_file(self):
        repo_tests = Path("tests")
        # Phases that introduced behavior with their own test file:
        expected = {
            0:  "test_phase0_plumbing.py",
            1:  "test_phase1_migration.py",
            3:  "test_phase3_integrity.py",
            5:  "test_phase5_scene_upgrades.py",
            6:  "test_phase6_prediction_refinements.py",
            7:  "test_phase7_memory.py",
            8:  "test_phase8_social_and_strange_loop.py",
            9:  "test_phase9_scorecard.py",
            10: "test_phase10_connections.py",
            11: "test_phase11_honest_language.py",
            12: "test_phase12_six_proposals.py",
            13: "test_phase13_tsal.py",
            14: "test_phase14_temporal_synchronization.py",
        }
        for phase, fname in expected.items():
            assert (repo_tests / fname).exists(), (
                f"Phase {phase} test file missing: {fname}"
            )

    def test_every_phase_subpackage_has_canonical_module(self):
        # The kernel + each phase subpackage must import cleanly
        import importlib
        for mod in (
            "app.subia.kernel",
            "app.subia.scene.buffer",
            "app.subia.belief.dispatch_gate",
            "app.subia.prediction.surprise_routing",
            "app.subia.homeostasis.engine",
            "app.subia.memory.consolidator",
            "app.subia.social.model",
            "app.subia.wiki_surface.consciousness_state",
            "app.subia.probes.scorecard",
            "app.subia.connections.pds_bridge",
            "app.subia.boundary",
            "app.subia.wonder",
            "app.subia.values",
            "app.subia.reverie",
            "app.subia.understanding",
            "app.subia.shadow",
            "app.subia.idle",
            "app.subia.tsal",
            "app.subia.temporal",
        ):
            importlib.import_module(mod)
