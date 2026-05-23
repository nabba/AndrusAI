"""``@switch_gated`` — short-circuit a function when a runtime setting is OFF
(Phase E.3, 2026-05-22).

Roughly 30 functions under ``app/`` open with the same shape::

    def some_feature(...):
        if not _enabled():
            return []      # or None, or "", or {}, ...
        # actual work

The pre-check is identical except for:

  * Which setting is being read (``code_intel_enabled`` /
    ``benchmarks_enabled`` / ``PAPER_PIPELINE_PEPS_ENABLED`` / …).
  * What "off" sentinel the function returns (``None`` / ``[]`` / ``""``
    / ``{}`` / 0 / a dataclass with ``disabled=True``).

This module collapses that pattern into one decorator::

    @switch_gated("code_intel_enabled", on_disabled=[])
    def find_callers(name: str) -> list[Caller]:
        ...

When the setting is OFF (or the lookup fails), the wrapped call is
skipped and the configured sentinel is returned. When ON, the function
runs as written.

Three sources for the setting value (in priority order):

  1. **runtime_settings.get_X()** for the documented runtime keys.
     Pulled lazily via ``importlib`` so tests can monkey-patch the
     module.
  2. **Environment variable** (``os.environ[name]`` with the same
     truthy parsing as ``app.episteme.feed_sources._enabled``).
  3. **Explicit ``default``** kwarg on the decorator.

If a lookup raises, the decorator FALLS BACK to the next source. The
ultimate fallback is the ``default`` — so a configuration glitch
(missing runtime_settings, broken env-var parse) degrades to whichever
posture the developer marked safe.

Design constraints
──────────────────

  * **Opt-in only**. Existing sites don't migrate; new sites can
    adopt the decorator to skip writing the same five-line preamble.
  * **Stays out of the call path when ON**. The pre-check is a single
    attribute lookup + a single bool compare in the common case.
  * **Failure-isolated lookup**. Any error in the setting resolver
    treats the switch as the operator-marked default — never raises.
"""
from __future__ import annotations

import functools
import importlib
import inspect
import logging
import os
from typing import Any, Callable, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

R = TypeVar("R")

# Sentinel — distinguishes "no default specified" from "default is None".
_MISSING = object()


# ── Truthy / falsy env parsing (matches existing _enabled idiom) ───


def _parse_env_bool(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes", "on", "y", "t")


# ── Resolver ────────────────────────────────────────────────────────


def _resolve_switch(
    name: str,
    *,
    default: bool,
    settings_module: str = "app.runtime_settings",
) -> bool:
    """Return whether ``name`` is currently ON. Failure-isolated.

    Resolution order:
      1. ``runtime_settings.get_<name>()`` if such a getter exists.
      2. ``os.environ[name]`` (or its UPPERCASE variant), if set.
      3. ``default``.

    A lookup that raises is caught and falls through to the next
    source. The final fallback is ``default``.
    """
    # 1. runtime_settings getter
    getter_name = f"get_{name}"
    try:
        rs = importlib.import_module(settings_module)
        getter = getattr(rs, getter_name, None)
        if getter is not None and callable(getter):
            val = getter()
            if val is None:
                # ``None`` is treated as the operator-marked "unset"
                # state; fall through to env / default.
                pass
            else:
                return bool(val)
    except Exception:
        logger.debug(
            "switch_gated: runtime_settings lookup for %s raised",
            name, exc_info=True,
        )

    # 2. env var
    for key in (name, name.upper()):
        raw = os.environ.get(key)
        if raw is not None and raw.strip() != "":
            return _parse_env_bool(raw)

    # 3. default
    return default


# ── Decorator ───────────────────────────────────────────────────────


def switch_gated(
    name: str,
    *,
    on_disabled: Any = _MISSING,
    default: bool = False,
    settings_module: str = "app.runtime_settings",
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Short-circuit a function when the named runtime setting is OFF.

    Parameters
    ----------
    name
        Setting name. The decorator first tries
        ``runtime_settings.get_<name>()``; if absent, the env var with
        the same name (case-insensitive); else ``default``.
    on_disabled
        Value to return when the switch is OFF. Required — pass
        ``None`` explicitly to make None the sentinel. The decorator
        refuses construction if omitted, because returning ``None``
        silently from a function that's typed to return ``list[X]``
        is exactly the bug class this primitive is supposed to
        prevent.

        Can be a *value* (returned as-is) or a *zero-arg callable*
        (called each time to produce a fresh value — useful when the
        sentinel is a mutable container).
    default
        Posture when no runtime_settings / env-var value can be
        resolved. Defaults to ``False`` (treat as OFF), which is the
        ship-dormant default the rest of the codebase uses.
    settings_module
        Module to look the getter in. Default ``"app.runtime_settings"``.
        Override only for tests.

    Returns
    -------
    decorator
        Wraps the target function so it short-circuits when OFF.
    """
    if on_disabled is _MISSING:
        raise TypeError(
            "switch_gated: on_disabled is required (pass None explicitly "
            "to use None as the off sentinel)"
        )

    # Any callable — including container types like ``list`` / ``dict`` —
    # is treated as a factory and called per request. That's almost
    # always what the caller wants: ``on_disabled=list`` produces a
    # fresh ``[]`` per call (no shared-mutable-state hazard). Plain
    # values (None, "", [], strings, ints) are not callable and are
    # returned as-is.
    is_factory = callable(on_disabled)

    def _decorator(fn: Callable[..., R]) -> Callable[..., R]:
        is_coro = inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> R:
            if not _resolve_switch(
                name, default=default,
                settings_module=settings_module,
            ):
                return on_disabled() if is_factory else on_disabled
            return fn(*args, **kwargs)

        @functools.wraps(fn)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> R:
            if not _resolve_switch(
                name, default=default,
                settings_module=settings_module,
            ):
                return on_disabled() if is_factory else on_disabled
            return await fn(*args, **kwargs)

        wrapper = _async_wrapper if is_coro else _sync_wrapper
        # Stash metadata so introspection tools can find the switch name
        wrapper.__switch_name__ = name  # type: ignore[attr-defined]
        wrapper.__switch_default__ = default  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return _decorator


__all__ = ["switch_gated"]
