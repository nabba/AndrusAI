"""Tests for app/control_plane/crew_task_spans.py and span_events.py.

Exercises the persistence helpers + ContextVar plumbing + tree-builder
response shape. DB-integration tests require the dev Postgres and are
skipped when DB calls fail — this file focuses on the pure-Python
pieces that can run anywhere.
"""
import unittest
from unittest.mock import MagicMock, patch


class TestTruncateDetail(unittest.TestCase):
    """The detail-payload budget caps pathological inputs."""

    def test_empty_returns_empty(self):
        from app.control_plane.crew_task_spans import _truncate_detail
        assert _truncate_detail(None) == {}
        assert _truncate_detail({}) == {}

    def test_small_payload_passes_through(self):
        from app.control_plane.crew_task_spans import _truncate_detail
        payload = {"tool": "WebSearch", "query": "opus 4.8"}
        out = _truncate_detail(payload)
        assert out == payload

    def test_large_string_value_gets_truncated(self):
        from app.control_plane.crew_task_spans import (
            _truncate_detail, _DETAIL_BUDGET_BYTES,
        )
        huge = "x" * (_DETAIL_BUDGET_BYTES * 2)
        out = _truncate_detail({"output": huge, "tool": "WebSearch"})
        assert "tool" in out
        assert isinstance(out["output"], str)
        # Long strings get truncated to ~256 chars plus marker.
        assert len(out["output"]) < 400

    def test_unserialisable_payload_returns_marker(self):
        from app.control_plane.crew_task_spans import _truncate_detail

        class _Unserialisable:
            def __repr__(self):
                raise RuntimeError("can't repr")

        # Any object json.dumps can't handle with default=str is rare,
        # but the error path still produces a dict.
        out = _truncate_detail({"weird": _Unserialisable()})
        assert isinstance(out, dict)


class TestSpanEventsContextVar(unittest.TestCase):
    """ContextVar-based correlation of CrewAI events to our task id."""

    def test_current_crew_task_id_defaults_none(self):
        from app.crews.span_events import _current_crew_task_id
        # Outside any crew context, ContextVar returns its default.
        assert _current_crew_task_id.get() is None

    def test_set_and_clear_restores_previous_value(self):
        from app.crews.span_events import (
            set_current_crew_task_id, clear_current_crew_task_id,
            _current_crew_task_id,
        )
        token = set_current_crew_task_id("task-A")
        assert _current_crew_task_id.get() == "task-A"
        clear_current_crew_task_id(token)
        assert _current_crew_task_id.get() is None

    def test_nested_set_stacks_correctly(self):
        """Nested crew dispatches must restore the parent task id on exit."""
        from app.crews.span_events import (
            set_current_crew_task_id, clear_current_crew_task_id,
            _current_crew_task_id,
        )
        outer = set_current_crew_task_id("parent")
        inner = set_current_crew_task_id("child")
        assert _current_crew_task_id.get() == "child"
        clear_current_crew_task_id(inner)
        assert _current_crew_task_id.get() == "parent"
        clear_current_crew_task_id(outer)
        assert _current_crew_task_id.get() is None


class TestSpanEventsMap(unittest.TestCase):
    """CrewAI event_id → span_id mapping roundtrip."""

    def test_remember_and_pop(self):
        from app.crews.span_events import _remember_span, _pop_span
        _remember_span("evt-1", 42, "task-A")
        assert _pop_span("evt-1") == 42
        # Second pop returns None (already consumed)
        assert _pop_span("evt-1") is None

    def test_peek_does_not_consume(self):
        from app.crews.span_events import _remember_span, _peek_span, _pop_span
        _remember_span("evt-2", 99, "task-B")
        assert _peek_span("evt-2") == 99
        assert _peek_span("evt-2") == 99  # still there
        # pop cleans up
        assert _pop_span("evt-2") == 99
        assert _peek_span("evt-2") is None

    def test_peek_none_safe(self):
        from app.crews.span_events import _peek_span
        assert _peek_span(None) is None
        assert _peek_span("never-existed") is None


class TestParentSpanResolution(unittest.TestCase):
    """_parent_span_id walks the event parent chain."""

    def test_no_parent_event_id_returns_none(self):
        from app.crews.span_events import _parent_span_id
        event = MagicMock(parent_event_id=None)
        assert _parent_span_id(event) is None

    def test_unknown_parent_event_id_returns_none(self):
        from app.crews.span_events import _parent_span_id
        event = MagicMock(parent_event_id="unknown-evt")
        assert _parent_span_id(event) is None

    def test_known_parent_resolves_to_span(self):
        from app.crews.span_events import _remember_span, _pop_span, _parent_span_id
        _remember_span("parent-evt", 17, "task-X")
        event = MagicMock(parent_event_id="parent-evt")
        assert _parent_span_id(event) == 17
        _pop_span("parent-evt")  # cleanup


class TestSafeStart(unittest.TestCase):
    """_safe_start is a no-op when there's no current crew task."""

    def test_no_task_context_is_noop(self):
        from app.crews.span_events import _safe_start
        with patch("app.crews.span_events.crew_task_spans") as cts:
            _safe_start(
                span_type="agent", name="Researcher",
                event=MagicMock(event_id="evt-x", parent_event_id=None),
            )
            cts.start_span.assert_not_called()

    def test_with_task_context_calls_persistence(self):
        from app.crews.span_events import (
            _safe_start, set_current_crew_task_id, clear_current_crew_task_id,
        )
        token = set_current_crew_task_id("task-Y")
        try:
            with patch("app.crews.span_events.crew_task_spans") as cts:
                cts.start_span.return_value = 123
                _safe_start(
                    span_type="tool", name="WebSearch",
                    event=MagicMock(event_id="evt-w", parent_event_id=None),
                    detail={"query": "test"},
                )
                cts.start_span.assert_called_once()
                kwargs = cts.start_span.call_args.kwargs
                assert kwargs["task_id"] == "task-Y"
                assert kwargs["span_type"] == "tool"
                assert kwargs["name"] == "WebSearch"
                assert kwargs["crewai_event_id"] == "evt-w"
        finally:
            clear_current_crew_task_id(token)


class TestInstallListenersIdempotent(unittest.TestCase):
    """Calling install_listeners() twice should only register once."""

    def test_idempotent(self):
        from app.crews import span_events
        # Force a clean state (test isolation).
        span_events._listeners_installed = False
        span_events.install_listeners()
        assert span_events._listeners_installed is True
        # Second call is a no-op — doesn't raise.
        span_events.install_listeners()
        assert span_events._listeners_installed is True


if __name__ == "__main__":
    unittest.main()
