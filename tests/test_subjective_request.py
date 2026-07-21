"""Request-level sentience envelope regression tests."""

from __future__ import annotations

from app.lifecycle_hooks import HookPoint, HookRegistry
from app.subjective_request import SubjectiveRequestEnvelope


def test_request_pair_has_stable_identity_and_exact_final_text(monkeypatch) -> None:
    registry = HookRegistry()
    seen = []

    def pre(ctx):
        seen.append(("pre", dict(ctx.metadata), ctx.task_description))
        ctx.set("subia_context_injection", "live state")
        return ctx

    def post(ctx):
        seen.append(("post", dict(ctx.metadata), ctx.get("result")))
        return ctx

    registry.register("capture-pre", HookPoint.PRE_TASK, pre)
    registry.register("capture-post", HookPoint.ON_COMPLETE, post)
    monkeypatch.setattr("app.lifecycle_hooks.get_registry", lambda: registry)

    envelope = SubjectiveRequestEnvelope(
        user_message="research this",
        request_id="request:trace-9",
    )
    assert envelope.begin() == "live state"
    assert envelope.context == "live state"
    assert envelope.begin() == "live state"
    envelope.finalize("grounded and concierge-rewritten answer")

    assert seen[0][1]["task_id"] == "request:trace-9"
    assert seen[1][1]["task_id"] == "request:trace-9"
    assert seen[0][1]["operation_type"] == "user_interaction"
    assert seen[1][2] == "grounded and concierge-rewritten answer"


def test_finalize_is_idempotent(monkeypatch) -> None:
    registry = HookRegistry()
    results = []

    def post(ctx):
        results.append(ctx.get("result"))
        return ctx

    registry.register("capture", HookPoint.ON_COMPLETE, post)
    monkeypatch.setattr("app.lifecycle_hooks.get_registry", lambda: registry)

    envelope = SubjectiveRequestEnvelope("question", "request:one")
    envelope.begin()
    envelope.finalize("first")
    envelope.finalize("second")

    assert results == ["first"]


def test_hook_failures_are_contained(monkeypatch) -> None:
    class BrokenRegistry:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("hook fault")

    monkeypatch.setattr(
        "app.lifecycle_hooks.get_registry", lambda: BrokenRegistry(),
    )
    envelope = SubjectiveRequestEnvelope("question", "request:broken")

    assert envelope.begin() == ""
    envelope.finalize("answer")
