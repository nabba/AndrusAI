"""
Phase 4: kernel serialization / hot-md round-trip regression tests.

Verifies:
  - serialize_kernel_to_markdown / load_kernel_from_markdown round-trip
    is lossless for non-trivial kernel state (scene items, commitments,
    predictions, social models, homeostasis).
  - generate_hot_md produces compact session-continuity markdown with
    frontmatter that apply_hot_md can read back.
  - save_kernel_state / load_kernel_state use safe_io atomic writes
    and survive process crashes.
  - Corrupt markdown returns a default kernel rather than crashing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.subia.kernel import (
    Commitment,
    HomeostaticState,
    MetaMonitorState,
    Prediction,
    SceneItem,
    SocialModelEntry,
    SubjectivityKernel,
)
from app.subia.persistence import (
    apply_hot_md,
    generate_hot_md,
    load_kernel_from_markdown,
    load_kernel_state,
    save_kernel_state,
    serialize_kernel_to_markdown,
)


def _rich_kernel() -> SubjectivityKernel:
    k = SubjectivityKernel(
        loop_count=42,
        last_loop_at="2026-04-13T10:00:00+00:00",
        session_id="session-xyz",
    )
    k.scene.append(SceneItem(
        id="s1", source="wiki", content_ref="archibal/landscape.md",
        summary="Truepic Series C analysis", salience=0.82,
        entered_at="2026-04-13T09:55:00+00:00",
        ownership="self", valence=0.25,
        dominant_affect="urgency", tier="focal",
    ))
    k.scene.append(SceneItem(
        id="s2", source="mem0", content_ref="mem0:episode-10",
        summary="peripheral item", salience=0.21,
        entered_at="2026-04-13T09:58:00+00:00",
        tier="peripheral",
    ))
    k.self_state.current_goals = ["ship Q2 plan", "fundraising support"]
    k.self_state.capabilities = {"research": "high", "coding": "medium"}
    k.self_state.active_commitments.append(Commitment(
        id="c1", description="draft investor memo",
        venture="archibal", created_at="2026-04-10T00:00:00+00:00",
        deadline="2026-04-20T23:59:59+00:00",
        status="active",
        related_wiki_pages=["archibal/investor-memo.md"],
        homeostatic_impact={"progress": 0.1},
    ))
    k.self_state.agency_log = [
        {"at": "2026-04-13T09:50:00+00:00", "summary": "agent ran"},
    ]
    k.homeostasis = HomeostaticState(
        variables={"coherence": 0.6, "progress": 0.4},
        set_points={"coherence": 0.5, "progress": 0.55},
        deviations={"coherence": 0.1, "progress": -0.15},
        restoration_queue=["progress"],
        last_updated="2026-04-13T10:00:00+00:00",
    )
    k.meta_monitor = MetaMonitorState(
        confidence=0.7,
        known_unknowns=["TikTok tier impact", "Protect Group terms"],
        uncertainty_sources=["low novelty in KaiCart"],
    )
    k.predictions.append(Prediction(
        id="p1", operation="researcher:ingest",
        predicted_outcome={"wiki_pages_affected": ["archibal/landscape.md"]},
        predicted_self_change={"confidence_change": 0.05},
        predicted_homeostatic_effect={"novelty_balance": 0.1},
        confidence=0.75,
        created_at="2026-04-13T09:55:00+00:00",
        resolved=True,
        actual_outcome={"wiki_pages_affected": ["archibal/landscape.md"]},
        prediction_error=0.1,
    ))
    k.social_models["andrus"] = SocialModelEntry(
        entity_id="andrus", entity_type="human",
        inferred_focus=["Archibal fundraising", "KaiCart resilience"],
        inferred_priorities=["Archibal > KaiCart > PLG"],
        trust_level=0.9,
        last_interaction="2026-04-13T08:00:00+00:00",
    )
    return k


# ── serialize_kernel / load_kernel round-trip ───────────────────

class TestRoundTrip:
    def test_default_kernel_round_trips(self):
        k = SubjectivityKernel()
        content = serialize_kernel_to_markdown(k)
        restored = load_kernel_from_markdown(content)
        assert restored.loop_count == 0
        assert restored.scene == []
        assert restored.self_state.identity["name"] == "AndrusAI"

    def test_rich_kernel_round_trips(self):
        k = _rich_kernel()
        content = serialize_kernel_to_markdown(k)
        restored = load_kernel_from_markdown(content)

        assert restored.loop_count == 42
        assert restored.session_id == "session-xyz"
        assert len(restored.scene) == 2
        assert restored.scene[0].summary == "Truepic Series C analysis"
        assert restored.scene[0].tier == "focal"
        assert restored.scene[1].tier == "peripheral"
        assert restored.scene[0].dominant_affect == "urgency"

        assert restored.self_state.current_goals == [
            "ship Q2 plan", "fundraising support",
        ]
        assert restored.self_state.capabilities == {
            "research": "high", "coding": "medium",
        }
        assert len(restored.self_state.active_commitments) == 1
        c = restored.self_state.active_commitments[0]
        assert c.id == "c1"
        assert c.deadline == "2026-04-20T23:59:59+00:00"
        assert c.homeostatic_impact == {"progress": 0.1}

        assert restored.homeostasis.variables == {"coherence": 0.6, "progress": 0.4}
        assert restored.homeostasis.restoration_queue == ["progress"]

        assert len(restored.predictions) == 1
        p = restored.predictions[0]
        assert p.resolved is True
        assert p.prediction_error == 0.1
        assert p.predicted_outcome == {
            "wiki_pages_affected": ["archibal/landscape.md"],
        }

        assert "andrus" in restored.social_models
        assert restored.social_models["andrus"].trust_level == 0.9

        assert restored.meta_monitor.confidence == 0.7
        assert len(restored.meta_monitor.known_unknowns) == 2

    def test_body_contains_scene_summary(self):
        """The markdown body (below frontmatter) should include the
        scene summary for human readability.
        """
        k = _rich_kernel()
        content = serialize_kernel_to_markdown(k)
        assert "## Current focal scene" in content
        assert "Truepic Series C analysis" in content

    def test_body_contains_homeostasis_alerts(self):
        k = _rich_kernel()
        # Bump progress deviation above threshold
        k.homeostasis.deviations["progress"] = -0.5
        content = serialize_kernel_to_markdown(k)
        assert "progress: -0.50" in content

    def test_frontmatter_includes_metadata(self):
        k = _rich_kernel()
        content = serialize_kernel_to_markdown(k)
        assert "title:" in content
        assert "SubIA Kernel State" in content
        assert "ownership:" in content


# ── hot.md round-trip ───────────────────────────────────────────

class TestHotMd:
    def test_generate_has_frontmatter_and_body(self):
        k = _rich_kernel()
        out = generate_hot_md(k)
        assert out.startswith("---\n")
        assert "loop_count: 42" in out
        assert 'session_id: "session-xyz"' in out
        assert "## Last focal scene" in out
        assert "Truepic Series C analysis" in out[:1000]

    def test_hot_md_has_resume_hint(self):
        k = _rich_kernel()
        k.homeostasis.deviations["progress"] = -0.6  # above threshold
        out = generate_hot_md(k)
        assert "## Resume hint" in out
        # With negative progress deviation, hint says "increase progress"
        assert "progress" in out.lower()

    def test_hot_md_empty_kernel(self):
        k = SubjectivityKernel()
        out = generate_hot_md(k)
        assert "(scene empty)" in out
        assert "(none)" in out
        assert "(all within equilibrium)" in out

    def test_apply_hot_md_picks_up_loop_count(self):
        k = SubjectivityKernel(loop_count=0)
        source = _rich_kernel()
        hot_content = generate_hot_md(source)
        apply_hot_md(k, hot_content)
        assert k.loop_count == 42
        assert k.session_id == "session-xyz"

    def test_apply_hot_md_never_rewinds(self):
        """If hot.md's loop_count is less than current, don't rewind."""
        k = SubjectivityKernel(loop_count=100)
        source = SubjectivityKernel(loop_count=5, session_id="s")
        apply_hot_md(k, generate_hot_md(source))
        assert k.loop_count == 100

    def test_apply_hot_md_corrupt_content_safe(self):
        k = SubjectivityKernel(loop_count=50)
        apply_hot_md(k, "garbage with no frontmatter")
        assert k.loop_count == 50

    def test_apply_hot_md_malformed_frontmatter_safe(self):
        k = SubjectivityKernel(loop_count=50)
        apply_hot_md(k, "---\nnot: valid: yaml:\n---\nbody\n")
        # Must not crash; loop_count unchanged (no valid loop_count field)
        assert k.loop_count == 50


# ── Disk-backed save/load ───────────────────────────────────────

class TestSaveLoad:
    def test_save_and_load_round_trips(self, tmp_path, monkeypatch):
        # Redirect persistence constants to tmp_path
        from app.subia import persistence as p
        kernel_state = tmp_path / "kernel-state.md"
        hot_md = tmp_path / "hot.md"
        monkeypatch.setattr(p, "KERNEL_STATE", kernel_state)
        monkeypatch.setattr(p, "HOT_MD", hot_md)

        k = _rich_kernel()
        save_kernel_state(k)
        assert kernel_state.exists()
        assert hot_md.exists()

        restored = load_kernel_state()
        assert restored.loop_count == 42
        assert restored.session_id == "session-xyz"

    def test_load_missing_returns_default(self, tmp_path):
        missing = tmp_path / "nope.md"
        k = load_kernel_state(missing)
        assert k.loop_count == 0
        assert k.scene == []

    def test_load_corrupt_returns_default(self, tmp_path):
        bad = tmp_path / "bad.md"
        bad.write_text("this is not a valid kernel page")
        k = load_kernel_state(bad)
        assert isinstance(k, SubjectivityKernel)
        assert k.loop_count == 0

    def test_save_is_atomic(self, tmp_path, monkeypatch):
        from app.subia import persistence as p
        kernel_state = tmp_path / "kernel.md"
        hot_md = tmp_path / "hot.md"
        monkeypatch.setattr(p, "KERNEL_STATE", kernel_state)
        monkeypatch.setattr(p, "HOT_MD", hot_md)
        save_kernel_state(_rich_kernel())
        # No .tmp leftover after a successful save.
        leftovers = [p_.name for p_ in tmp_path.iterdir()
                     if p_.name not in ("kernel.md", "hot.md")]
        assert leftovers == [], f"unexpected: {leftovers}"


# ── Edge cases ──────────────────────────────────────────────────

class TestEdges:
    def test_prediction_cap_truncates_to_last_64(self):
        k = SubjectivityKernel()
        for i in range(100):
            k.predictions.append(Prediction(
                id=f"p{i}", operation=f"op-{i}",
                predicted_outcome={}, predicted_self_change={},
                predicted_homeostatic_effect={},
                confidence=0.5, created_at="",
            ))
        content = serialize_kernel_to_markdown(k)
        restored = load_kernel_from_markdown(content)
        assert len(restored.predictions) == 64
        assert restored.predictions[-1].id == "p99"
        assert restored.predictions[0].id == "p36"   # 100 - 64

    def test_agency_log_cap_200(self):
        k = SubjectivityKernel()
        k.self_state.agency_log = [
            {"at": f"t{i}", "summary": f"e{i}"} for i in range(400)
        ]
        content = serialize_kernel_to_markdown(k)
        restored = load_kernel_from_markdown(content)
        assert len(restored.self_state.agency_log) == 200

    def test_special_chars_in_strings_round_trip(self):
        k = SubjectivityKernel()
        k.scene.append(SceneItem(
            id="s1", source="test", content_ref="x",
            summary='with "quotes" and \\ backslash and :colons',
            salience=0.5, entered_at="",
        ))
        content = serialize_kernel_to_markdown(k)
        restored = load_kernel_from_markdown(content)
        assert restored.scene[0].summary == (
            'with "quotes" and \\ backslash and :colons'
        )
