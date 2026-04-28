"""Tests for app.transfer_memory.retriever."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.transfer_memory import retriever as r


@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


# ── Pure helpers ─────────────────────────────────────────────────────

def test_compose_plan_query_includes_crew_and_intent():
    q = r._compose_plan_query(
        crew_name="researcher",
        task_text="Investigate Finnish forest biomass density trends.",
        predicted_failure_mode="hallucinated_citation",
        risk_tier="medium",
        expected_output_type="report",
    )
    assert "crew=researcher" in q
    assert "intent=" in q
    assert "failure_mode=hallucinated_citation" in q
    assert "risk_tier=medium" in q
    assert "output_type=report" in q


def test_compose_plan_query_handles_empty_task():
    q = r._compose_plan_query(crew_name="coder", task_text="")
    assert "crew=coder" in q
    assert "intent=" in q


def test_crew_to_domain_known():
    assert r._crew_to_domain("researcher") == "research"
    assert r._crew_to_domain("Coder") == "coding"
    assert r._crew_to_domain("commander") == "ops"


def test_crew_to_domain_unknown_returns_empty():
    assert r._crew_to_domain("totally_unknown_crew") == ""
    assert r._crew_to_domain("") == ""


# ── Post-ranking ─────────────────────────────────────────────────────

def _result(text, score, **meta):
    res = MagicMock()
    res.text = text
    res.score = score
    res.metadata = meta
    return res


def test_post_rank_prefers_higher_abstraction():
    a = _result("low abstraction", 0.7, abstraction_score=0.2, leakage_risk=0.0,
                source_domain="coding")
    b = _result("high abstraction", 0.7, abstraction_score=0.9, leakage_risk=0.0,
                source_domain="coding")
    ranked = r._post_rank([a, b], target_domain="coding")
    assert ranked[0] is b


def test_post_rank_penalises_high_leakage():
    a = _result("clean", 0.7, abstraction_score=0.5, leakage_risk=0.0,
                source_domain="coding")
    b = _result("leaky", 0.7, abstraction_score=0.5, leakage_risk=0.9,
                source_domain="coding")
    ranked = r._post_rank([a, b], target_domain="coding")
    assert ranked[0] is a


def test_post_rank_penalises_domain_mismatch():
    same = _result("same domain", 0.5, abstraction_score=0.5, leakage_risk=0.0,
                    source_domain="coding")
    other = _result("other domain", 0.5, abstraction_score=0.5, leakage_risk=0.0,
                     source_domain="grounding")
    ranked = r._post_rank([same, other], target_domain="coding")
    assert ranked[0] is same


def test_post_rank_no_target_domain_no_mismatch_penalty():
    a = _result("x", 0.5, abstraction_score=0.5, leakage_risk=0.0,
                source_domain="coding")
    b = _result("y", 0.5, abstraction_score=0.5, leakage_risk=0.0,
                source_domain="evolution")
    ranked = r._post_rank([a, b], target_domain="")
    # Both score equally; ordering is stable input-order.
    assert {ranked[0] is a, ranked[0] is b} == {True, False}


# ── Project-local filter ─────────────────────────────────────────────

def test_filter_project_local_drops_non_matching_origin():
    plg = _result("plg", 0.8, transfer_scope="project_local", project_origin="plg")
    archibal = _result("archibal", 0.8, transfer_scope="project_local",
                        project_origin="archibal")
    out = r._filter_project_local([plg, archibal], project_scope="plg")
    assert plg in out and archibal not in out


def test_filter_project_local_passes_global():
    g = _result("global", 0.8, transfer_scope="global_meta", project_origin="")
    out = r._filter_project_local([g], project_scope="plg")
    assert g in out


def test_filter_project_local_no_active_project_drops_all_local():
    plg = _result("plg", 0.8, transfer_scope="project_local", project_origin="plg")
    g = _result("global", 0.8, transfer_scope="global_meta", project_origin="")
    out = r._filter_project_local([plg, g], project_scope=None)
    assert g in out and plg not in out


# ── Blacklist filter ─────────────────────────────────────────────────

def test_filter_blacklist_drops_blacklisted(monkeypatch):
    monkeypatch.setattr(
        "app.transfer_memory.attribution.is_blacklisted",
        lambda rid: rid == "skill_episteme_bad",
    )
    good = _result("ok", 0.8, skill_record_id="skill_episteme_good")
    bad = _result("nope", 0.8, skill_record_id="skill_episteme_bad")
    out = r._filter_blacklist([good, bad])
    assert good in out and bad not in out


# ── Public entry: production retrieval ───────────────────────────────

def test_compose_block_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(r, "_retrieval_enabled", lambda: False)
    out = r.compose_transfer_insight_block(
        crew_name="coder", task_text="x", project_scope=None,
    )
    assert out == ""


def test_compose_block_returns_empty_when_no_results(monkeypatch):
    monkeypatch.setattr(r, "_retrieval_enabled", lambda: True)
    monkeypatch.setattr(r, "_allowed_domains", lambda: ())
    monkeypatch.setattr(r, "_query_records", lambda **kw: [])
    out = r.compose_transfer_insight_block(crew_name="coder", task_text="x")
    assert out == ""


def test_compose_block_renders_when_results_present(monkeypatch):
    monkeypatch.setattr(r, "_retrieval_enabled", lambda: True)
    monkeypatch.setattr(r, "_allowed_domains", lambda: ())
    monkeypatch.setattr(
        r, "_query_records",
        lambda **kw: [_result(
            "Verify external numeric claims before finalising.",
            0.8,
            skill_record_id="skill_x",
            topic="Verification rule",
            source_kind="grounding_correction",
            source_domain="grounding",
            transfer_scope="global_meta",
            abstraction_score=0.7,
            leakage_risk=0.0,
        )],
    )
    monkeypatch.setattr(
        "app.transfer_memory.attribution.is_blacklisted", lambda rid: False,
    )
    out = r.compose_transfer_insight_block(crew_name="coder", task_text="something")
    assert "<transfer_memory>" in out
    assert "Verification rule" in out
    assert "scope=global_meta" in out


def test_compose_block_filters_disabled_domains(monkeypatch):
    """When transfer_memory_enabled_domains is set, off-allowlist crews
    return empty regardless of matches."""
    monkeypatch.setattr(r, "_retrieval_enabled", lambda: True)
    monkeypatch.setattr(r, "_allowed_domains", lambda: ("research",))
    out = r.compose_transfer_insight_block(crew_name="coder", task_text="x")
    assert out == ""


# ── Public entry: shadow retrieval logging ───────────────────────────

def test_log_shadow_returns_zero_when_disabled(monkeypatch, tmp_queue_dir):
    monkeypatch.setattr(r, "_shadow_logging_enabled", lambda: False)
    n = r.log_shadow_retrieval(crew_name="coder", task_text="x")
    assert n == 0
    assert not (tmp_queue_dir / "shadow_retrievals.jsonl").exists()


def test_log_shadow_writes_jsonl(monkeypatch, tmp_queue_dir):
    monkeypatch.setattr(r, "_shadow_logging_enabled", lambda: True)
    monkeypatch.setattr(
        r, "_query_records",
        lambda **kw: [_result(
            "verify discipline", 0.8,
            skill_record_id="skill_a",
            topic="Verify",
            source_kind="grounding_correction",
            source_domain="grounding",
            transfer_scope="shadow",
            abstraction_score=0.8,
        )],
    )
    n = r.log_shadow_retrieval(crew_name="coder", task_text="task plan")
    assert n == 1
    p = tmp_queue_dir / "shadow_retrievals.jsonl"
    assert p.exists()
    rows = [json.loads(ln) for ln in p.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["crew_name"] == "coder"
    assert rows[0]["surfaced_count"] == 1
    assert rows[0]["surfaced"][0]["skill_record_id"] == "skill_a"
