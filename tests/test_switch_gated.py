"""Tests for the @switch_gated decorator (Phase E.3, 2026-05-22).

Pins the contract:

  * Decorator requires explicit ``on_disabled`` (no silent None).
  * Resolution order: runtime_settings getter → env var → default.
  * Switch ON → wrapped function runs.
  * Switch OFF → ``on_disabled`` returned without calling the function.
  * Factory ``on_disabled`` (zero-arg callable) called fresh each time.
  * Async functions supported.
  * Failure in lookup degrades to default (failure-isolated).
  * Decorator stashes ``__switch_name__`` for introspection.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


from app.utils.switch_gated import switch_gated  # noqa: E402


# ── Decorator-time validation ───────────────────────────────────────


class TestDecoratorValidation:
    def test_on_disabled_required(self):
        with pytest.raises(TypeError) as excinfo:
            @switch_gated("foo")
            def f():
                pass
        assert "on_disabled is required" in str(excinfo.value)

    def test_on_disabled_none_allowed_when_explicit(self):
        @switch_gated("foo", on_disabled=None, default=False)
        def f():
            return "ran"
        # OFF → returns None (the explicit sentinel)
        assert f() is None

    def test_stashes_switch_metadata(self):
        @switch_gated("foo", on_disabled=[], default=True)
        def f():
            pass
        assert f.__switch_name__ == "foo"
        assert f.__switch_default__ is True


# ── ON path ─────────────────────────────────────────────────────────


class TestOnPath:
    def test_on_runs_function(self, monkeypatch):
        # Force a settings module that returns True
        fake = MagicMock()
        fake.get_foo.return_value = True
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated("foo", on_disabled=[], settings_module="_test_rs")
        def f(x):
            return x * 2

        assert f(7) == 14

    def test_on_preserves_args_kwargs(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = True
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated("foo", on_disabled=None, settings_module="_test_rs")
        def f(a, b, *, c=10):
            return a + b + c

        assert f(1, 2, c=3) == 6


# ── OFF path ────────────────────────────────────────────────────────


class TestOffPath:
    def test_off_skips_function(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        calls = []

        @switch_gated("foo", on_disabled=[], settings_module="_test_rs")
        def f(x):
            calls.append(x)
            return [x]

        result = f(99)
        assert result == []
        assert calls == []

    def test_off_returns_configured_sentinel(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated("foo", on_disabled="DISABLED", settings_module="_test_rs")
        def f():
            return "ran"

        assert f() == "DISABLED"

    def test_off_factory_called_fresh_each_time(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        counter = [0]

        def _fresh():
            counter[0] += 1
            return {"call_n": counter[0]}

        @switch_gated("foo", on_disabled=_fresh, settings_module="_test_rs")
        def f():
            return "ran"

        # Each call produces a fresh dict — not a shared one
        r1 = f()
        r2 = f()
        assert r1 == {"call_n": 1}
        assert r2 == {"call_n": 2}
        assert r1 is not r2

    def test_off_container_type_treated_as_factory(self, monkeypatch):
        """Phase E.3 follow-up: ``on_disabled=list`` (the built-in type)
        must call it each time to produce a fresh empty list — NOT
        return the ``list`` class object itself. Same applies to dict,
        set, tuple, etc."""
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated(
            "foo", on_disabled=list, settings_module="_test_rs",
        )
        def f():
            return ["ran"]

        r1 = f()
        r2 = f()
        # Each call produces a NEW empty list — never the type object
        assert r1 == []
        assert r2 == []
        assert r1 is not r2
        assert type(r1) is list  # not the class itself

    def test_off_dict_type_factory(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated(
            "foo", on_disabled=dict, settings_module="_test_rs",
        )
        def f():
            return {"ran": True}

        r = f()
        assert r == {}
        assert isinstance(r, dict)


# ── Resolution order: runtime_settings → env → default ──────────────


class TestResolution:
    def test_settings_getter_wins(self, monkeypatch):
        # Setting returns True; env says false; default false
        fake = MagicMock()
        fake.get_foo.return_value = True
        monkeypatch.setitem(sys.modules, "_test_rs", fake)
        monkeypatch.setenv("foo", "false")

        @switch_gated(
            "foo", on_disabled=[], default=False,
            settings_module="_test_rs",
        )
        def f():
            return "ran"

        assert f() == "ran"

    def test_env_falls_through_when_getter_returns_none(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = None
        monkeypatch.setitem(sys.modules, "_test_rs", fake)
        monkeypatch.setenv("foo", "true")

        @switch_gated(
            "foo", on_disabled=[], default=False,
            settings_module="_test_rs",
        )
        def f():
            return "ran"

        assert f() == "ran"

    def test_env_falls_through_when_getter_missing(self, monkeypatch):
        # Module has no get_foo attribute
        fake = MagicMock(spec=[])
        monkeypatch.setitem(sys.modules, "_test_rs", fake)
        monkeypatch.setenv("FOO", "on")

        @switch_gated(
            "foo", on_disabled=[], default=False,
            settings_module="_test_rs",
        )
        def f():
            return "ran"

        assert f() == "ran"

    def test_default_used_when_no_other_source(self, monkeypatch):
        fake = MagicMock(spec=[])
        monkeypatch.setitem(sys.modules, "_test_rs", fake)
        monkeypatch.delenv("foo", raising=False)
        monkeypatch.delenv("FOO", raising=False)

        @switch_gated(
            "foo", on_disabled=[], default=True,
            settings_module="_test_rs",
        )
        def f():
            return "ran"

        # Default True → ON
        assert f() == "ran"

    def test_env_value_parsing(self, monkeypatch):
        fake = MagicMock(spec=[])
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated(
            "foo", on_disabled="off",
            settings_module="_test_rs",
        )
        def f():
            return "on"

        # Truthy values
        for truthy in ("true", "TRUE", "1", "yes", "on", "y", "t"):
            monkeypatch.setenv("foo", truthy)
            assert f() == "on", f"failed for {truthy!r}"
        # Falsy values
        for falsy in ("false", "0", "no", "off", "", " "):
            monkeypatch.setenv("foo", falsy)
            assert f() == "off", f"failed for {falsy!r}"

    def test_failure_in_resolution_falls_back_to_default(
        self, monkeypatch,
    ):
        # Getter raises
        fake = MagicMock()
        fake.get_foo.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "_test_rs", fake)
        monkeypatch.delenv("foo", raising=False)

        @switch_gated(
            "foo", on_disabled="off", default=False,
            settings_module="_test_rs",
        )
        def f():
            return "on"

        # Resolver caught the error, fell through env (unset), used default
        assert f() == "off"


# ── Async support ───────────────────────────────────────────────────


class TestAsync:
    def test_async_function_off(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = False
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated("foo", on_disabled=[], settings_module="_test_rs")
        async def f():
            return ["ran"]

        result = asyncio.run(f())
        assert result == []

    def test_async_function_on(self, monkeypatch):
        fake = MagicMock()
        fake.get_foo.return_value = True
        monkeypatch.setitem(sys.modules, "_test_rs", fake)

        @switch_gated("foo", on_disabled=[], settings_module="_test_rs")
        async def f(x):
            return [x * 2]

        result = asyncio.run(f(5))
        assert result == [10]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
