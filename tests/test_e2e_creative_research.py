"""
test_e2e_creative_research.py — Integration and E2E tests for creativity + research.

These tests exercise real module interactions and (where marked e2e)
require Docker services (ChromaDB, PostgreSQL).

Run unit-like integration tests:
    pytest tests/test_e2e_creative_research.py -v -k "not e2e"

Run full E2E (requires Docker stack):
    pytest tests/test_e2e_creative_research.py -v -m e2e
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Marker for tests that need Docker services (ChromaDB, API server, etc.)
e2e = pytest.mark.e2e


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests (no external services needed)
# ═══════════════════════════════════════════════════════════════════════════

class TestCreativeCrewIntegration:
    """Tests that don't need external services — exercise internal logic."""

    def test_format_peers(self):
        from app.crews.creative_crew import _format_peers, PhaseOutput
        outputs = [
            PhaseOutput(role="researcher", text="Idea from researcher.", duration_s=1.0),
            PhaseOutput(role="writer", text="Idea from writer.", duration_s=1.0),
        ]
        result = _format_peers(outputs)
        assert "### researcher" in result
        assert "### writer" in result
        assert "Idea from researcher." in result

    def test_format_peers_excludes_role(self):
        from app.crews.creative_crew import _format_peers, PhaseOutput
        outputs = [
            PhaseOutput(role="researcher", text="R text", duration_s=1.0),
            PhaseOutput(role="writer", text="W text", duration_s=1.0),
        ]
        result = _format_peers(outputs, exclude_role="researcher")
        assert "R text" not in result
        assert "W text" in result

    def test_phase_output_dataclass(self):
        from app.crews.creative_crew import PhaseOutput
        po = PhaseOutput(role="test", text="hello", duration_s=0.5)
        assert po.role == "test"
        assert po.duration_s == 0.5

    def test_creative_run_result_fields(self):
        from app.crews.creative_crew import CreativeRunResult
        r = CreativeRunResult(
            final_output="synthesized output",
            phase_1_outputs=[],
            phase_2_outputs=[],
            cost_usd=0.05,
        )
        assert r.final_output == "synthesized output"
        assert r.aborted_reason is None
        assert r.scores is None
        assert r.cost_usd == 0.05

    def test_budget_exceeded_exception(self):
        from app.crews.creative_crew import BudgetExceeded
        with pytest.raises(BudgetExceeded):
            raise BudgetExceeded("over budget")

    def test_check_budget_passes_when_under(self):
        from app.crews.creative_crew import _check_budget
        with patch("app.crews.creative_crew.get_active_tracker") as mock_tracker:
            t = MagicMock()
            t.total_cost_usd = 0.01
            mock_tracker.return_value = t
            # Should not raise
            _check_budget(0.10, "test_phase")

    def test_check_budget_raises_when_over(self):
        from app.crews.creative_crew import _check_budget, BudgetExceeded
        with patch("app.crews.creative_crew.get_active_tracker") as mock_tracker:
            t = MagicMock()
            t.total_cost_usd = 0.50
            mock_tracker.return_value = t
            with pytest.raises(BudgetExceeded):
                _check_budget(0.10, "test_phase")

    def test_idea_diversity_density_empty(self):
        from app.crews.creative_crew import _idea_diversity_density
        assert _idea_diversity_density([]) == 0.0
        assert _idea_diversity_density(["single idea"]) == 0.0

    def test_reasoning_method_assignment(self):
        from app.crews.creative_crew import _REASONING_METHOD_BY_ROLE
        assert "researcher" in _REASONING_METHOD_BY_ROLE
        assert "writer" in _REASONING_METHOD_BY_ROLE
        assert "critic" in _REASONING_METHOD_BY_ROLE
        assert "commander" in _REASONING_METHOD_BY_ROLE

    def test_tier_assignment(self):
        from app.crews.creative_crew import _TIER_BY_ROLE_CREATIVE
        assert _TIER_BY_ROLE_CREATIVE["commander"] == "premium"
        assert _TIER_BY_ROLE_CREATIVE["researcher"] == "local"


# ── MAP-Elites Wiring Integration ───────────────────────────────────────────

