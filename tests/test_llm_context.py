"""
LLM Context Propagation Tests
==============================

Tests the ``app.llm_context`` scope/current helpers — the core of Phase 1.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_context.py -v
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestScope:
    def test_current_none_when_no_scope(self):
        from app.llm_context import current
        assert current() is None

    def test_scope_sets_and_restores(self):
        from app.llm_context import scope, current

        assert current() is None
        with scope(crew_name="coding", role="coding", task_type="coding") as ctx:
            assert current() is ctx
            assert ctx.crew_name == "coding"
            assert ctx.role == "coding"
            assert ctx.task_type == "coding"
            assert isinstance(ctx.started_at, float)
            assert ctx.started_at > 0
        assert current() is None

    def test_nested_scopes_restore_outer(self):
        from app.llm_context import scope, current

        with scope(crew_name="outer", role="outer", task_type="research"):
            outer = current()
            with scope(crew_name="inner", role="inner", task_type="coding"):
                assert current().task_type == "coding"
                assert current().crew_name == "inner"
            assert current() is outer
            assert current().task_type == "research"

    def test_default_task_type_on_empty(self):
        from app.llm_context import scope, current

        with scope(crew_name="", role="", task_type=""):
            assert current().task_type == "general"

    def test_started_at_advances_with_time(self):
        from app.llm_context import scope

        with scope(crew_name="x", role="x", task_type="coding") as ctx1:
            t1 = ctx1.started_at
        time.sleep(0.01)
        with scope(crew_name="x", role="x", task_type="coding") as ctx2:
            assert ctx2.started_at > t1


class TestCurrentTaskType:
    def test_returns_default_when_unset(self):
        from app.llm_context import current_task_type
        assert current_task_type() == "general"
        assert current_task_type("coding") == "coding"

    def test_returns_active_task_type(self):
        from app.llm_context import scope, current_task_type

        with scope(crew_name="c", role="c", task_type="writing"):
            assert current_task_type() == "writing"
            assert current_task_type("coding") == "writing"


class TestContextVarPropagation:
    def test_asyncio_propagation(self):
        """ContextVars propagate through asyncio by default."""
        import asyncio
        from app.llm_context import scope, current_task_type

        async def _inner():
            return current_task_type()

        async def _outer():
            with scope(crew_name="c", role="c", task_type="coding"):
                return await _inner()

        assert asyncio.run(_outer()) == "coding"

    def test_threadpool_without_copy_loses_context(self):
        """Plain submit drops ContextVars — documents the edge case."""
        from app.llm_context import scope, current_task_type

        with ThreadPoolExecutor(max_workers=1) as ex:
            with scope(crew_name="c", role="c", task_type="coding"):
                fut = ex.submit(current_task_type)
                # In worker thread there is no context → default wins.
                assert fut.result() == "general"

    def test_run_in_context_copies_across_threads(self):
        """run_in_context returns a bound callable that carries the
        caller's context into a worker thread."""
        from app.llm_context import scope, current_task_type, run_in_context

        with ThreadPoolExecutor(max_workers=1) as ex:
            with scope(crew_name="c", role="c", task_type="coding"):
                bound = run_in_context(current_task_type)
            # Scope has exited; the bound callable still remembers it.
            fut = ex.submit(bound)
            assert fut.result() == "coding"

    def test_scopes_isolated_per_thread(self):
        """Two threads each enter their own scope without bleeding."""
        from app.llm_context import scope, current_task_type

        observations: dict[str, str] = {}
        ready = threading.Event()

        def worker(name: str, task: str):
            with scope(crew_name=name, role=name, task_type=task):
                ready.wait()
                observations[name] = current_task_type()

        t1 = threading.Thread(target=worker, args=("a", "coding"))
        t2 = threading.Thread(target=worker, args=("b", "writing"))
        t1.start(); t2.start()
        ready.set()
        t1.join(); t2.join()
        assert observations == {"a": "coding", "b": "writing"}


class TestCanonicalTaskType:
    def test_canonical_keys_complete(self):
        """Every canonical key must correspond to a strength column in
        at least one catalog entry. Prevents typos drifting the API."""
        from app.llm_catalog import CATALOG, CANONICAL_TASK_TYPES

        seen = set()
        for info in CATALOG.values():
            seen.update(info.get("strengths", {}).keys())
        # "routing", "synthesis", "media" may exist in strengths but are
        # not canonical task types — we're only checking the reverse.
        for key in CANONICAL_TASK_TYPES:
            assert key in seen, f"canonical task_type {key!r} missing from all CATALOG strengths"

    @pytest.mark.parametrize("role,expected", [
        ("coding", "coding"),
        ("research", "research"),
        ("writing", "writing"),
        ("media", "multimodal"),
        ("critic", "reasoning"),
        ("self_improve", "research"),
        ("vetting", "vetting"),
        ("synthesis", "writing"),
        ("planner", "architecture"),
        ("default", "general"),
        ("unknown_role", "general"),
    ])
    def test_role_mapping(self, role: str, expected: str):
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(role=role) == expected

    @pytest.mark.parametrize("hint,expected", [
        ("please debug the traceback", "debugging"),
        ("implement a merge_intervals function", "coding"),
        ("write release notes", "writing"),
        ("research consensus algorithms", "research"),
        ("analyze the screenshot", "multimodal"),
        ("reason about the proof", "reasoning"),
    ])
    def test_hint_keyword_detection(self, hint: str, expected: str):
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(task_hint=hint) == expected

    def test_hint_beats_role(self):
        """task_hint is more specific than role."""
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(role="writing", task_hint="debug the traceback") == "debugging"

    def test_crew_name_used_when_role_missing(self):
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(crew_name="repo_analysis") == "architecture"

    def test_fallback_on_empty(self):
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type() == "general"
