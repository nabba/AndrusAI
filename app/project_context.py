"""Request-scoped "current project" tracking.

A ContextVar that lets telemetry write sites (token recorder, request-cost
recorder, crew tracker) attribute data to a project without every call site
growing a new parameter.

Resolution order used by ``resolve_current_project_id()``:
  1. The thread-/task-local ContextVar (set via ``project_scope(...)`` or
     ``set_current_project_id(...)``).
  2. The global "active project" from ``control_plane.projects``.
  3. ``None`` — row will be stored without a project_id (legacy behaviour).
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_project_id: ContextVar[str | None] = ContextVar("project_id", default=None)
_agent_role: ContextVar[str | None] = ContextVar("agent_role", default=None)


def set_current_project_id(project_id: str | None):
    """Set the current project id for this async/thread context.

    Returns a token that can be passed to :func:`reset_current_project_id`
    to restore the previous value.
    """
    return _project_id.set(project_id)


def reset_current_project_id(token) -> None:
    """Undo a previous :func:`set_current_project_id`."""
    try:
        _project_id.reset(token)
    except ValueError:
        # Token belongs to a different Context — benign on thread boundaries.
        pass


def get_current_project_id() -> str | None:
    """Return the project id set via the ContextVar (or None if unset)."""
    return _project_id.get()


def resolve_current_project_id() -> str | None:
    """Return the best-guess current project id: ContextVar → active project → None."""
    pid = _project_id.get()
    if pid:
        return pid
    try:
        from app.control_plane.projects import get_projects
        return get_projects().get_active_project_id() or None
    except Exception as exc:
        logger.debug("resolve_current_project_id: fallback failed: %s", exc)
        return None


def set_current_agent_role(agent_role: str | None):
    """Mark the current crew/agent so telemetry rolls up under the right role."""
    return _agent_role.set(agent_role)


def reset_current_agent_role(token) -> None:
    try:
        _agent_role.reset(token)
    except ValueError:
        pass


def resolve_current_agent_role() -> str | None:
    """Return the current agent role, or None."""
    return _agent_role.get()


@contextlib.contextmanager
def agent_scope(agent_role: str | None):
    """Context manager scoping subsequent telemetry to a crew/agent role."""
    token = _agent_role.set(agent_role)
    try:
        yield
    finally:
        try:
            _agent_role.reset(token)
        except ValueError:
            pass


@contextlib.contextmanager
def project_scope(project_id: str | None):
    """Context manager scoping subsequent telemetry to a project id.

    Example::

        with project_scope("abc-123"):
            commander.handle(...)
    """
    token = _project_id.set(project_id)
    try:
        yield
    finally:
        try:
            _project_id.reset(token)
        except ValueError:
            pass
