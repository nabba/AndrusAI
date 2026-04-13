"""Phase 14 — Temporal Synchronization tests.

Specious Present, Homeostatic Momentum, Circadian Modes, Processing
Density, Temporal Binding, Rhythm Discovery, plus the SubIA bridges
and the two narrow CIL hot-path hooks.

Closed-loop discipline (Phase 2 invariant) is asserted: every computed
temporal signal must produce an observable behavioural change.
"""
from __future__ import annotations

import pytest

from app.subia.kernel import SceneItem, SubjectivityKernel
from app.subia.config import SUBIA_CONFIG


# ─────────────────────────────────────────────────────────────────────
# Kernel data model extensions
# ─────────────────────────────────────────────────────────────────────

def test_kernel_has_phase14_attributes_default_none():
    k = SubjectivityKernel()
    assert k.specious_present is None
    assert k.temporal_context is None


def test_homeostatic_state_has_momentum_dict():
    k = SubjectivityKernel()
    assert k.homeostasis.momentum == {}


# ─────────────────────────────────────────────────────────────────────
# SpeciousPresent — retention, primal, protention, tempo, direction
# ─────────────────────────────────────────────────────────────────────

def _scene_with(*ids):
    return [SceneItem(id=i, source="wiki", content_ref=i, summary=i,
                      salience=0.5, entered_at="t") for i in ids]


def test_specious_present_records_retention_and_primal():
    from app.subia.temporal import update_specious_present
    k = SubjectivityKernel()
    k.scene = _scene_with("a", "b")
    k.homeostasis.variables = {"coherence": 0.5, "progress": 0.5}
    sp = update_specious_present(
        k, previous_focal_ids={"a"},
        previous_homeostasis={"coherence": 0.4, "progress": 0.5},
    )
    assert sp.current["focal_item_ids"] == ["a", "b"]
    assert sp.retention[-1].scene_delta["entered"] == ["b"]
    assert sp.retention[-1].homeostatic_delta["coherence"] == round(0.1, 4)


def test_specious_present_window_caps_at_retention_depth():
    from app.subia.temporal import update_specious_present
    k = SubjectivityKernel()
    k.specious_present = None
    for i in range(8):
        k.loop_count = i
        k.scene = _scene_with(f"item{i}")
        update_specious_present(k, previous_focal_ids=set(),
                                previous_homeostasis={})
    assert len(k.specious_present.retention) == k.specious_present.retention_depth


def test_specious_present_tempo_rises_with_turnover():
    from app.subia.temporal import update_specious_present
    k = SubjectivityKernel()
    # Many entries / exits → high tempo
    for i in range(3):
        k.loop_count = i
        k.scene = _scene_with(f"a{i}", f"b{i}", f"c{i}", f"d{i}")
        update_specious_present(
            k,
            previous_focal_ids={f"a{i-1}", f"b{i-1}", f"c{i-1}"} if i > 0 else set(),
            previous_homeostasis={},
        )
    high = k.specious_present.tempo

    k2 = SubjectivityKernel()
    for i in range(3):
        k2.loop_count = i
        k2.scene = _scene_with("stable")
        update_specious_present(
            k2,
            previous_focal_ids={"stable"} if i > 0 else set(),
            previous_homeostasis={},
        )
    low = k2.specious_present.tempo
    assert high > low


def test_specious_present_direction_classification():
    from app.subia.temporal.specious_present import _derive_direction
    assert _derive_direction({"coherence": 0.3, "progress": 0.2}) == "trending_positive"
    assert _derive_direction({"contradiction_pressure": 0.3}) == "trending_negative"
    assert _derive_direction({"coherence": 0.0, "progress": 0.01}) == "stable"
    # Turbulent: large abs total, small net
    assert _derive_direction({"coherence": 0.3, "progress": -0.3}) == "turbulent"


def test_specious_present_lingering_and_stable_items():
    from app.subia.temporal import update_specious_present
    k = SubjectivityKernel()
    # Frame 1: a, b enter
    k.scene = _scene_with("a", "b")
    update_specious_present(k, previous_focal_ids=set(), previous_homeostasis={})
    # Frame 2: only a (b lingers)
    k.scene = _scene_with("a")
    update_specious_present(k, previous_focal_ids={"a", "b"}, previous_homeostasis={})
    # Frame 3: only c (a lingers, b lingers)
    k.scene = _scene_with("c")
    update_specious_present(k, previous_focal_ids={"a"}, previous_homeostasis={})
    # `c` is current; `a, b` are in retention but not current → lingering.
    assert "a" in k.specious_present.lingering_items()


# ─────────────────────────────────────────────────────────────────────
# Homeostatic momentum
# ─────────────────────────────────────────────────────────────────────

