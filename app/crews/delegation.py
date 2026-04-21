"""
delegation.py — One dispatcher for "try delegated, fall back to single-agent".

Before this module, each crew that had a delegated variant opened its
``run()`` method with an identical 12-line try/except:

    try:
        from app.crews.delegation_settings import is_enabled
        if is_enabled("research"):
            from app.crews.delegated_research import DelegatedResearchCrew
            return DelegatedResearchCrew().run(...)
    except Exception:
        logger.warning(
            "Delegated research crew failed; falling back to single-agent",
            exc_info=True,
        )
    return self._run_single_agent(...)

Every crew did this the same way with only the crew name and the
delegated-class import substituted.  ``dispatch()`` replaces all of
them with one call site.

Example
-------
::

    class ResearchCrew:
        def run(self, topic, parent_task_id=None, difficulty=5):
            return dispatch(
                "research",
                delegated_cls=lambda: _load("app.crews.delegated_research",
                                            "DelegatedResearchCrew"),
                single_agent_fn=self._run_single_agent,
                topic=topic, parent_task_id=parent_task_id,
                difficulty=difficulty,
            )

``delegated_cls`` is a *callable* that returns the class — this keeps
the import lazy, so a broken ``delegated_research`` module can't break
import of ``research_crew``.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def dispatch(
    crew_name: str,
    *,
    delegated_cls: Callable[[], Any] | None,
    single_agent_fn: Callable[..., str],
    **run_kwargs,
) -> str:
    """Run the delegated variant of the crew if delegation is enabled
    for it, otherwise run the single-agent path.

    On any failure during the delegated attempt (including import
    failure of the delegated module), log a warning and fall through to
    the single-agent path.  Never raises from the dispatch layer
    itself — either the delegated crew's exception (re-raised) or the
    single-agent function's exception reaches the caller; we don't
    layer our own.

    Parameters
    ----------
    crew_name : Must match the key used in
        ``app/workspace/delegation_settings.json`` (e.g. ``"research"``,
        ``"coding"``, ``"writing"``).
    delegated_cls : zero-arg callable returning the delegated-crew
        class.  Typically a lambda that does the import lazily so a
        broken delegated module doesn't break single-agent imports.
        Pass ``None`` to signal "no delegated variant exists yet";
        dispatch then unconditionally runs ``single_agent_fn``.
    single_agent_fn : function implementing the single-agent path.
        Receives ``**run_kwargs`` as-is.
    **run_kwargs : forwarded to whichever path is chosen.

    Returns
    -------
    str — the crew's result, from either path.
    """
    if delegated_cls is not None:
        try:
            from app.crews.delegation_settings import is_enabled
            if is_enabled(crew_name):
                cls = delegated_cls()
                return cls().run(**run_kwargs)
        except Exception:
            logger.warning(
                "delegation.dispatch: delegated %s crew failed; "
                "falling back to single-agent",
                crew_name,
                exc_info=True,
            )
    return single_agent_fn(**run_kwargs)


def lazy(module_path: str, class_name: str) -> Callable[[], Any]:
    """Helper: build a zero-arg callable that imports ``class_name``
    from ``module_path`` on first use.

    Lets callers write ``delegated_cls=lazy("app.crews.delegated_research",
    "DelegatedResearchCrew")`` without paying the import cost until
    delegation is actually enabled.
    """
    def _load() -> Any:
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    return _load
