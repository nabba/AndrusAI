"""
LLM Telemetry Feedback Loop Tests
===================================

Verifies that ``app.rate_throttle`` records the *real* task_type,
latency, and success/failure signals into the benchmarks table so
that ``app.llm_benchmarks.get_scores`` produces a meaningful score
for the selector to use.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_telemetry.py -v
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def isolated_benchmarks_db(monkeypatch, tmp_path):
    """Point llm_benchmarks at a throwaway SQLite file and reset the
    per-thread connection + write buffer so each test starts clean."""
    import app.llm_benchmarks as bm

    db_file = tmp_path / "benchmarks.db"
    monkeypatch.setattr(bm, "DB_PATH", db_file)
    bm._local = type(bm._local)()  # fresh threading.local
    with bm._write_lock:
        bm._write_buffer.clear()
        bm._last_flush = 0.0
    yield bm


class _FakeUsage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeResponse:
    def __init__(self, model: str, prompt: int = 80, completion: int = 120) -> None:
        self.model = model
        self.usage = _FakeUsage(prompt, completion)


class TestRecordTokenUsage:
    def test_success_uses_context_task_type_and_latency(self, isolated_benchmarks_db):
        from app.rate_throttle import _record_token_usage, _benchmark_recorded
        from app.llm_context import scope
        bm = isolated_benchmarks_db

        # get_scores requires runs >= 2 per model for confidence, so
        # write two independent records inside the coding scope.
        for latency in (4321, 4800):
            with scope(crew_name="coding", role="coding", task_type="coding"):
                _benchmark_recorded.set(False)
                _record_token_usage(
                    _FakeResponse("openrouter/deepseek/deepseek-chat"),
                    kwargs={}, latency_ms=latency,
                )

        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT task_type, latency_ms, success FROM benchmarks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("coding", 4800, 1)

        scores = bm.get_scores("coding")
        assert scores, "expected at least one coding score"
        assert bm.get_scores("general") == {}

    def test_guard_prevents_double_record(self, isolated_benchmarks_db):
        """If the guard is already set, a second write is suppressed."""
        from app.rate_throttle import _record_token_usage, _benchmark_recorded
        from app.llm_context import scope
        bm = isolated_benchmarks_db

        with scope(crew_name="writing", role="writing", task_type="writing"):
            _benchmark_recorded.set(False)
            _record_token_usage(_FakeResponse("model-a"), kwargs={}, latency_ms=100)
            # Second invocation for same call should be suppressed
            _record_token_usage(_FakeResponse("model-a"), kwargs={}, latency_ms=100)

        bm._flush_writes()
        conn = bm._get_conn()
        rows = conn.execute(
            "SELECT COUNT(*) FROM benchmarks WHERE task_type='writing'"
        ).fetchone()
        assert rows[0] == 1, f"expected exactly one benchmark row, got {rows[0]}"

    def test_failure_records_success_false(self, isolated_benchmarks_db):
        from app.rate_throttle import _record_benchmark_failure
        from app.llm_context import scope
        bm = isolated_benchmarks_db

        with scope(crew_name="coding", role="coding", task_type="coding"):
            _record_benchmark_failure("openrouter/some-model", latency_ms=5000)

        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT success, latency_ms, task_type FROM benchmarks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == 0   # success=False
        assert row[1] == 5000
        assert row[2] == "coding"

    def test_no_context_falls_back_to_general(self, isolated_benchmarks_db):
        from app.rate_throttle import _record_token_usage, _benchmark_recorded
        bm = isolated_benchmarks_db

        _benchmark_recorded.set(False)
        _record_token_usage(_FakeResponse("fallback-model"), kwargs={}, latency_ms=200)

        bm._flush_writes()
        conn = bm._get_conn()
        row = conn.execute(
            "SELECT task_type FROM benchmarks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "general"


class TestGetScoresSignal:
    def test_mixed_success_and_latency_produces_varied_score(self, isolated_benchmarks_db):
        """Prior to the fix, every row was success=True, latency=0 →
        get_scores returned identical scores for every model. After the
        fix, distinct success/latency values produce distinct scores."""
        bm = isolated_benchmarks_db

        # Model A: fast + reliable
        for _ in range(4):
            bm.record("model-a", "coding", True, latency_ms=500, tokens=100)
        # Model B: slow + flaky
        for _ in range(3):
            bm.record("model-b", "coding", True, latency_ms=30000, tokens=100)
        bm.record("model-b", "coding", False, latency_ms=90000, tokens=0)
        bm._flush_writes()

        scores = bm.get_scores("coding")
        assert "model-a" in scores
        assert "model-b" in scores
        assert scores["model-a"] > scores["model-b"], (
            f"fast+reliable model should outscore slow+flaky: {scores}"
        )