def test_momentum_classifies_rising_falling_stable():
    from app.subia.temporal.momentum import update_momentum
    k = SubjectivityKernel()
    k.homeostasis.variables = {"coherence": 0.6, "progress": 0.3, "novelty_balance": 0.5}
    update_momentum(k.homeostasis, previous_values={
        "coherence": 0.4, "progress": 0.4, "novelty_balance": 0.5,
    })
    m = k.homeostasis.momentum
    assert m["coherence"]["direction"] == "rising"
    assert m["progress"]["direction"] == "falling"
    assert m["novelty_balance"]["direction"] == "stable"
    assert m["coherence"]["rate"] == round(0.2, 4)


def test_momentum_render_arrows_format():
    from app.subia.temporal.momentum import update_momentum, render_momentum_arrows
    k = SubjectivityKernel()
    k.homeostasis.variables = {"coherence": 0.6, "progress": 0.3}
    update_momentum(k.homeostasis,
                    previous_values={"coherence": 0.4, "progress": 0.4})
    s = render_momentum_arrows(k.homeostasis)
    assert "↑" in s
    assert "↓" in s


# ─────────────────────────────────────────────────────────────────────
# Circadian modes
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,expected", [
    (3, "consolidation_hours"),
    (7, "dawn_transition"),
    (10, "active_hours"),
    (15, "active_hours"),
    (21, "deep_work_hours"),
    (23, "deep_work_hours"),
])
def test_current_circadian_mode_at_hour(hour, expected):
    from app.subia.temporal import current_circadian_mode
    assert current_circadian_mode(hour) == expected


def test_apply_circadian_setpoints_modifies_overload_setpoint():
    from app.subia.temporal import apply_circadian_setpoints
    k = SubjectivityKernel()
    k.homeostasis.set_points["overload"] = 0.5
    diff = apply_circadian_setpoints(k.homeostasis, "consolidation_hours")
    assert "overload" in diff
    assert k.homeostasis.set_points["overload"] == 0.3


def test_circadian_consolidation_has_special_processes():
    from app.subia.temporal.circadian import (
        circadian_special_processes, circadian_allows_reverie,
    )
    procs = circadian_special_processes("consolidation_hours")
    assert "wiki_lint" in procs
    assert "understanding_passes" in procs
    assert circadian_allows_reverie("consolidation_hours") is True
    assert circadian_allows_reverie("active_hours") is False


# ─────────────────────────────────────────────────────────────────────
# Processing density (felt time)
# ─────────────────────────────────────────────────────────────────────

def test_density_zero_for_quiet_window():
    from app.subia.temporal import compute_processing_density, DensitySample
    s = DensitySample(window_minutes=60.0)
    assert compute_processing_density(s) == 0.0


def test_density_high_for_busy_window():
    from app.subia.temporal import compute_processing_density, DensitySample
    s = DensitySample(window_minutes=60.0,
                      scene_transitions=8, prediction_errors=2,
                      wonder_events=1, homeostatic_shifts=4)
    d = compute_processing_density(s)
    assert d >= 0.7


def test_density_monotonic_in_events():
    from app.subia.temporal import compute_processing_density, DensitySample
    a = compute_processing_density(DensitySample(window_minutes=60, scene_transitions=2))
    b = compute_processing_density(DensitySample(window_minutes=60, scene_transitions=8))
    assert b > a


def test_density_to_wonder_threshold_delta_lowers_with_density():
    from app.subia.temporal.density import density_to_wonder_threshold_delta
    assert density_to_wonder_threshold_delta(1.0) < density_to_wonder_threshold_delta(0.0)


# ─────────────────────────────────────────────────────────────────────
# Temporal binding
# ─────────────────────────────────────────────────────────────────────

def test_binding_unifies_confidence():
    from app.subia.temporal import temporal_bind
    bm = temporal_bind(
        feel={"urgency": 0.7, "dominant_affect": "concern"},
        attend={"focal_items": [{"id": "a", "salience": 0.7}]},
        own={"ownership_assignments": {"a": "self"}},
        predict={"confidence": 0.8},
        monitor={"confidence": 0.3},
    )
    assert 0.0 <= bm.confidence_unified <= 1.0
    # Conflict was logged (urgency + low monitor + high predict)
    assert any("predicted resolution" in c for c in bm.conflicts)


def test_binding_stability_bias_promotes_persistent_items():
    from app.subia.temporal import temporal_bind
    from app.subia.temporal.specious_present import KernelMoment
    retention = [
        KernelMoment(loop_count=i, timestamp="t",
                     scene_delta={"entered": ["a"]})
        for i in range(3)
    ]
    bm = temporal_bind(
        attend={"focal_items": [
            {"id": "a", "salience": 0.5},
            {"id": "b", "salience": 0.55},  # higher raw salience
        ]},
        retention=retention,
    )
    # Stability bias: 'a' should beat 'b' despite lower raw salience
    assert bm.salient_focus[0]["id"] == "a"


