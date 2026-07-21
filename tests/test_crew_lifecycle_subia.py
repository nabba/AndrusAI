"""Regression coverage for the live SubIA crew-boundary extension points."""

from __future__ import annotations

import pytest

from app.crews import lifecycle


def _install_isolated_event_bus(monkeypatch) -> None:
    def started(ctx) -> None:
        ctx.task_id = "firebase-task-17"

    monkeypatch.setattr(lifecycle.crew_events, "fire_crew_started", started)
    monkeypatch.setattr(
        lifecycle.crew_events, "fire_crew_completed", lambda _ctx: None,
    )
    monkeypatch.setattr(
        lifecycle.crew_events, "fire_crew_failed", lambda _ctx: None,
    )


def test_success_boundary_uses_one_stable_task_id(monkeypatch) -> None:
    _install_isolated_event_bus(monkeypatch)
    calls = []
    monkeypatch.setattr(
        lifecycle, "subia_pre_task",
        lambda crew, title, task_id: calls.append(
            ("pre", crew, title, task_id),
        ),
    )
    monkeypatch.setattr(
        lifecycle, "subia_post_task",
        lambda crew, status, exc, task_id, title: calls.append(
            ("post", crew, title, task_id, status, exc),
        ),
    )

    with lifecycle.crew_lifecycle(
        "research", "researcher", "Find primary sources",
    ) as ctx:
        ctx.set_outcome("done")

    assert calls == [
        ("pre", "research", "Find primary sources", "firebase-task-17"),
        (
            "post", "research", "Find primary sources",
            "firebase-task-17", "success", None,
        ),
    ]


def test_failure_boundary_closes_before_reraising(monkeypatch) -> None:
    _install_isolated_event_bus(monkeypatch)
    calls = []
    monkeypatch.setattr(
        lifecycle, "subia_pre_task",
        lambda crew, title, task_id: calls.append(("pre", task_id)),
    )
    monkeypatch.setattr(
        lifecycle, "subia_post_task",
        lambda crew, status, exc, task_id, title: calls.append(
            ("post", status, task_id, str(exc)),
        ),
    )

    with pytest.raises(RuntimeError, match="crew exploded"):
        with lifecycle.crew_lifecycle(
            "research", "researcher", "Find primary sources",
        ):
            raise RuntimeError("crew exploded")

    assert calls == [
        ("pre", "firebase-task-17"),
        ("post", "failed", "firebase-task-17", "crew exploded"),
    ]


def test_subia_boundary_failures_never_fail_the_crew(monkeypatch) -> None:
    _install_isolated_event_bus(monkeypatch)

    def fail(*_args) -> None:
        raise RuntimeError("contained SubIA fault")

    monkeypatch.setattr(lifecycle, "subia_pre_task", fail)
    monkeypatch.setattr(lifecycle, "subia_post_task", fail)

    with lifecycle.crew_lifecycle(
        "research", "researcher", "Find primary sources",
    ) as ctx:
        ctx.set_outcome("still succeeded")


def test_orchestrated_scope_suppresses_only_subjective_callback(monkeypatch) -> None:
    event_calls = []
    subjective_calls = []

    def started(ctx) -> None:
        ctx.task_id = "task-88"
        event_calls.append("started")

    monkeypatch.setattr(lifecycle.crew_events, "fire_crew_started", started)
    monkeypatch.setattr(
        lifecycle.crew_events, "fire_crew_completed",
        lambda _ctx: event_calls.append("completed"),
    )
    monkeypatch.setattr(
        lifecycle, "subia_pre_task",
        lambda *_args: subjective_calls.append("pre"),
    )
    monkeypatch.setattr(
        lifecycle, "subia_post_task",
        lambda *_args: subjective_calls.append("post"),
    )

    with lifecycle.suppress_subjective_boundary():
        with lifecycle.crew_lifecycle(
            "research", "researcher", "orchestrated",
        ):
            pass

    assert event_calls == ["started", "completed"]
    assert subjective_calls == []

    # ContextVar reset is mandatory: the next autonomous crew is observed.
    with lifecycle.crew_lifecycle(
        "research", "researcher", "autonomous",
    ):
        pass
    assert subjective_calls == ["pre", "post"]
