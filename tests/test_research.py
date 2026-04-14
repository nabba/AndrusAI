"""
test_research.py — Unit tests for the research synthesis features.

Tests modules that need mocked external services (ChromaDB, Mem0, LLM).
Tests: blackboard (scoped_memory), blackboard_tool, blend_tool, observer,
       mcp_server helpers, llm_factory phase threading.

Run: pytest tests/test_research.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Blackboard (scoped_memory) ──────────────────────────────────────────────

class TestBlackboard:

    @patch("app.memory.scoped_memory.store")
    def test_store_finding_correct_scope(self, mock_store):
        from app.memory.scoped_memory import store_finding
        store_finding("task123", "Helsinki is at 60N", "Wikipedia", "high")
        mock_store.assert_called_once()
        call_args = mock_store.call_args
        scope = call_args[0][0]
        assert scope == "scope_research_bb--task123"

    @patch("app.memory.scoped_memory.store")
    def test_store_finding_metadata(self, mock_store):
        from app.memory.scoped_memory import store_finding
        store_finding("t1", "claim", "evidence", "medium", "url", "researcher", "unverified")
        meta = mock_store.call_args[0][2]
        assert meta["confidence"] == "medium"
        assert meta["agent"] == "researcher"
        assert meta["verification_status"] == "unverified"
        assert meta["task_id"] == "t1"

    @patch("app.memory.scoped_memory.retrieve_with_metadata")
    def test_retrieve_findings_calls_correct_scope(self, mock_retrieve):
        mock_retrieve.return_value = [{"text": "finding1", "metadata": {"confidence": "high"}}]
        from app.memory.scoped_memory import retrieve_findings
        results = retrieve_findings("task99", "Helsinki")
        mock_retrieve.assert_called_once()
        scope_arg = mock_retrieve.call_args[0][0]
        assert "task99" in scope_arg

    @patch("app.memory.scoped_memory.retrieve_filtered")
    def test_retrieve_findings_with_confidence_filter(self, mock_filtered):
        mock_filtered.return_value = [{"text": "f1", "metadata": {"confidence": "high"}}]
        from app.memory.scoped_memory import retrieve_findings
        results = retrieve_findings("t1", "query", confidence_filter="high")
        mock_filtered.assert_called_once()
        assert mock_filtered.call_args[1]["where"] == {"confidence": "high"}

    @patch("app.memory.scoped_memory.store_project_memory")
    @patch("app.memory.scoped_memory.retrieve_findings")
    def test_promote_to_knowledge_base(self, mock_retrieve, mock_store_project):
        mock_retrieve.return_value = [
            {"text": "verified claim", "metadata": {"confidence": "high", "verification_status": "verified"}},
            {"text": "unverified claim", "metadata": {"confidence": "high", "verification_status": "unverified"}},
        ]
        from app.memory.scoped_memory import promote_to_knowledge_base
        count = promote_to_knowledge_base("t1", project="test")
        assert count == 1  # only the verified one
        mock_store_project.assert_called_once_with("test", "verified claim", importance="high")


# ── Blackboard Tools ────────────────────────────────────────────────────────

class TestBlackboardTools:

    def test_create_returns_two_tools(self):
        from app.tools.blackboard_tool import create_blackboard_tools
        tools = create_blackboard_tools("task1", "researcher")
        assert len(tools) == 2
        assert tools[0].name == "deposit_finding"
        assert tools[1].name == "read_findings"

    def test_tools_scoped_to_task(self):
        from app.tools.blackboard_tool import create_blackboard_tools
        tools = create_blackboard_tools("my_task", "writer")
        assert tools[0].task_id == "my_task"
        assert tools[0].agent_name == "writer"
        assert tools[1].task_id == "my_task"

    @patch("app.memory.scoped_memory.store_finding")
    def test_deposit_calls_store(self, mock_store):
        from app.tools.blackboard_tool import DepositFindingTool
        tool = DepositFindingTool()
        tool.task_id = "t1"
        tool.agent_name = "researcher"
        result = tool._run(claim="X is true", evidence="source Y", confidence="high")
        mock_store.assert_called_once()
        assert "deposited" in result.lower()

    @patch("app.memory.scoped_memory.retrieve_findings")
    def test_read_formats_findings(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"text": "Claim A", "metadata": {"confidence": "high", "verification_status": "verified", "agent": "r"}},
            {"text": "Claim B", "metadata": {"confidence": "low", "verification_status": "unverified", "agent": "w"}},
        ]
        from app.tools.blackboard_tool import ReadFindingsTool
        tool = ReadFindingsTool()
        tool.task_id = "t1"
        result = tool._run(query="claims")
        assert "Claim A" in result
        assert "Claim B" in result
        assert "high" in result
        assert "Finding 1" in result

    @patch("app.memory.scoped_memory.retrieve_findings")
    def test_read_empty_returns_message(self, mock_retrieve):
        mock_retrieve.return_value = []
        from app.tools.blackboard_tool import ReadFindingsTool
        tool = ReadFindingsTool()
        tool.task_id = "t1"
        result = tool._run(query="nothing")
        assert "No findings" in result


# ── Blend Tool ──────────────────────────────────────────────────────────────

class TestBlendTool:

    @patch("app.tools.blend_tool._retrieve_fiction")
    @patch("app.tools.blend_tool._retrieve_philosophy")
    def test_blend_contains_both_concepts(self, mock_phil, mock_fic):
        mock_phil.return_value = "Aristotle on phronesis: practical wisdom..."
        mock_fic.return_value = "Kintsugi: the art of golden repair..."
        from app.tools.blend_tool import ConceptBlendTool
        tool = ConceptBlendTool()
        result = tool._run(concept_a="Aristotelian phronesis", concept_b="kintsugi repair")
        assert "Aristotelian phronesis" in result
        assert "kintsugi repair" in result
        assert "Structural mapping" in result
        assert "[PIT]" in result or "[PIH]" in result

    @patch("app.tools.blend_tool._retrieve_fiction")
    @patch("app.tools.blend_tool._retrieve_philosophy")
    def test_blend_graceful_empty_fiction(self, mock_phil, mock_fic):
        mock_phil.return_value = "Philosophy passage."
        mock_fic.return_value = "(fiction module unavailable — use concept B as-stated: test)"
        from app.tools.blend_tool import ConceptBlendTool
        tool = ConceptBlendTool()
        result = tool._run(concept_a="test A", concept_b="test B")
        assert "test A" in result  # still produces output


# ── Observer ────────────────────────────────────────────────────────────────

class TestObserver:

    def _run_predict(self, kickoff_return=None, crew_side_effect=None):
        """Helper: run predict_failure with mocked Crew + observer creation."""
        with patch("app.agents.observer.create_observer", return_value=MagicMock()):
            # Crew is imported locally inside predict_failure via `from crewai import ...`
            # so we patch it at the crewai module level
            with patch("crewai.Crew") as MockCrew:
                if crew_side_effect:
                    MockCrew.side_effect = crew_side_effect
                else:
                    mock_instance = MagicMock()
                    mock_instance.kickoff.return_value = kickoff_return
                    MockCrew.return_value = mock_instance
                # Also patch Task to avoid validation
                with patch("crewai.Task"):
                    from app.agents.observer import predict_failure
                    return predict_failure("r", "task desc", "next action", ["hist"])

    def test_predict_failure_parses_json(self):
        result = self._run_predict(kickoff_return=json.dumps({
            "predicted_failure_mode": "confidence_mirage",
            "confidence": 0.85,
            "recommendation": "Add source verification.",
        }))
        assert result["predicted_failure_mode"] == "confidence_mirage"
        assert result["confidence"] == 0.85

    def test_predict_failure_handles_garbage(self):
        result = self._run_predict(kickoff_return="this is not json at all {{")
        assert result["predicted_failure_mode"] is None
        assert result["confidence"] == 0.0

    def test_predict_failure_handles_exception(self):
        """When crew.kickoff() raises, predict_failure returns safe default."""
        # Make Crew() succeed but kickoff() raise
        def kickoff_raises(**kw):
            m = MagicMock()
            m.kickoff.side_effect = RuntimeError("LLM unavailable")
            return m
        result = self._run_predict(crew_side_effect=kickoff_raises)
        assert result["predicted_failure_mode"] is None


# ── MCP Server Helpers ──────────────────────────────────────────────────────

class TestMCPHelpers:

    def test_read_mcsv_returns_valid_json(self):
        from app.mcp_server import _read_mcsv
        result = _read_mcsv("")
        data = json.loads(result)
        assert "emotional_awareness" in data
        assert "requires_observer" in data

    @patch("app.personality.creativity_scoring.score_output")
    def test_score_creativity_returns_json(self, mock_score):
        from app.personality.creativity_scoring import CreativityScores
        mock_score.return_value = CreativityScores(
            fluency=3, flexibility=2, originality=0.7, elaboration=0.5,
        )
        from app.mcp_server import _score_creativity
        result = _score_creativity("test text with multiple ideas")
        data = json.loads(result)
        assert data["fluency"] == 3
        assert data["originality"] == 0.7

    @patch("app.memory.scoped_memory.retrieve_findings")
    def test_read_blackboard_formats(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"text": "Finding A", "metadata": {"confidence": "high", "verification_status": "verified", "agent": "r"}},
        ]
        from app.mcp_server import _read_blackboard
        result = _read_blackboard("task1")
        assert "Finding A" in result
        assert "high" in result

    @patch("app.memory.scoped_memory.retrieve_findings")
    def test_read_blackboard_empty(self, mock_retrieve):
        mock_retrieve.return_value = []
        from app.mcp_server import _read_blackboard
        result = _read_blackboard("empty_task")
        assert "No findings" in result


# ── LLM Factory Phase Threading ─────────────────────────────────────────────

class TestLLMFactoryPhase:

    def test_sampling_helper_none_phase(self):
        from app.llm_factory import _sampling
        kwargs, key = _sampling(None, "anthropic")
        assert kwargs == {}
        assert key == ""

    def test_sampling_helper_diverge(self):
        from app.llm_factory import _sampling
        kwargs, key = _sampling("diverge", "anthropic")
        assert "temperature" in kwargs
        assert kwargs["temperature"] == 1.3
        assert key == "anthropic:diverge"

    def test_sampling_helper_ollama(self):
        from app.llm_factory import _sampling
        kwargs, key = _sampling("discuss", "ollama")
        assert "extra_body" in kwargs
        assert key == "ollama:discuss"

    @patch("app.llm_factory._get_LLM_class")
    def test_cached_llm_sampling_key_in_cache(self, mock_llm_cls):
        """Verify that different sampling_keys produce different cache entries."""
        # Return a class that creates distinct instances each time
        mock_cls = MagicMock(side_effect=lambda **kw: MagicMock(**kw))
        mock_llm_cls.return_value = mock_cls
        from app.llm_factory import _cached_llm
        a = _cached_llm("test/sampling-key-b", max_tokens=128,
                         sampling_key="", api_key="test-sk2")
        b = _cached_llm("test/sampling-key-b", max_tokens=128,
                         sampling_key="anthropic:diverge", api_key="test-sk2",
                         temperature=1.3, top_p=0.95)
        assert a is not b  # different cache entries

    @patch("app.llm_factory._get_LLM_class")
    def test_cached_llm_legacy_identity(self, mock_llm_cls):
        """phase=None should produce identical cache hits as legacy calls."""
        mock_cls = MagicMock(side_effect=lambda **kw: MagicMock(**kw))
        mock_llm_cls.return_value = mock_cls
        from app.llm_factory import _cached_llm
        a = _cached_llm("test/legacy-id-test2", max_tokens=128,
                         sampling_key="", api_key="test-lid2")
        b = _cached_llm("test/legacy-id-test2", max_tokens=128,
                         sampling_key="", api_key="test-lid2")
        assert a is b  # same cache entry (second call hits cache)
