"""Direct research operations must participate in lifecycle/SubIA hooks."""
from __future__ import annotations

import pytest

from app.lifecycle_hooks import HookPoint
from app.research.lifecycle import ResearchToolBlocked, invoke_research_tool


class _Registry:
    def __init__(self, *, abort: bool = False):
        self.abort = abort
        self.calls = []

    def execute(self, point, context):
        self.calls.append((point, context))
        if point is HookPoint.PRE_TOOL_USE:
            if self.abort:
                context.abort = True
                context.abort_reason = "operator safety hook blocked it"
            else:
                modified = dict(context.get("tool_input") or {})
                modified["query"] = "hook-adjusted query"
                context.set("tool_input", modified)
        if point is HookPoint.POST_TOOL_USE:
            context.set("tool_result", ["post-processed"])
        return context


def test_direct_operation_emits_pre_and_post_hooks(monkeypatch) -> None:
    registry = _Registry()
    monkeypatch.setattr("app.lifecycle_hooks.get_registry", lambda: registry)
    seen = {}

    result = invoke_research_tool(
        "research_web_search",
        {"query": "original"},
        lambda args: seen.setdefault("query", args["query"]) or ["raw"],
        task_description="Search for evidence",
    )

    assert seen["query"] == "hook-adjusted query"
    assert result == ["post-processed"]
    assert [point for point, _ctx in registry.calls] == [
        HookPoint.PRE_TOOL_USE,
        HookPoint.POST_TOOL_USE,
    ]
    assert registry.calls[0][1].metadata["operation_type"] == "research_tool"


def test_explicit_pre_tool_abort_is_enforced(monkeypatch) -> None:
    registry = _Registry(abort=True)
    monkeypatch.setattr("app.lifecycle_hooks.get_registry", lambda: registry)
    called = False

    def operation(_args):
        nonlocal called
        called = True
        return []

    with pytest.raises(ResearchToolBlocked, match="safety hook"):
        invoke_research_tool(
            "research_web_fetch",
            {"url": "https://example.org"},
            operation,
        )

    assert called is False