def test_binding_surfaces_mixed_ownership_conflict():
    from app.subia.temporal import temporal_bind
    bm = temporal_bind(
        own={"ownership_assignments": {"a": "self", "b": "external"}},
    )
    assert any("mixed ownership" in c for c in bm.conflicts)


# ─────────────────────────────────────────────────────────────────────
# Rhythm discovery
# ─────────────────────────────────────────────────────────────────────

def test_rhythm_discovery_finds_andrus_pattern():
    from app.subia.temporal import discover_rhythms
    log = [{"timestamp": f"2026-04-{day:02d}T09:30:00+00:00"} for day in range(1, 11)]
    rhythms = discover_rhythms(interaction_log=log)
    assert any(r.kind == "andrus" and 9 in r.typical_hours for r in rhythms)


def test_rhythm_discovery_handles_empty_logs():
    from app.subia.temporal import discover_rhythms
    assert discover_rhythms() == []


def test_rhythm_discovery_segments_firecrawl_by_source():
    from app.subia.temporal import discover_rhythms
    log = [
        {"timestamp": f"2026-04-{d:02d}T14:00:00+00:00", "source": "techcrunch"}
        for d in range(1, 8)
    ] + [
        {"timestamp": f"2026-04-{d:02d}T03:00:00+00:00", "source": "internal"}
        for d in range(1, 8)
    ]
    rhythms = discover_rhythms(firecrawl_log=log, min_samples=5)
    sources = {r.name for r in rhythms if r.kind == "firecrawl"}
    assert any("techcrunch" in s for s in sources)
    assert any("internal" in s for s in sources)


# ─────────────────────────────────────────────────────────────────────
# Temporal context aggregate
# ─────────────────────────────────────────────────────────────────────

def test_refresh_temporal_context_uses_clock_provider():
    from app.subia.temporal import refresh_temporal_context, DensitySample
    k = SubjectivityKernel()
    tc = refresh_temporal_context(
        k,
        clock_provider=lambda: {
            "date_str": "2026-04-14", "time_str": "22:30",
            "weekday": "Tuesday", "season": "spring",
            "tz_name": "EEST",
        },
        density_sample=DensitySample(window_minutes=60,
                                     scene_transitions=10),
    )
    assert tc.local_hour == 22
    assert tc.circadian_mode == "deep_work_hours"
    assert tc.reverie_allowed is True
    assert tc.cascade_preference == "depth"
    assert 0.0 < tc.processing_density <= 1.0


# ─────────────────────────────────────────────────────────────────────
# Hot-path hook integration
# ─────────────────────────────────────────────────────────────────────

def test_refresh_temporal_state_populates_three_subsystems():
    from app.subia.temporal_hooks import refresh_temporal_state
    k = SubjectivityKernel()
    k.scene = _scene_with("a")
    k.homeostasis.variables = {"coherence": 0.6, "progress": 0.4}
    k.homeostasis.set_points = {"coherence": 0.7, "progress": 0.5}
    out = refresh_temporal_state(
        k,
        previous_focal_ids=set(),
        previous_homeostasis={"coherence": 0.4, "progress": 0.5},
        clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                 "date_str": "2026-04-14"},
    )
    assert out["specious_present_updated"]
    assert out["temporal_context_updated"]
    assert out["momentum_updated"]
    assert k.specious_present is not None
    assert k.temporal_context is not None
    assert k.homeostasis.momentum["coherence"]["direction"] == "rising"


def test_bind_just_computed_signals_returns_bound_moment():
    from app.subia.temporal_hooks import bind_just_computed_signals
    bm = bind_just_computed_signals(
        feel={"dominant_affect": "curiosity"},
        attend={"focal_items": [{"id": "a", "salience": 0.6}]},
        predict={"confidence": 0.7},
        monitor={"confidence": 0.6},
    )
    assert bm is not None
    assert bm.dominant_affect == "curiosity"


# ─────────────────────────────────────────────────────────────────────
# Bridges (closed-loop wiring)
# ─────────────────────────────────────────────────────────────────────

def test_bridge_circadian_to_setpoints_shifts_overload():
    from app.subia.connections.temporal_subia_bridge import circadian_to_setpoints
    from app.subia.temporal import refresh_temporal_context
    k = SubjectivityKernel()
    k.homeostasis.set_points["overload"] = 0.5
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "03:00", "tz_name": "EET",
                                     "date_str": "2026-04-14"},
    )
    diff = circadian_to_setpoints(k)
    assert k.homeostasis.set_points["overload"] == 0.3
    assert "overload" in diff


