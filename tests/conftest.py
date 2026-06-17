"""Test bootstrapping (Phase B.2 cleanup, 2026-05-22).

Centralizes psycopg2 + crewai stub installation that ~58 test files
previously duplicated. The stubs install at conftest **module-load
time** — not in a fixture — because pytest evaluates module-level
code in test files (including their own ``sys.modules.setdefault``
blocks) during collection, which happens BEFORE any fixture runs.

The conftest.py module loads FIRST (pytest discovers and imports it
at session start, before collecting any tests), so our stubs win
the race and per-file setdefault calls become harmless no-ops.

Why this matters
────────────────

Previously, the stub installed by test_A.py would silently change
behavior of test_B.py — for example, test_connector_budget.py
installed a crewai stub whose ``@tool`` decorator was a no-op
pass-through (no ``.name`` attribute attached). Then
test_travel_tools.py iterated ``[t.name for t in tools]`` and
crashed with AttributeError. Same pytest session, different test
file, mysterious failure.

Centralizing here means:
  1. ONE definition of the stub shape — no per-file drift.
  2. The crewai stub now attaches ``.name`` to functions like real
     crewai does, so tests that probe tool metadata work either way.
  3. New test files don't need to copy-paste the block.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


# ── psycopg2 stub ─────────────────────────────────────────────────
# Many app modules import psycopg2 at module-load time. The gateway
# has it installed; the dev host doesn't. The stub provides the two
# exception classes referenced in module-level try/except blocks.
if "psycopg2" not in sys.modules:
    _mock_pg = MagicMock()
    _mock_pg.InterfaceError = type(
        "InterfaceError", (Exception,), {},
    )
    _mock_pg.OperationalError = type(
        "OperationalError", (Exception,), {},
    )
    sys.modules["psycopg2"] = _mock_pg
if "psycopg2.pool" not in sys.modules:
    _mock_pg_pool = MagicMock()
    # Must be a real exception class: ``except pg_pool.PoolError`` clauses
    # (app/control_plane/db.py) raise ``TypeError: catching classes that
    # do not inherit from BaseException`` when this is a bare MagicMock
    # attribute — detonating before the generic ``except Exception``
    # fallback can swallow whatever actually went wrong.
    _mock_pg_pool.PoolError = type("PoolError", (Exception,), {})
    sys.modules["psycopg2.pool"] = _mock_pg_pool
    if "psycopg2" in sys.modules:
        sys.modules["psycopg2"].pool = _mock_pg_pool


# ── crewai stub ───────────────────────────────────────────────────
# When real crewai is installed (gateway / CI), we don't stub.
# On the dev host without it, the stub provides:
#   * ``crewai.tools.tool`` — decorator that attaches ``.name`` to
#     the decorated function (matching real crewai semantics).
#   * ``crewai.tools.BaseTool`` — base class for tool classes;
#     subclassed by some agent tool factories.
try:
    import crewai  # noqa: F401
    _crewai_real = True
except ImportError:
    _crewai_real = False


def _stub_tool_decorator(tool_name):
    """``@tool("name")`` stub matching real crewai's interface
    contract: the decorated function exposes ``.name`` (the tool
    name) and ``.func`` (the wrapped function itself, for direct
    invocation by tests). Real crewai's @tool returns a Tool
    instance with both attributes; we attach them directly to the
    function for a lighter stub.
    """
    def _wrap(fn):
        try:
            fn.name = tool_name
            fn.func = fn  # tests call ``tool.func(args)`` to invoke
        except (AttributeError, TypeError):
            # Some callables (builtins, C-impl) may not allow
            # arbitrary attribute setting. Pass through unchanged.
            pass
        return fn
    return _wrap


if not _crewai_real:
    if "crewai" not in sys.modules:
        sys.modules["crewai"] = types.ModuleType("crewai")
    if "crewai.tools" not in sys.modules:
        _crewai_tools = types.ModuleType("crewai.tools")
        _crewai_tools.tool = _stub_tool_decorator
        _crewai_tools.BaseTool = type("BaseTool", (), {})
        sys.modules["crewai.tools"] = _crewai_tools


# ── v2 settings-shim confinement (2026-06-12) ─────────────────────
# v2 test files call tests._v2_shim.install_settings_shim() at module
# level — their module-level ``app.*`` imports need the fake active
# during collection, so that call can't move into a fixture. But the
# install used to be permanent: fake app.config accessors leaked into
# every test collected after a v2 file (same bug class as the
# module-level ``config_mod.get_settings = ...`` overrides converted
# to autouse monkeypatch fixtures the same day). This fixture confines
# the shim to the tests that opted in: modules that imported
# install_settings_shim get a fresh default install per test; all
# other tests get the real accessors restored first.

@pytest.fixture(autouse=True)
def _confine_v2_settings_shim(request):
    from tests import _v2_shim

    try:
        module = request.module
    except Exception:
        module = None
    uses_shim = getattr(module, "install_settings_shim", None) is not None

    if uses_shim:
        # Fresh default install per test — also resets any override a
        # previous test in the module installed (e.g. mcp_servers_json).
        _v2_shim.install_settings_shim()
        yield
        _v2_shim.uninstall_settings_shim()
    else:
        # Clear any leak from collection-time installs or from modules
        # that call install_settings_shim() inside test bodies
        # (test_budget_default_limit.py).
        _v2_shim.uninstall_settings_shim()
        yield


# ── collection-time leak guard (2026-06-17) ──────────────────────
# Several test files install a module-level MagicMock / bare ModuleType
# stub for a shared dependency under an ``if "X" not in sys.modules``
# guard (e.g. test_emotions.py → app.control_plane, defensive against an
# import-time DB pull; test_long_response.py → litellm, when the real
# package is incomplete). pytest imports EVERY test module during the
# collection phase, so that fake persists in sys.modules and breaks the
# *collection* of unrelated files that import the real module
# (test_epistemic_* need real app.control_plane.auth_dep → "not a
# package"; threads/test_api.py needs real fastapi → "cannot import name
# 'FastAPI'").
#
# Restoring the reals before each collector node is collected confines
# every such stub to its own module without editing the ~25 stubber
# files. A stubber module still installs its fake during its own
# collection (it runs after this hook), and its tests use per-test
# ``patch(...)`` against the real dotted path, so nothing regresses.
# Heavy shared deps that ``app.main``'s import chain pulls in and that
# various test files defensively stub at module level. All are really
# installed in CI / the dev .venv / the gateway container, so restoring
# the real one over a leaked stub is always correct there; on a genuinely
# dep-less host the re-import fails and the stub is left in place.
_PROTECTED_REALS = (
    "fastapi", "litellm", "crewai", "chromadb", "groq",
    "sentence_transformers", "apscheduler", "anthropic",
    "app.control_plane",
)


def _looks_like_stub(mod) -> bool:
    # MagicMock / Mock — its class lives in unittest.mock.
    if type(mod).__module__ == "unittest.mock":
        return True
    # bare types.ModuleType("x") stub — real modules carry a __file__.
    return getattr(mod, "__file__", None) is None


def pytest_collectstart(collector):
    import importlib

    for _name in _PROTECTED_REALS:
        # Scan the package AND every submodule already in sys.modules; a
        # leak can stub the submodule alone (``crewai.tools``) while the
        # parent (``crewai``) stays real, or stub the parent (``apscheduler``)
        # and shadow the real ``apscheduler.schedulers.asyncio`` app.main
        # needs. Restore independently of the parent's state.
        _stubbed = sorted(
            (
                k for k in list(sys.modules)
                if (k == _name or k.startswith(_name + "."))
                and _looks_like_stub(sys.modules[k])
            ),
            key=lambda k: k.count("."),  # parents before submodules
        )
        if not _stubbed:
            continue
        for _k in _stubbed:
            sys.modules.pop(_k, None)
        for _k in _stubbed:
            try:
                importlib.import_module(_k)
            except Exception:  # noqa: BLE001 — genuinely-absent dep: leave it
                pass
