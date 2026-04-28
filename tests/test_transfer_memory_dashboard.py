"""Tests for app.transfer_memory.dashboard."""

import json
import time

import pytest

from app.transfer_memory import dashboard as D


@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


def _write_shadow_drafts(dir, rows):
    p = dir / "shadow_drafts.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _write_jsonl(dir, name, rows):
    p = dir / name
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ── Overview ─────────────────────────────────────────────────────────

def test_overview_empty_state(tmp_queue_dir, monkeypatch):
    monkeypatch.setattr(D, "_kb_record_count", lambda **kw: 0)
    o = D.get_overview()
    assert o["queue"]["pending"] == 0
    assert o["compiled"]["total_drafts_logged"] == 0
    assert o["demotions"]["blacklist_size"] == 0
    assert o["promotion"]["auto_promote_enabled"] in (True, False)


def test_overview_counts_after_writes(tmp_queue_dir, monkeypatch):
    _write_shadow_drafts(tmp_queue_dir, [
        {"event_id": "e1", "draft": {"id": "d1"}},
        {"event_id": "e2", "draft": {"id": "d2"}},
        {"event_id": "e3", "error": "llm_call_failed"},
    ])
    (tmp_queue_dir / "demotion_blacklist.jsonl").write_text("skill_a\nskill_b\n")
    monkeypatch.setattr(D, "_kb_record_count", lambda **kw: 0)

    o = D.get_overview()
    assert o["compiled"]["total_drafts_logged"] == 3
    assert o["demotions"]["blacklist_size"] == 2


# ── By source kind ───────────────────────────────────────────────────

def test_by_source_kind_aggregates(tmp_queue_dir):
    _write_shadow_drafts(tmp_queue_dir, [
        {
            "kind": "healing",
            "draft": {"id": "d1"},
            "abstraction_score": 0.8,
            "leakage_risk": 0.0,
        },
        {
            "kind": "healing",
            "draft": {"id": "d2"},
            "abstraction_score": 0.6,
            "leakage_risk": 0.1,
        },
        {"kind": "healing", "error": "llm_failed"},
        {"kind": "evo_success", "draft": {"id": "d3"}, "abstraction_score": 0.7},
    ])
    by_kind = D.get_by_source_kind()
    assert by_kind["healing"]["compiled"] == 2
    assert by_kind["healing"]["errors"] == 1
    assert by_kind["healing"]["total"] == 3
    assert by_kind["evo_success"]["compiled"] == 1
    assert 0.0 < by_kind["healing"]["avg_abstraction"] <= 1.0


# ── Recent activity ──────────────────────────────────────────────────

def test_recent_activity_respects_window(tmp_queue_dir):
    now = time.time()
    long_ago = now - (30 * 86400)
    _write_shadow_drafts(tmp_queue_dir, [
        {"event_id": "e_old", "draft": {"id": "d1"}, "compiled_at": long_ago},
        {"event_id": "e_new", "draft": {"id": "d2"}, "compiled_at": now},
    ])
    activity = D.get_recent_activity(days=7)
    assert activity["drafts_compiled"] == 1


# ── Top / worst performers ───────────────────────────────────────────

def test_top_performers_excludes_those_with_negatives(tmp_queue_dir):
    _write_shadow_drafts(tmp_queue_dir, [
        {"event_id": "e1", "draft": {
            "id": "good", "topic": "Good rule", "source_kind": "healing",
            "source_domain": "healing", "transfer_scope": "global_meta",
            "abstraction_score": 0.7,
        }},
        {"event_id": "e2", "draft": {
            "id": "bad", "topic": "Bad rule", "source_kind": "evo_failure",
            "source_domain": "evolution", "transfer_scope": "global_meta",
            "abstraction_score": 0.5,
        }},
    ])
    _write_jsonl(tmp_queue_dir, "shadow_retrievals.jsonl", [
        {"surfaced": [{"skill_record_id": "good"}, {"skill_record_id": "bad"}]},
        {"surfaced": [{"skill_record_id": "good"}, {"skill_record_id": "bad"}]},
        {"surfaced": [{"skill_record_id": "good"}]},
    ])
    _write_jsonl(tmp_queue_dir, "negative_transfer.jsonl", [
        {"skill_record_id": "bad", "tag": "domain_mismatched_anchor"},
    ])
    top = D.get_top_performers(n=10)
    ids = [r["skill_record_id"] for r in top]
    assert "good" in ids
    assert "bad" not in ids


