"""Declarative master-switch registry (Phase E.2, 2026-05-22).

Roughly 50+ boolean toggles in :mod:`app.runtime_settings` follow the
same three-step pattern:

  1. A default in ``_defaults()``::

        "X_enabled": False,

  2. A getter::

        def get_X_enabled() -> bool:
            return bool(_ensure_initialized().get("X_enabled", False))

  3. A setter::

        def set_X_enabled(value: bool) -> None:
            _update({"X_enabled": bool(value)})
            logger.info("runtime_settings: X_enabled set to %s", bool(value))

The boilerplate is mechanical. This module provides a declarative
alternative: subsystems register their toggles up-front, and the
registry auto-generates getter/setter pairs with consistent logging,
validation, and behaviour.

Existing settings are **NOT migrated** — the runtime_settings.py
getter/setter pairs stay intact (zero risk of disturbing the
operator-facing surface that React + Signal flips through). The
registry is opt-in for NEW settings; the existing namespace
co-exists.

Design constraints
──────────────────

  * **Declarative, not implicit**. Every switch is registered with
    an explicit ``MasterSwitch`` record. ``grep "master_switch"``
    surfaces the full namespace for a future operator audit.
  * **Defaults + validators carried in the record**. The same record
    feeds the default-dict in ``_defaults()`` AND the
    set-time validation in the auto-generated setter.
  * **No accidental shadowing**. ``register_into(module)`` refuses
    to overwrite an existing attribute — if you accidentally register
    a name that's already in runtime_settings, the registry fails
    fast at module-load.
  * **Failure-isolated getter** — same posture as the existing
    runtime_settings getters: a read failure degrades to the default,
    never raises.

Usage
─────

::

    from app.utils.master_switches import MasterSwitch, SwitchRegistry

    REGISTRY = SwitchRegistry()

    REGISTRY.register(MasterSwitch(
        name="my_subsystem_enabled",
        default=False,
        description="Turn on the my_subsystem idle scheduler tuple.",
    ))

    # Auto-generate get_my_subsystem_enabled + set_my_subsystem_enabled
    # on the current module:
    REGISTRY.bind(globals())

    # Or, when the subsystem owns its own module:
    REGISTRY.bind_as_module_attrs(my_subsystem)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MasterSwitch:
    """One declarative master-switch entry.

    Attributes
    ----------
    name
        The runtime_settings key (e.g. ``"benchmarks_enabled"``).
        Auto-generated functions are ``get_<name>`` and ``set_<name>``.
    default
        Returned when the runtime_settings store has no value for
        this key, or when reading fails.
    description
        One-line operator-facing summary. Surfaces in the React
        Settings card and the registry's introspection helpers.
    validator
        Optional callable ``value -> value`` that normalises a
        proposed set. Used by the auto-generated setter to coerce
        / refuse bad values. Raise :class:`ValueError` to refuse;
        return the coerced value to accept.
        Default behaviour is :func:`bool` (any input → bool).
    """

    name: str
    default: Any = False
    description: str = ""
    validator: Optional[Callable[[Any], Any]] = None

    def normalise(self, value: Any) -> Any:
        """Apply the validator. None ``validator`` -> ``bool(value)``.

        Used by the auto-generated setter before persisting.
        """
        if self.validator is None:
            return bool(value)
        return self.validator(value)


class SwitchRegistry:
    """A collection of :class:`MasterSwitch` records that knows how
    to bind itself onto a module's namespace.

    Each instance owns a distinct namespace. Multiple registries can
    co-exist — a subsystem can keep its switches together without
    polluting the global namespace.
    """

    def __init__(
        self,
        *,
        settings_module: str = "app.runtime_settings",
    ) -> None:
        self._switches: dict[str, MasterSwitch] = {}
        self._settings_module = settings_module

    # ── Registration ─────────────────────────────────────────────

    def register(self, switch: MasterSwitch) -> None:
        """Add ``switch`` to the registry. Refuses duplicates."""
        if switch.name in self._switches:
            raise ValueError(
                f"master_switch {switch.name!r} already registered"
            )
        self._switches[switch.name] = switch

    def register_many(self, switches: list[MasterSwitch]) -> None:
        """Convenience: register multiple at once."""
        for s in switches:
            self.register(s)

    def get(self, name: str) -> Optional[MasterSwitch]:
        """Lookup. None when not found."""
        return self._switches.get(name)

    def all(self) -> list[MasterSwitch]:
        """All registered switches, in registration order."""
        return list(self._switches.values())

    def defaults_dict(self) -> dict[str, Any]:
        """Map of ``name -> default`` — feed this into
        ``_defaults()`` in runtime_settings."""
        return {s.name: s.default for s in self._switches.values()}

    # ── Binding ──────────────────────────────────────────────────

    def bind(
        self,
        namespace: dict[str, Any],
        *,
        refuse_shadow: bool = True,
    ) -> None:
        """Auto-generate ``get_<name>`` / ``set_<name>`` functions on
        ``namespace`` for every registered switch.

        Pass ``globals()`` from the calling module to install the
        functions there. ``refuse_shadow=True`` (default) raises if
        the namespace already has a function with the target name —
        prevents accidentally shadowing a hand-written getter that
        already does its own bespoke logic.
        """
        for switch in self._switches.values():
            getter_name = f"get_{switch.name}"
            setter_name = f"set_{switch.name}"
            if refuse_shadow:
                for nm in (getter_name, setter_name):
                    if nm in namespace:
                        raise ValueError(
                            f"master_switch binding: {nm!r} already in "
                            f"namespace — pass refuse_shadow=False to "
                            f"override"
                        )
            namespace[getter_name] = self._make_getter(switch)
            namespace[setter_name] = self._make_setter(switch)

    def bind_as_module_attrs(
        self, module: Any, *, refuse_shadow: bool = True,
    ) -> None:
        """Attach getter/setter pairs as attributes on an already-
        imported module. Equivalent to :meth:`bind` but doesn't need
        the caller to pass ``globals()``.
        """
        for switch in self._switches.values():
            getter_name = f"get_{switch.name}"
            setter_name = f"set_{switch.name}"
            if refuse_shadow:
                for nm in (getter_name, setter_name):
                    if hasattr(module, nm):
                        raise ValueError(
                            f"master_switch binding: module "
                            f"{module.__name__} already has {nm!r}"
                        )
            setattr(module, getter_name, self._make_getter(switch))
            setattr(module, setter_name, self._make_setter(switch))

    # ── Generators ───────────────────────────────────────────────

    def _make_getter(self, switch: MasterSwitch) -> Callable[[], Any]:
        """Build a parameterless getter for ``switch``.

        Same failure-isolated shape as the hand-written
        runtime_settings getters: any read error degrades to
        ``switch.default``. Never raises.
        """
        settings_module = self._settings_module
        name = switch.name
        default = switch.default

        def _getter() -> Any:
            try:
                import importlib
                rs = importlib.import_module(settings_module)
                # Most settings modules expose ``_ensure_initialized``
                # (the runtime_settings pattern) — use it when present.
                ensure = getattr(rs, "_ensure_initialized", None)
                if callable(ensure):
                    state = ensure()
                    return state.get(name, default)
                # Fall back to a public read API if the module provides
                # a generic ``get`` function.
                generic_get = getattr(rs, "get", None)
                if callable(generic_get):
                    return generic_get(name, default)
            except Exception:
                logger.debug(
                    "master_switch[%s]: read failed, returning default",
                    name, exc_info=True,
                )
            return default

        _getter.__name__ = f"get_{name}"
        _getter.__qualname__ = _getter.__name__
        _getter.__doc__ = (
            f"Auto-generated master-switch getter for {name!r}.\n"
            f"Default: {default!r}.\n{switch.description}"
        )
        return _getter

    def _make_setter(self, switch: MasterSwitch) -> Callable[[Any], None]:
        """Build a one-arg setter for ``switch``.

        Applies ``switch.normalise`` before persisting; raises
        :class:`ValueError` when the validator refuses.
        """
        settings_module = self._settings_module
        name = switch.name

        def _setter(value: Any) -> None:
            normalised = switch.normalise(value)
            try:
                import importlib
                rs = importlib.import_module(settings_module)
                update = getattr(rs, "_update", None)
                if callable(update):
                    update({name: normalised})
                else:
                    generic_set = getattr(rs, "set", None)
                    if callable(generic_set):
                        generic_set(name, normalised)
                    else:
                        logger.warning(
                            "master_switch[%s]: no _update / set "
                            "available in %s", name, settings_module,
                        )
                        return
            except Exception:
                logger.warning(
                    "master_switch[%s]: write failed",
                    name, exc_info=True,
                )
                return
            logger.info(
                "master_switch: %s set to %s", name, normalised,
            )

        _setter.__name__ = f"set_{name}"
        _setter.__qualname__ = _setter.__name__
        _setter.__doc__ = (
            f"Auto-generated master-switch setter for {name!r}. "
            f"Normalises via the configured validator before persisting."
        )
        return _setter


__all__ = ["MasterSwitch", "SwitchRegistry"]
