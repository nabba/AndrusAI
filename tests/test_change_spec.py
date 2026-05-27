"""Tests for the grounded change-spec builder (app/self_improvement/change_spec.py).

Pins the property that matters most: given a real file, the spec carries the
COMPLETE source, the public API surface, the external callers (blast radius),
the covering tests, and executable preservation assertions — i.e. exactly the
information the old 8KB-truncated implementer lacked when it shipped a scaffold.

Skips cleanly on a host without the app import chain (CI/Docker runs it).
"""
from __future__ import annotations

import pytest

try:
    from app.self_improvement.change_spec import (
        ChangeSpec,
        build_change_spec,
        render_for_prompt,
    )
    from app.code_intel import store as ci_store
except Exception as exc:  # pragma: no cover - host without full env
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


@pytest.fixture()
def mini_repo(tmp_path):
    """A tiny but realistic repo: a module with public + private API, an
    external caller, and a test that references the public symbol."""
    (tmp_path / "app" / "crews").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "crews" / "__init__.py").write_text("")

    (tmp_path / "app" / "crews" / "widget.py").write_text(
        "import json\n"
        "\n"
        "\n"
        "class Widget:\n"
        '    """A widget."""\n'
        "    def run(self, x):\n"
        "        return self._helper(x)\n"
        "\n"
        "    def _helper(self, x):\n"
        "        return json.dumps(x)\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return Widget()\n"
    )
    # External caller of Widget.run
    (tmp_path / "app" / "main.py").write_text(
        "from app.crews.widget import Widget\n"
        "\n"
        "\n"
        "def go():\n"
        "    return Widget().run(1)\n"
    )
    # A guarding test that references the public symbol
    (tmp_path / "tests" / "test_widget.py").write_text(
        "from app.crews.widget import Widget\n"
        "\n"
        "\n"
        "def test_widget_exists():\n"
        "    assert Widget is not None\n"
    )

    # Redirect the code_intel index to a tmp dir so we never touch the real one.
    ci_store.reset_for_tests(tmp_path / "workspace" / "code_intel")
    try:
        yield tmp_path
    finally:
        ci_store.reset_for_tests(None)


def test_spec_is_grounded_and_complete(mini_repo):
    spec = build_change_spec(
        "app/crews/widget.py", root=mini_repo, refresh=True
    )

    assert isinstance(spec, ChangeSpec)
    assert spec.target_file == "app/crews/widget.py"
    assert spec.module_path == "app.crews.widget"

    # The COMPLETE file is carried — not a truncated fragment.
    assert "def run(self, x):" in spec.full_source
    assert "def helper():" in spec.full_source
    assert spec.is_grounded


def test_public_api_surface_excludes_private(mini_repo):
    spec = build_change_spec("app/crews/widget.py", root=mini_repo, refresh=True)
    names = {s.fully_qualified for s in spec.public_symbols}

    assert "Widget" in names
    assert "Widget.run" in names
    assert "helper" in names
    # Private method must NOT be part of the preserved contract.
    assert "Widget._helper" not in names


def test_blast_radius_finds_external_caller(mini_repo):
    spec = build_change_spec("app/crews/widget.py", root=mini_repo, refresh=True)

    # Widget.run is called by go() in app/main.py — the kind of dependency the
    # old engine was blind to.
    assert "Widget.run" in spec.callers
    assert any("app/main.py:go" == site for site in spec.callers["Widget.run"])


def test_covering_tests_and_deps(mini_repo):
    spec = build_change_spec("app/crews/widget.py", root=mini_repo, refresh=True)

    assert "tests/test_widget.py" in spec.covering_tests
    assert "json" in spec.module_deps


def test_preservation_assertions_pin_the_api(mini_repo):
    spec = build_change_spec("app/crews/widget.py", root=mini_repo, refresh=True)
    blob = "\n".join(spec.preservation_assertions)

    assert "import app.crews.widget as _m" in blob
    assert "hasattr(_m, 'Widget')" in blob
    assert "hasattr(getattr(_m, 'Widget'), 'run')" in blob
    assert "hasattr(_m, 'helper')" in blob
    # The assertions are valid Python (compile cleanly).
    compile("\n".join(spec.preservation_assertions), "<assertions>", "exec")


def test_render_for_prompt_includes_full_file_and_contract(mini_repo):
    spec = build_change_spec("app/crews/widget.py", root=mini_repo, refresh=True)
    rendered = render_for_prompt(spec)

    assert "GROUNDED CONTRACT" in rendered
    assert "MUST be preserved" in rendered
    assert "MODIFY this, do NOT rewrite from scratch" in rendered
    # The complete file body is embedded.
    assert "def run(self, x):" in rendered
    assert "def helper():" in rendered


def test_missing_file_degrades_gracefully(mini_repo):
    spec = build_change_spec("app/crews/does_not_exist.py", root=mini_repo, refresh=True)
    assert spec.full_source == ""
    assert not spec.is_grounded
    assert any("unreadable" in n for n in spec.notes)