def test_worst_performers_ranks_by_failures(tmp_queue_dir):
    _write_shadow_drafts(tmp_queue_dir, [
        {"event_id": "e1", "draft": {"id": "x", "topic": "x"}},
        {"event_id": "e2", "draft": {"id": "y", "topic": "y"}},
    ])
    _write_jsonl(tmp_queue_dir, "negative_transfer.jsonl", [
        {"skill_record_id": "x", "tag": "domain_mismatched_anchor"},
        {"skill_record_id": "x", "tag": "over_abstraction"},
        {"skill_record_id": "x", "tag": "domain_mismatched_anchor"},
        {"skill_record_id": "y", "tag": "domain_mismatched_anchor"},
    ])
    worst = D.get_worst_performers(n=10)
    assert worst[0]["skill_record_id"] == "x"
    assert worst[0]["total_failures"] == 3
    assert worst[1]["skill_record_id"] == "y"


# ── Sanitizer stats ──────────────────────────────────────────────────

def test_sanitizer_stats_counts_hard_rejects(tmp_queue_dir):
    _write_shadow_drafts(tmp_queue_dir, [
        {
            "sanitizer_findings": [
                ["hard_reject:anthropic_key", "redacted"],
            ],
            "sanitizer_max_scope": "shadow",
        },
        {
            "sanitizer_findings": [
                ["project_noun:plg", "plg"],
            ],
            "sanitizer_max_scope": "project_local",
        },
        {"sanitizer_findings": [], "sanitizer_max_scope": "global_meta"},
    ])
    stats = D.get_sanitizer_stats()
    assert stats["hard_rejects"] == 1
    assert stats["by_max_scope"].get("project_local") == 1
    assert stats["by_max_scope"].get("global_meta") == 1


# ── Source × target matrix ───────────────────────────────────────────

def test_source_target_matrix(tmp_queue_dir):
    _write_jsonl(tmp_queue_dir, "shadow_retrievals.jsonl", [
        {"target_domain": "coding", "surfaced": [
            {"source_domain": "healing"}, {"source_domain": "evolution"},
        ]},
        {"target_domain": "coding", "surfaced": [{"source_domain": "healing"}]},
        {"target_domain": "research", "surfaced": [{"source_domain": "grounding"}]},
    ])
    matrix = D.get_source_to_target_matrix()
    assert matrix["healing"]["coding"] == 2
    assert matrix["evolution"]["coding"] == 1
    assert matrix["grounding"]["research"] == 1


# ── Negative transfer stats ──────────────────────────────────────────

def test_negative_transfer_stats(tmp_queue_dir):
    _write_jsonl(tmp_queue_dir, "negative_transfer.jsonl", [
        {"skill_record_id": "x", "tag": "domain_mismatched_anchor", "ts": 1.0},
        {"skill_record_id": "y", "tag": "over_abstraction", "ts": 2.0},
        {"skill_record_id": "z", "tag": "domain_mismatched_anchor", "ts": 3.0},
    ])
    stats = D.get_negative_transfer_stats()
    assert stats["total"] == 3
    assert stats["by_tag"]["domain_mismatched_anchor"] == 2
    assert stats["by_tag"]["over_abstraction"] == 1
    # recent is sorted ts-desc.
    assert stats["recent"][0]["ts"] == 3.0
