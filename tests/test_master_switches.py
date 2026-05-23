"""Tests for the master-switch declarative registry (Phase E.2, 2026-05-22).

Pins the contract:

  * MasterSwitch record validation (bool default with no validator).
  * SwitchRegistry refuses duplicate registration.
  * bind(namespace) installs get_<name> / set_<name> functions.
  * Getter falls back to default on any read error.
  * Setter applies validator (bool by default).
  * Custom validator can coerce (e.g. to positive float) and refuse.
  * refuse_shadow=True refuses to overwrite an existing name.
  * bind_as_module_attrs alternative entry-point works.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


from app.utils.master_switches import (  # noqa: E402
    MasterSwitch, SwitchRegistry,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _make_fake_settings_module(initial: dict | None = None) -> str:
    """Install a fake runtime-settings module into sys.modules and
    return its name for SwitchRegistry construction."""
    name = f"_fake_rs_{id(initial)}_{len(sys.modules)}"
    mod = types.ModuleType(name)
    state = dict(initial or {})

    def _ensure_initialized():
        return state

    def _update(updates):
        state.update(updates)

    mod._ensure_initialized = _ensure_initialized  # type: ignore[attr-defined]
    mod._update = _update  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return name


# ── MasterSwitch record ─────────────────────────────────────────────


class TestMasterSwitchRecord:
    def test_default_validator_is_bool(self):
        s = MasterSwitch(name="x", default=False)
        assert s.normalise(1) is True
        assert s.normalise(0) is False
        assert s.normalise("") is False
        assert s.normalise("yes") is True

    def test_custom_validator_can_coerce(self):
        def _positive_float(v):
            f = float(v)
            if f <= 0:
                raise ValueError(f"must be > 0, got {f}")
            return f

        s = MasterSwitch(
            name="cap_usd", default=None, validator=_positive_float,
        )
        assert s.normalise("12.5") == 12.5
        assert s.normalise(3) == 3.0
        with pytest.raises(ValueError):
            s.normalise(-5)


# ── SwitchRegistry ──────────────────────────────────────────────────


class TestRegistry:
    def test_register_and_lookup(self):
        r = SwitchRegistry()
        r.register(MasterSwitch(name="foo", default=False))
        assert r.get("foo") is not None
        assert r.get("missing") is None

    def test_register_duplicate_refused(self):
        r = SwitchRegistry()
        r.register(MasterSwitch(name="foo", default=False))
        with pytest.raises(ValueError) as excinfo:
            r.register(MasterSwitch(name="foo", default=True))
        assert "already registered" in str(excinfo.value)

    def test_register_many(self):
        r = SwitchRegistry()
        r.register_many([
            MasterSwitch(name="a", default=False),
            MasterSwitch(name="b", default=True),
        ])
        assert len(r.all()) == 2

    def test_defaults_dict(self):
        r = SwitchRegistry()
        r.register_many([
            MasterSwitch(name="a", default=False),
            MasterSwitch(name="b", default=True),
            MasterSwitch(name="c", default=42),
        ])
        d = r.defaults_dict()
        assert d == {"a": False, "b": True, "c": 42}


# ── Binding ─────────────────────────────────────────────────────────


class TestBind:
    def test_bind_installs_get_and_set(self):
        modname = _make_fake_settings_module({"foo_enabled": False})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo_enabled", default=False))
        ns: dict = {}
        r.bind(ns)
        assert "get_foo_enabled" in ns
        assert "set_foo_enabled" in ns
        assert callable(ns["get_foo_enabled"])
        assert callable(ns["set_foo_enabled"])

    def test_generated_getter_reads_state(self):
        modname = _make_fake_settings_module({"foo": True})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {}
        r.bind(ns)
        assert ns["get_foo"]() is True

    def test_generated_getter_returns_default_on_missing_key(self):
        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="bar", default=True))
        ns: dict = {}
        r.bind(ns)
        assert ns["get_bar"]() is True

    def test_generated_getter_returns_default_on_module_failure(
        self, monkeypatch,
    ):
        # Settings module raises on _ensure_initialized
        modname = f"_broken_rs_{id(self)}"
        mod = types.ModuleType(modname)

        def _broken():
            raise RuntimeError("nope")

        mod._ensure_initialized = _broken  # type: ignore[attr-defined]
        sys.modules[modname] = mod
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="zap", default=False))
        ns: dict = {}
        r.bind(ns)
        # Read error degrades to default, never raises
        assert ns["get_zap"]() is False

    def test_generated_setter_persists_value(self):
        modname = _make_fake_settings_module({"foo": False})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {}
        r.bind(ns)
        ns["set_foo"](True)
        assert ns["get_foo"]() is True

    def test_generated_setter_normalises_via_default_validator(self):
        # Default validator is bool; passing "yes" must coerce
        modname = _make_fake_settings_module({"foo": False})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {}
        r.bind(ns)
        ns["set_foo"]("yes")  # string truthy
        assert ns["get_foo"]() is True
        ns["set_foo"](0)
        assert ns["get_foo"]() is False

    def test_generated_setter_honors_custom_validator(self):
        def _coerce_positive(v):
            f = float(v)
            if f <= 0:
                raise ValueError(f"non-positive: {f}")
            return f

        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(
            name="cap", default=None, validator=_coerce_positive,
        ))
        ns: dict = {}
        r.bind(ns)
        ns["set_cap"]("25.50")
        assert ns["get_cap"]() == 25.50

    def test_generated_setter_validator_refusal_propagates(self):
        def _refuser(v):
            raise ValueError("nope")

        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(
            name="cap", default=None, validator=_refuser,
        ))
        ns: dict = {}
        r.bind(ns)
        with pytest.raises(ValueError):
            ns["set_cap"]("anything")

    def test_bind_refuses_shadow_by_default(self):
        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {"get_foo": lambda: "hand-written"}
        with pytest.raises(ValueError) as excinfo:
            r.bind(ns)
        assert "already in namespace" in str(excinfo.value)

    def test_bind_allows_shadow_when_explicit(self):
        modname = _make_fake_settings_module({"foo": True})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {"get_foo": lambda: "hand-written"}
        r.bind(ns, refuse_shadow=False)
        # The bound getter wins
        assert ns["get_foo"]() is True


# ── bind_as_module_attrs ────────────────────────────────────────────


class TestBindAsModuleAttrs:
    def test_attaches_to_module(self):
        modname = _make_fake_settings_module({"thingy": True})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="thingy", default=False))
        target = types.ModuleType("test_target")
        r.bind_as_module_attrs(target)
        assert callable(getattr(target, "get_thingy"))
        assert getattr(target, "get_thingy")() is True

    def test_refuses_shadow(self):
        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="thingy", default=False))
        target = types.ModuleType("test_target")
        target.get_thingy = lambda: "existing"  # type: ignore[attr-defined]
        with pytest.raises(ValueError):
            r.bind_as_module_attrs(target)


# ── Docstring + name metadata ───────────────────────────────────────


class TestMetadata:
    def test_getter_has_correct_name_and_doc(self):
        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(
            name="foo", default=False,
            description="Turn on the foo subsystem.",
        ))
        ns: dict = {}
        r.bind(ns)
        getter = ns["get_foo"]
        assert getter.__name__ == "get_foo"
        assert "foo" in getter.__doc__
        assert "Turn on the foo subsystem" in getter.__doc__

    def test_setter_has_correct_name(self):
        modname = _make_fake_settings_module({})
        r = SwitchRegistry(settings_module=modname)
        r.register(MasterSwitch(name="foo", default=False))
        ns: dict = {}
        r.bind(ns)
        setter = ns["set_foo"]
        assert setter.__name__ == "set_foo"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
