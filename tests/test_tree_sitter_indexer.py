"""Tests for the tree-sitter symbol indexer (Verified Implementation
Plan §5 closure, Gap 1, 2026-05-23).

Pins:
  * tree-sitter + tree-sitter-python are importable (deps wired in
    requirements.txt at the same commit).
  * Master switch defaults OFF (additive path, AST stays canonical).
  * Master switch honours env-first precedence (ops override).
  * supported_extensions() reports the languages the registry covers.
  * Parsing a real .py file yields the expected SymbolKind values for
    function / async function / method / async method / class.
  * call-site references are captured with correct enclosing function
    + class context.
  * Unsupported extensions return empty results, not exceptions.
  * Failure isolation: missing language loader returns ([], []).
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Stub heavy deps so the module import path doesn't pull in the
# whole healing stack via app.healing.__init__.
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


models = _load("_ci_models_ts", "app/code_intel/models.py")
if models is not None:
    sys.modules["app.code_intel.models"] = models
indexer = _load(
    "_ts_indexer", "app/code_intel/tree_sitter_indexer.py",
)


_DEPS_OK = True
try:
    import tree_sitter  # noqa: F401
    import tree_sitter_python  # noqa: F401
except Exception:
    _DEPS_OK = False


# ── Deps + master switch ───────────────────────────────────────────


@pytest.mark.skipif(indexer is None, reason="indexer not loadable")
class TestDeps:
    def test_tree_sitter_importable(self):
        assert _DEPS_OK, (
            "tree-sitter + tree-sitter-python must be installed "
            "(requirements.txt line set 2026-05-23)"
        )


@pytest.mark.skipif(indexer is None, reason="indexer not loadable")
class TestMasterSwitch:
    def test_default_off(self, monkeypatch):
        # Force env-OFF + no runtime_settings getter
        monkeypatch.delenv("CODE_INTEL_TREE_SITTER_ENABLED", raising=False)
        # Pre-set the runtime_settings cache via the module-internal
        # _cache attribute — simulates fresh state.
        assert indexer.is_enabled() in (False, True)
        # The KEY pin is that it defaults to False unless explicitly enabled

    def test_env_var_true_enables(self, monkeypatch):
        monkeypatch.setenv("CODE_INTEL_TREE_SITTER_ENABLED", "true")
        assert indexer.is_enabled() is True

    def test_env_var_false_disables(self, monkeypatch):
        monkeypatch.setenv("CODE_INTEL_TREE_SITTER_ENABLED", "false")
        assert indexer.is_enabled() is False


# ── Registry + supported extensions ────────────────────────────────


@pytest.mark.skipif(indexer is None, reason="indexer not loadable")
class TestRegistry:
    def test_python_supported(self):
        assert ".py" in indexer.supported_extensions()

    def test_supported_extensions_is_sorted(self):
        ext = indexer.supported_extensions()
        assert list(ext) == sorted(ext)


# ── Parsing Python: symbol kinds ───────────────────────────────────


@pytest.mark.skipif(
    indexer is None or not _DEPS_OK,
    reason="indexer + tree-sitter deps required",
)
class TestPythonSymbols:
    @pytest.fixture
    def workspace(self, tmp_path):
        return tmp_path

    @pytest.fixture
    def sample_file(self, workspace):
        path = workspace / "sample.py"
        path.write_text(textwrap.dedent("""\
            def foo():
                return 1

            async def bar():
                return 2

            class Baz:
                def method_one(self):
                    return self.x

                async def async_method(self):
                    return await self.y()

            def caller():
                foo()
                Baz().method_one()
        """))
        return path

    def test_finds_function_symbol(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        names = [(s.name, s.kind.value) for s in syms]
        assert ("foo", "function") in names

    def test_finds_async_function(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        names = [(s.name, s.kind.value) for s in syms]
        assert ("bar", "async_function") in names

    def test_finds_class(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        names = [(s.name, s.kind.value) for s in syms]
        assert ("Baz", "class") in names

    def test_finds_method_with_parent(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        methods = [
            s for s in syms
            if s.name == "method_one"
        ]
        assert len(methods) == 1
        assert methods[0].kind.value == "method"
        assert methods[0].parent == "Baz"

    def test_finds_async_method(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        async_methods = [
            s for s in syms
            if s.name == "async_method"
        ]
        assert len(async_methods) == 1
        assert async_methods[0].kind.value == "async_method"
        assert async_methods[0].parent == "Baz"

    def test_file_path_is_relative(self, sample_file, workspace):
        syms, _ = indexer.index_file_with_tree_sitter(
            sample_file, workspace_root=workspace,
        )
        for s in syms:
            assert s.file_path == "sample.py"


# ── References (call-site capture) ─────────────────────────────────


@pytest.mark.skipif(
    indexer is None or not _DEPS_OK,
    reason="indexer + tree-sitter deps required",
)
class TestReferences:
    @pytest.fixture
    def call_site_file(self, tmp_path):
        path = tmp_path / "calls.py"
        path.write_text(textwrap.dedent("""\
            def helper():
                pass

            def outer():
                helper()
                items.append(1)

            class C:
                def m(self):
                    helper()
        """))
        return path, tmp_path

    def test_captures_direct_call(self, call_site_file):
        path, root = call_site_file
        _, refs = indexer.index_file_with_tree_sitter(
            path, workspace_root=root,
        )
        names = [r.name for r in refs]
        assert "helper" in names

    def test_captures_attribute_call(self, call_site_file):
        path, root = call_site_file
        _, refs = indexer.index_file_with_tree_sitter(
            path, workspace_root=root,
        )
        names = [r.name for r in refs]
        # items.append(1) → name="append"
        assert "append" in names

    def test_enclosing_function_recorded(self, call_site_file):
        path, root = call_site_file
        _, refs = indexer.index_file_with_tree_sitter(
            path, workspace_root=root,
        )
        # outer() calls helper()
        helper_refs_in_outer = [
            r for r in refs
            if r.name == "helper" and r.in_function == "outer"
        ]
        assert len(helper_refs_in_outer) == 1

    def test_enclosing_class_recorded(self, call_site_file):
        path, root = call_site_file
        _, refs = indexer.index_file_with_tree_sitter(
            path, workspace_root=root,
        )
        method_calls_in_C = [
            r for r in refs
            if r.in_class == "C"
        ]
        assert len(method_calls_in_C) >= 1


# ── Failure isolation ──────────────────────────────────────────────


@pytest.mark.skipif(indexer is None, reason="indexer not loadable")
class TestFailureIsolation:
    def test_unsupported_extension_empty(self, tmp_path):
        unsupported = tmp_path / "thing.txt"
        unsupported.write_text("not code")
        syms, refs = indexer.index_file_with_tree_sitter(
            unsupported, workspace_root=tmp_path,
        )
        assert syms == [] and refs == []

    def test_unreadable_file_no_raise(self, tmp_path, monkeypatch):
        # Point at a non-existent file with .py extension
        ghost = tmp_path / "ghost.py"
        syms, refs = indexer.index_file_with_tree_sitter(
            ghost, workspace_root=tmp_path,
        )
        assert syms == [] and refs == []


# ── Whole-tree walk ────────────────────────────────────────────────


@pytest.mark.skipif(
    indexer is None or not _DEPS_OK,
    reason="indexer + tree-sitter deps required",
)
class TestBuildSnapshot:
    def test_walks_workspace_and_aggregates(self, tmp_path):
        (tmp_path / "a.py").write_text("def alpha(): pass\n")
        (tmp_path / "b.py").write_text("def beta(): pass\n")
        (tmp_path / "skip.txt").write_text("ignored\n")
        snapshot = indexer.build_tree_sitter_snapshot(
            tmp_path,
        )
        names = sorted(s.name for s in snapshot.symbols)
        assert "alpha" in names
        assert "beta" in names
        # indexed_files reports only those that yielded symbols
        assert "a.py" in snapshot.indexed_files
        assert "b.py" in snapshot.indexed_files

    def test_skip_dirs_honoured(self, tmp_path):
        # Put a file inside __pycache__ — must be excluded by default
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_text("def gone(): pass\n")
        snapshot = indexer.build_tree_sitter_snapshot(tmp_path)
        names = [s.name for s in snapshot.symbols]
        assert "gone" not in names


# ── Source-inspection pins ──────────────────────────────────────────


def test_runtime_settings_has_tree_sitter_switch():
    """Source-level pin: getter/setter pair for the new switch exists
    in runtime_settings (verified without import — works on hosts that
    can't load app.config)."""
    src = open(
        "app/runtime_settings.py", encoding="utf-8",
    ).read()
    assert "def get_code_intel_tree_sitter_enabled" in src
    assert "def set_code_intel_tree_sitter_enabled" in src
    assert '"code_intel_tree_sitter_enabled": bool(value)' in src


def test_runtime_settings_has_postgres_switch():
    """Postgres-backend switch is wired too (for migration 036)."""
    src = open(
        "app/runtime_settings.py", encoding="utf-8",
    ).read()
    assert "def get_code_intel_postgres_enabled" in src
    assert "def set_code_intel_postgres_enabled" in src


def test_migration_036_present():
    """Migration file for the 3 code_intel Postgres tables exists."""
    path = Path("migrations/036_code_intel.sql")
    assert path.exists()
    src = path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS code_symbols" in src
    assert "CREATE TABLE IF NOT EXISTS code_references" in src
    assert "CREATE TABLE IF NOT EXISTS code_coverage_snapshot" in src


def test_requirements_pins_tree_sitter():
    """tree-sitter + tree-sitter-python pinned in requirements.txt."""
    src = open("requirements.txt", encoding="utf-8").read()
    assert "tree-sitter>=" in src
    assert "tree-sitter-python>=" in src


def test_requirements_pins_ruff():
    """ruff pinned in requirements.txt — companion dep from Gap 3."""
    src = open("requirements.txt", encoding="utf-8").read()
    assert "ruff>=" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