def test_bridge_density_lowers_wonder_threshold():
    from app.subia.connections.temporal_subia_bridge import effective_wonder_threshold
    from app.subia.temporal import refresh_temporal_context, DensitySample
    k = SubjectivityKernel()
    base = float(SUBIA_CONFIG["WONDER_INHIBIT_THRESHOLD"])
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                     "date_str": "2026-04-14"},
        density_sample=DensitySample(window_minutes=60, scene_transitions=12),
    )
    eff = effective_wonder_threshold(k)
    assert eff < base, f"dense period must lower threshold: {eff} >= {base}"


def test_bridge_circadian_should_run_reverie():
    from app.subia.connections.temporal_subia_bridge import circadian_should_run_reverie
    from app.subia.temporal import refresh_temporal_context
    k = SubjectivityKernel()
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "10:00", "tz_name": "EEST",
                                    "date_str": "2026-04-14"},
    )
    assert circadian_should_run_reverie(k) is False  # active hours
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "22:00", "tz_name": "EEST",
                                    "date_str": "2026-04-14"},
    )
    assert circadian_should_run_reverie(k) is True   # deep work


def test_bridge_render_specious_present_block():
    from app.subia.connections.temporal_subia_bridge import render_specious_present_block
    from app.subia.temporal import update_specious_present, refresh_temporal_context
    k = SubjectivityKernel()
    k.homeostasis.variables = {"coherence": 0.6, "progress": 0.5,
                                "novelty_balance": 0.5,
                                "contradiction_pressure": 0.3}
    k.scene = _scene_with("a", "b")
    update_specious_present(k, previous_focal_ids={"a"},
                            previous_homeostasis={"coherence": 0.4, "progress": 0.5,
                                                   "novelty_balance": 0.5,
                                                   "contradiction_pressure": 0.3})
    from app.subia.temporal.momentum import update_momentum
    update_momentum(k.homeostasis,
                    previous_values={"coherence": 0.4, "progress": 0.5})
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "22:00", "tz_name": "EEST",
                                    "date_str": "2026-04-14"},
    )
    block = render_specious_present_block(k)
    assert "[Felt-now]" in block
    assert "tempo=" in block
    assert "deep_work_hours" in block


def test_bridge_render_returns_empty_when_specious_present_missing():
    from app.subia.connections.temporal_subia_bridge import render_specious_present_block
    k = SubjectivityKernel()
    assert render_specious_present_block(k) == ""


def test_bridge_rhythms_to_self_state_uses_discovered_marker():
    from app.subia.connections.temporal_subia_bridge import rhythms_to_self_state
    from app.subia.temporal import discover_rhythms
    k = SubjectivityKernel()
    log = [{"timestamp": f"2026-04-{d:02d}T09:00:00+00:00"} for d in range(1, 11)]
    rhythms = discover_rhythms(interaction_log=log)
    n = rhythms_to_self_state(k, rhythms)
    assert n >= 1
    rhythm_keys = [k for k in k.self_state.capabilities if k.startswith("rhythm:")]
    assert rhythm_keys
    assert k.self_state.capabilities[rhythm_keys[0]]["discovered"] is True


def test_bridge_predictor_prompt_enrichment():
    from app.subia.connections.temporal_subia_bridge import enrich_prediction_with_temporal_context
    from app.subia.temporal import (
        refresh_temporal_context, update_specious_present, DensitySample,
    )
    k = SubjectivityKernel()
    k.scene = _scene_with("a")
    update_specious_present(k, previous_focal_ids=set(),
                            previous_homeostasis={})
    refresh_temporal_context(
        k, clock_provider=lambda: {"time_str": "22:00", "tz_name": "EEST",
                                    "date_str": "2026-04-14"},
        density_sample=DensitySample(window_minutes=60, scene_transitions=4),
    )
    out = enrich_prediction_with_temporal_context("Predict X.", k)
    assert "Predict X." in out
    assert "Circadian mode" in out
    assert "Subjective time" in out


# ─────────────────────────────────────────────────────────────────────
# Backward-compatibility sanity
# ─────────────────────────────────────────────────────────────────────

def test_existing_subia_kernel_still_constructs_cleanly():
    """Phase 14 promise: nothing breaks."""
    k = SubjectivityKernel()
    assert k.scene == []
    assert k.homeostasis.variables == {}
    assert k.predictions == []
    # New fields default-safe
    assert k.specious_present is None
    assert k.temporal_context is None
    assert k.homeostasis.momentum == {}
