"""Tests for evaluator.scan_for_low_effectiveness_tips (Phase 6)."""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _mk_skill_record(record_id: str, is_tip: bool = True,
                      source_trajectory_id: str = "traj_x"):
    """Build a SkillRecord with or without tip provenance."""
    from app.self_improvement.types import SkillRecord
    provenance = {"gap_id": "", "draft_id": "", "rationale": ""}
    if is_tip:
        provenance["tip_type"] = "recovery"
        provenance["source_trajectory_id"] = source_trajectory_id
    return SkillRecord(
        id=record_id, topic=f"topic {record_id}", content_markdown="...",
        kb="tensions", status="active",
        provenance=provenance, created_at="2026-04-01T00:00:00+00:00",
    )


def test_scan_for_low_effectiveness_tips_emits_for_poor_tips(monkeypatch, tmp_path):
    """A tip with many uses and <35% effectiveness should produce a gap."""
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    # 12 uses, 2 successes → 0.167 effectiveness (below 0.35 threshold)
    rows = []
    for i in range(2):
        rows.append({"skill_id": "skill_bad", "trajectory_id": f"s{i}",
                      "passed_quality_gate": True, "retries": 0, "verdict": "baseline"})
    for i in range(10):
        rows.append({"skill_id": "skill_bad", "trajectory_id": f"f{i}",
                      "passed_quality_gate": False, "retries": 2, "verdict": "failure"})
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    # Mock load_record to return a tip-type skill
    def fake_load_record(rid):
        if rid == "skill_bad":
            return _mk_skill_record("skill_bad", is_tip=True)
        return None

    emitted_gaps: list = []
    def fake_emit_gap(gap):
        emitted_gaps.append(gap)
        return True

    with patch("app.self_improvement.integrator.load_record",
               side_effect=fake_load_record), \
         patch("app.self_improvement.store.emit_gap",
               side_effect=fake_emit_gap):
        from app.self_improvement.evaluator import scan_for_low_effectiveness_tips
        n = scan_for_low_effectiveness_tips()

    assert n == 1
    assert len(emitted_gaps) == 1
    gap = emitted_gaps[0]
    assert gap.evidence["skill_record_id"] == "skill_bad"
    assert gap.evidence["reason"] == "low_effectiveness"
    assert gap.evidence["uses"] == 12


def test_scan_ignores_external_topic_skills(monkeypatch, tmp_path):
    """Non-tip (external-topic) skills are handled by scan_for_decay, not this sweep."""
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    rows = [{"skill_id": "external_skill", "trajectory_id": f"t{i}",
              "passed_quality_gate": False, "retries": 0, "verdict": "failure"}
             for i in range(15)]
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    def fake_load_record(rid):
        if rid == "external_skill":
            return _mk_skill_record("external_skill", is_tip=False)
        return None

    emitted: list = []
    with patch("app.self_improvement.integrator.load_record",
               side_effect=fake_load_record), \
         patch("app.self_improvement.store.emit_gap",
               side_effect=lambda g: emitted.append(g) or True):
        from app.self_improvement.evaluator import scan_for_low_effectiveness_tips
        n = scan_for_low_effectiveness_tips()

    # No emissions — tip_type missing → skipped
    assert n == 0
    assert emitted == []


def test_scan_ignores_tips_above_threshold(monkeypatch, tmp_path):
    """Tips with ≥35% effectiveness are healthy — no gap emitted."""
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    # 10 uses, 6 successes → 0.6 effectiveness (above threshold)
    rows = [{"skill_id": "skill_good", "trajectory_id": f"t{i}",
              "passed_quality_gate": (i < 6), "retries": 0, "verdict": "baseline"}
             for i in range(10)]
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    def fake_load_record(rid):
        if rid == "skill_good":
            return _mk_skill_record("skill_good", is_tip=True)
        return None

    emitted: list = []
    with patch("app.self_improvement.integrator.load_record",
               side_effect=fake_load_record), \
         patch("app.self_improvement.store.emit_gap",
               side_effect=lambda g: emitted.append(g) or True):
        from app.self_improvement.evaluator import scan_for_low_effectiveness_tips
        n = scan_for_low_effectiveness_tips()

    assert n == 0


def test_scan_below_min_uses_no_emit(monkeypatch, tmp_path):
    """A tip with <10 uses can't be acted on even at 0% effectiveness."""
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    rows = [{"skill_id": "skill_small", "trajectory_id": f"t{i}",
              "passed_quality_gate": False, "retries": 0, "verdict": "failure"}
             for i in range(5)]
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    def fake_load_record(rid):
        return _mk_skill_record("skill_small", is_tip=True)

    emitted: list = []
    with patch("app.self_improvement.integrator.load_record",
               side_effect=fake_load_record), \
         patch("app.self_improvement.store.emit_gap",
               side_effect=lambda g: emitted.append(g) or True):
        from app.self_improvement.evaluator import scan_for_low_effectiveness_tips
        n = scan_for_low_effectiveness_tips()

    assert n == 0
