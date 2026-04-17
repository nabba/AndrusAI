"""
Vetting Feedback + Cost-Aware Selection Tests
==============================================

Phase 4 coverage:
  - every vetting stage records a benchmarks row tagged with the
    canonical task type for the generating model (not the judge),
  - select_model's Pareto demotion prefers a cheaper model that
    scores within quality_gap of the default,
  - select_model's budget_usd enforcement picks the highest-scoring
    in-budget alternative when the default would blow the budget.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_vetting_feedback.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def isolated_benchmarks_db(monkeypatch, tmp_path):
    import app.llm_benchmarks as bm

    db_file = tmp_path / "benchmarks.db"
    monkeypatch.setattr(bm, "DB_PATH", db_file)
    bm._local = type(bm._local)()
    with bm._write_lock:
        bm._write_buffer.clear()
        bm._last_flush = 0.0
    yield bm


class TestVettingFeedback:
    def test_schema_pass_records_success(self, isolated_benchmarks_db):
        from app.vetting import _verify_schema
        bm = isolated_benchmarks_db

        response = "This is a valid, substantive answer to the question."
        passed, _ = _verify_schema(response, "writing",
                                   generating_model="openrouter/model-a")
        assert passed is True

        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT model, task_type, success FROM benchmarks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("openrouter/model-a", "writing", 1)

    def test_schema_fail_records_failure(self, isolated_benchmarks_db):
        from app.vetting import _verify_schema
        bm = isolated_benchmarks_db

        passed, _ = _verify_schema("too short", "writing",
                                   generating_model="openrouter/model-a")
        assert passed is False
        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT model, task_type, success FROM benchmarks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("openrouter/model-a", "writing", 0)

    def test_cheap_records_against_generating_model(self, isolated_benchmarks_db):
        """Verifies the judge's model is NOT what lands in benchmarks —
        the generating model is."""
        from app import vetting
        bm = isolated_benchmarks_db

        judge = MagicMock()
        judge.call = MagicMock(return_value="FAIL: too vague")

        with patch.object(vetting, "_get_cheap_vetting_llm", return_value=judge):
            passed, _ = vetting._verify_cheap(
                "user question", "long enough response text here",
                "coding", generating_model="openrouter/coder-model",
            )
        assert passed is False

        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT model, task_type, success FROM benchmarks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("openrouter/coder-model", "coding", 0)

    def test_no_generating_model_skips_record(self, isolated_benchmarks_db):
        from app.vetting import _verify_schema
        bm = isolated_benchmarks_db

        _verify_schema("a valid long response", "writing", generating_model=None)
        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM benchmarks"
        ).fetchone()
        assert row[0] == 0


class TestParetoDemotion:
    @pytest.fixture
    def silence_db(self, monkeypatch):
        # Avoid role_assignments DB lookups during unit tests.
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )

    def test_pareto_picks_cheaper_when_score_close(self, silence_db, monkeypatch):
        """Given two budget-tier models where the cheaper one scores
        within quality_gap of the default, Pareto demotion fires."""
        from app import llm_selector

        # Fake get_scores to return a close-enough cheaper alternative.
        scores = {
            "claude-opus-4.6": 0.88,
            "deepseek-v3.2":   0.85,
        }
        monkeypatch.setattr(llm_selector, "get_scores", lambda t: scores)

        # Force balanced commander default to claude-opus-4.6.
        monkeypatch.setattr(
            "app.llm_catalog.get_default_for_role",
            lambda role, cost_mode: "claude-opus-4.6",
        )
        # Convince _model_available that both are reachable.
        monkeypatch.setattr(
            llm_selector, "_model_available", lambda *a, **kw: True,
        )
        # Clear env overrides.
        monkeypatch.delenv("ROLE_MODEL_COMMANDER", raising=False)

        chosen = llm_selector.select_model("commander", task_hint="routing")
        assert chosen == "deepseek-v3.2"

    def test_pareto_no_swap_when_score_gap_too_large(self, silence_db, monkeypatch):
        from app import llm_selector

        scores = {
            "claude-opus-4.6": 0.92,
            "deepseek-v3.2":   0.50,  # too far below default
        }
        monkeypatch.setattr(llm_selector, "get_scores", lambda t: scores)
        monkeypatch.setattr(
            "app.llm_catalog.get_default_for_role",
            lambda role, cost_mode: "claude-opus-4.6",
        )
        monkeypatch.setattr(
            llm_selector, "_model_available", lambda *a, **kw: True,
        )
        monkeypatch.delenv("ROLE_MODEL_COMMANDER", raising=False)

        chosen = llm_selector.select_model("commander", task_hint="routing")
        assert chosen == "claude-opus-4.6"


class TestBudgetEnforcement:
    @pytest.fixture
    def silence_db(self, monkeypatch):
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda *a, **kw: None,
        )

    def test_budget_demotes_to_cheapest_in_range(self, silence_db, monkeypatch):
        from app import llm_selector

        scores = {
            "claude-opus-4.6": 0.92,
            "deepseek-v3.2":   0.88,
        }
        monkeypatch.setattr(llm_selector, "get_scores", lambda t: scores)
        monkeypatch.setattr(
            "app.llm_catalog.get_default_for_role",
            lambda role, cost_mode: "claude-opus-4.6",
        )
        monkeypatch.setattr(
            llm_selector, "_model_available", lambda *a, **kw: True,
        )
        monkeypatch.delenv("ROLE_MODEL_COMMANDER", raising=False)

        # Opus costs ~$25/M output; 2000 tokens output = ~$0.05
        # DeepSeek V3.2 at $0.42/M output; 2000 tokens = ~$0.0008
        # Budget of $0.01 forces demotion to deepseek.
        chosen = llm_selector.select_model(
            "commander", task_hint="routing",
            expected_input_tokens=2000, expected_output_tokens=2000,
            budget_usd=0.01,
        )
        assert chosen == "deepseek-v3.2"

    def test_budget_kept_default_when_within_budget(self, silence_db, monkeypatch):
        from app import llm_selector

        scores = {"claude-opus-4.6": 0.92}
        monkeypatch.setattr(llm_selector, "get_scores", lambda t: scores)
        monkeypatch.setattr(
            "app.llm_catalog.get_default_for_role",
            lambda role, cost_mode: "claude-opus-4.6",
        )
        monkeypatch.setattr(
            llm_selector, "_model_available", lambda *a, **kw: True,
        )
        monkeypatch.delenv("ROLE_MODEL_COMMANDER", raising=False)

        chosen = llm_selector.select_model(
            "commander", task_hint="routing",
            expected_input_tokens=200, expected_output_tokens=200,
            budget_usd=10.0,
        )
        assert chosen == "claude-opus-4.6"
