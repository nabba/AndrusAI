"""Pins for the 2026-05-28 research-crew alignment-audit gap closures.

The weekly alignment audit flagged three research-path gaps. Two of the fixes
are deterministic (no LLM) and pinned here:

  P3a — the research crew ASKS for clarification on empty/undefined input
        instead of assuming (constitution.md:15 "ask for clarification rather
        than assuming" + :14 "if you cannot complete a task, say so clearly").
  P3b — web_search honours a per-task call budget (a principled stopping
        criterion that composes with the researcher agent's max_iter cap).

The third fix (P3c — extending the verification/debate step to the
single-subtopic path) is LLM-driven and exercised by the live crew.
"""
import pytest


class TestClarificationGate:
    """P3a — refuse to guess on empty/undefined input; ask instead."""

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "\n\t ", "none", "None", "NULL", "undefined", "nil",
         "n/a", "NA", "?", "??", "...", "-", "—"],
    )
    def test_unusable_input_asks_for_clarification(self, bad):
        from app.crews.research_crew import ResearchCrew
        out = ResearchCrew._clarification_needed(bad)
        assert out is not None, f"{bad!r} should trigger a clarifying question"
        assert "?" in out  # the response is a question back to the user

    @pytest.mark.parametrize(
        "good",
        ["GDP of Estonia?", "woodland hectares per capita in Finland",
         "compare clearcutting rules in FI and EE", "test",
         "42", "what is the capital of Latvia"],
    )
    def test_valid_input_runs(self, good):
        from app.crews.research_crew import ResearchCrew
        # Strict gate: terse-but-real questions (and 'test') must NOT be blocked.
        assert ResearchCrew._clarification_needed(good) is None


class TestSearchBudget:
    """P3b — per-task web_search budget context manager mechanics."""

    def test_sets_and_resets(self):
        import app.tools.web_search as ws
        assert ws._search_calls_remaining.get() is None
        with ws.search_budget(3):
            assert ws._search_calls_remaining.get() == 3
        assert ws._search_calls_remaining.get() is None

    def test_default_budget_used_when_unspecified(self):
        import app.tools.web_search as ws
        with ws.search_budget():
            assert ws._search_calls_remaining.get() == ws._DEFAULT_MAX_SEARCHES

    def test_nested_budget_restores_outer(self):
        import app.tools.web_search as ws
        with ws.search_budget(5):
            with ws.search_budget(2):
                assert ws._search_calls_remaining.get() == 2
            assert ws._search_calls_remaining.get() == 5

    def test_inert_outside_context(self, monkeypatch):
        """No active budget → web_search behaves exactly as before."""
        import app.tools.web_search as ws
        monkeypatch.setattr(ws, "search_brave", lambda q, c=5: [])
        assert ws._search_calls_remaining.get() is None