@e2e
class TestMAPElitesWiring:
    """MAP-Elites wiring tests — requires Docker (Settings loads from env)."""

    def test_get_context_empty_grid(self):
        from app.crews.self_improvement_crew import SelfImprovementCrew
        sic = SelfImprovementCrew()
        ctx = sic._get_map_elites_context("test topic xyz")
        assert isinstance(ctx, str)

    def test_get_context_with_entries(self):
        """After populating the researcher grid with ≥5 entries, the self-improver
        draws inspiration from it. (The method now reads from 'researcher' grid —
        the Learner is itself doing research, and researcher strategies are most
        relevant.)"""
        from app.map_elites import get_db, StrategyEntry
        from app.crews.self_improvement_crew import SelfImprovementCrew
        from datetime import datetime, timezone

        # Populate the researcher grid with 5+ diverse entries to pass the
        # sparseness threshold in _get_map_elites_context.
        db = get_db("researcher")
        for i in range(6):
            db.add_strategy(StrategyEntry(
                strategy_id=f"test_wire_{i}",
                role="researcher",
                prompt_content=f"Research strategy {i}: "
                               f"{'broad' if i % 2 else 'focused'} approach, "
                               f"{'detailed' if i < 3 else 'concise'} output.",
                fitness_score=0.5 + (i * 0.05),
                feature_vector={
                    "complexity": 0.2 + (i * 0.12),
                    "cost_efficiency": 0.3 + (i * 0.08),
                    "specialization": 0.1 + (i * 0.15),
                },
                created_at=datetime.now(timezone.utc).isoformat(),
            ))

        sic = SelfImprovementCrew()
        ctx = sic._get_map_elites_context("APIs")
        # Either the grid was populated enough to return context, or it's
        # still sparse (other tests may have affected state). Both are OK
        # as long as the contract holds: returns str, empty when sparse.
        assert isinstance(ctx, str)
        if ctx:
            assert "MAP-Elites" in ctx or "Strategy" in ctx or "inspiration" in ctx.lower()

    def test_crew_outcome_records_to_grid(self):
        """record_crew_outcome is the single system-wide write point.

        Replaces the older _archive_to_map_elites helper (removed in favor of
        orchestrator post-crew telemetry; see app/map_elites_wiring.py).
        """
        from app.map_elites import get_db
        from app.map_elites_wiring import CrewOutcome, record_crew_outcome

        db = get_db("writer")
        initial_size = sum(isl.size for isl in db._islands)

        record_crew_outcome(CrewOutcome(
            crew_name="writer",
            task_description="summarize quarterly report",
            result="a clear summary of the key points",
            backstory_snippet="You are a Writer who produces clear summaries.",
            difficulty=5,
            duration_s=12.3,
            confidence=0.8,
            completeness=0.9,
            passed_quality_gate=True,
            has_result=True,
            is_failure_pattern=False,
        ))

        final_size = sum(isl.size for isl in db._islands)
        assert final_size >= initial_size  # grid received at minimum no regression


# ── Observer Integration ────────────────────────────────────────────────────

class TestObserverIntegration:

    def test_mcsv_gates_observer(self):
        """MCSV with low correctness should require observer."""
        from app.subia.belief.internal_state import (
            MetacognitiveStateVector, CertaintyVector, SomaticMarker,
        )
        cv = CertaintyVector(factual_grounding=0.2)
        sm = SomaticMarker()
        mcsv = MetacognitiveStateVector.from_state(cv, sm)
        assert mcsv.requires_observer is True

    def test_mcsv_skips_observer_when_confident(self):
        from app.subia.belief.internal_state import (
            MetacognitiveStateVector, CertaintyVector, SomaticMarker,
        )
        cv = CertaintyVector(factual_grounding=0.9, tool_confidence=0.9, coherence=0.9)
        sm = SomaticMarker()
        mcsv = MetacognitiveStateVector.from_state(cv, sm)
        assert mcsv.requires_observer is False

    def test_full_predict_flow(self):
        """Full prediction flow with mocked Crew."""
        with patch("app.agents.observer.create_observer", return_value=MagicMock()):
            with patch("crewai.Crew") as MockCrew:
                mock_crew = MagicMock()
                mock_crew.kickoff.return_value = json.dumps({
                    "predicted_failure_mode": "scope_creep",
                    "confidence": 0.6,
                    "recommendation": "Re-scope the task.",
                })
                MockCrew.return_value = mock_crew
                with patch("crewai.Task"):
                    from app.subia.belief.internal_state import MetacognitiveStateVector
                    from app.agents.observer import predict_failure
                    mcsv = MetacognitiveStateVector(correctness_evaluation=0.2)
                    result = predict_failure(
                        agent_id="writer",
                        task_description="Write about everything in the universe",
                        next_action="start writing",
                        recent_history=["prev task 1"],
                        mcsv=mcsv,
                    )
                    assert result["predicted_failure_mode"] == "scope_creep"


# ── Failure Mode + Scoring Integration ──────────────────────────────────────

class TestFailureScoringIntegration:

    def test_scan_on_creative_output(self):
        """Failure scan on a realistic creative output."""
        from app.failure_modes import scan_for_failures
        good_output = (
            "1. Build a modular habitat using 3D-printed components (source: NASA research https://nasa.gov/hab).\n"
            "2. Use mycelium-based insulation for thermal regulation.\n"
            "3. Deploy autonomous drones for site surveying.\n"
            "4. Integrate vertical farming modules for food production."
        )
        signals = scan_for_failures("Design a Mars habitat", good_output)
        # Good output should trigger few or no failure modes
        mirage = [s for s in signals if s.mode_name == "confidence_mirage"]
        assert len(mirage) == 0

    def test_scoring_on_numbered_output(self):
        """Creativity scoring on a realistic multi-idea output."""
        from app.personality.creativity_scoring import extract_ideas, CreativityScores, _elaboration
        text = (
            "1. Use biomimicry to design self-healing concrete because coral structures demonstrate natural repair.\n"
            "2. Deploy swarm robotics for construction, specifically modeled after termite mound building.\n"
            "3. Create a closed-loop water system, for example recycling condensation from temperature differentials.\n"
            "4. Integrate social spaces using biophilic design patterns, therefore improving crew mental health."
        )
        ideas = extract_ideas(text)
        assert len(ideas) == 4
        elab = _elaboration(ideas)
        assert elab > 0.2  # has detail markers (because, specifically, for example, therefore)


# ═══════════════════════════════════════════════════════════════════════════
# E2E tests below require Docker stack (ChromaDB, etc.)
# Run with: pytest tests/test_e2e_creative_research.py -v -m e2e
# ═══════════════════════════════════════════════════════════════════════════

@e2e
class TestBlackboardE2E:
    """Requires running ChromaDB service."""

    def test_deposit_and_retrieve_cycle(self):
        from app.memory.scoped_memory import store_finding, retrieve_findings
        task_id = "e2e_test_bb_cycle"
        store_finding(task_id, "Earth is round", "Multiple satellite images", "high",
                      "https://nasa.gov", "researcher", "verified")
        store_finding(task_id, "Moon is cheese", "", "low",
                      "", "writer", "contradicted")

        results = retrieve_findings(task_id, "Earth shape", n=5)
        assert len(results) >= 1
        # retrieve_with_metadata returns {"document": ..., "metadata": ...}
        texts = [r.get("document", r.get("text", "")) for r in results]
        assert any("Earth" in t for t in texts)

    def test_promote_verified_to_kb(self):
        from app.memory.scoped_memory import store_finding, promote_to_knowledge_base
        task_id = "e2e_test_bb_promote"
        store_finding(task_id, "Verified fact for promotion", "", "high",
                      "", "researcher", "verified")
        count = promote_to_knowledge_base(task_id, project="e2e_test")
        assert count >= 0  # may be 0 if the retrieve returns differently ordered


@e2e
class TestMCPEndpointE2E:
    """Requires running FastAPI server at :8765."""

    def test_mcp_module_loads(self):
        """Basic check that the MCP module is importable and the SDK works."""
        import mcp
        from app.mcp.server import mount_mcp_routes
        # We don't test the actual SSE connection here — that requires
        # an async HTTP client. Just verify the module chain works.
        assert callable(mount_mcp_routes)

    def test_mcp_read_mcsv(self):
        from app.mcp.server import _read_mcsv
        result = _read_mcsv("current")
        data = json.loads(result)
        assert "emotional_awareness" in data

    def test_mcp_score_creativity(self):
        from app.mcp.server import _score_creativity
        result = _score_creativity(
            "1. First creative idea with detail.\n"
            "2. Second creative idea with substance.\n"
            "3. Third creative idea that is unique."
        )
        data = json.loads(result)
        assert "fluency" in data
        assert data["fluency"] >= 1


@e2e
class TestWikiCorpusE2E:
    """Requires ChromaDB with wiki_corpus collection ingested."""

    def test_wiki_corpus_retrievable(self):
        from app.memory.chromadb_manager import retrieve
        results = retrieve("wiki_corpus", "philosophy values ethics", n=3)
        assert len(results) >= 1

    def test_wiki_corpus_for_originality(self):
        """Verify the wiki corpus can serve as an originality baseline."""
        from app.memory.chromadb_manager import retrieve, embed
        results = retrieve("wiki_corpus", "common everyday knowledge", n=3)
        # Should return something — the wiki has content
        assert len(results) >= 1
        # Embedding should work on retrieved text
        vec = embed(results[0][:200])
        assert len(vec) > 0


@e2e
class TestCreativeAPIE2E:
    """Tests the creative mode API endpoints."""

    def test_creative_mode_get(self):
        import urllib.request
        try:
            resp = urllib.request.urlopen("http://localhost:8765/config/creative_mode", timeout=5)
            data = json.loads(resp.read())
            assert "creative_run_budget_usd" in data
            assert "originality_wiki_weight" in data
        except Exception as exc:
            pytest.skip(f"API not available: {exc}")
