"""Tests for app.upgrade_lifecycle.pyproject_writer (D#a)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.upgrade_lifecycle import pyproject_writer as pw


@pytest.fixture
def pyproject(tmp_path, monkeypatch):
    path = tmp_path / "pyproject.toml"
    monkeypatch.setenv("PYPROJECT_PATH", str(path))
    monkeypatch.setattr(pw, "_enabled", lambda: True)
    return path


# ── Master switch + safety envelope ─────────────────────────────────────


def test_master_switch_off(pyproject, monkeypatch):
    monkeypatch.setattr(pw, "_enabled", lambda: False)
    pyproject.write_text('[project]\ndependencies = ["starlette==0.52.1"]\n')
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "master_switch_off"


def test_untrusted_requestor(pyproject):
    pyproject.write_text('[project]\ndependencies = ["starlette==0.52.1"]\n')
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="evil", reason="test",
    )
    assert res.ok is False
    assert "requestor_not_allowed" in res.reason


def test_malformed_version(pyproject):
    pyproject.write_text('[project]\ndependencies = ["starlette==0.52.1"]\n')
    res = pw.apply_bump(
        package="starlette", to_version="$(injection)",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "malformed_version"


# ── PEP 621 [project.dependencies] ──────────────────────────────────────


def test_pep621_simple_bump(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [project]
        name = "myproj"
        dependencies = [
            "starlette==0.52.1",
            "fastapi==0.110.0",
        ]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert res.table_section == "project"
    text = pyproject.read_text()
    assert '"starlette==1.0.1"' in text
    assert "0.52.1" not in text
    assert "fastapi==0.110.0" in text     # sibling untouched


def test_pep621_preserves_extras(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette[full]==0.52.1",
        ]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    text = pyproject.read_text()
    assert '"starlette[full]==1.0.1"' in text


def test_pep621_preserves_marker(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette==0.52.1 ; python_version >= '3.11'",
        ]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    text = pyproject.read_text()
    assert "python_version >= '3.11'" in text
    assert "==1.0.1" in text


# ── PDM project (uses PEP 621 [project.dependencies] natively) ────────


def test_pdm_via_pep621_bump(pyproject):
    """PDM 2.x projects use [project.dependencies] natively, with a
    sibling [tool.pdm] section for PDM-specific config. The writer
    bumps the PEP 621 array exactly like uv projects."""
    pyproject.write_text(textwrap.dedent("""
        [project]
        name = "myproj"
        dependencies = [
            "starlette==0.52.1",
        ]

        [tool.pdm]
        python_version = ">=3.11"
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert res.table_section == "project"


# ── [tool.poetry.dependencies] ─────────────────────────────────────────


def test_poetry_simple_bump(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [tool.poetry]
        name = "myproj"

        [tool.poetry.dependencies]
        python = "^3.11"
        starlette = "^0.52.1"
        fastapi = "^0.110.0"
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert "poetry" in res.table_section
    text = pyproject.read_text()
    assert 'starlette = "1.0.1"' in text
    assert "0.52.1" not in text
    assert 'fastapi = "^0.110.0"' in text


def test_poetry_inline_table_bump(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [tool.poetry.dependencies]
        starlette = { version = "^0.52.1", extras = ["full"] }
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    text = pyproject.read_text()
    assert 'version = "1.0.1"' in text
    # Extras preserved
    assert "extras = " in text


# ── Refusals ───────────────────────────────────────────────────────────


def test_package_not_found_refused(pyproject):
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = ["fastapi==0.110.0"]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "package_not_found"


def test_ambiguous_multiple_sections_refused(pyproject):
    """Same package in both [project] and [tool.poetry] → refused.

    Note: the [project].dependencies array uses multi-line form so the
    writer's array-content scanner finds the entry. Single-line array
    syntax (``dependencies = ["x==1"]``) is currently unsupported —
    documented as a TODO since most real projects use multi-line for
    readability."""
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette==0.52.1",
        ]

        [tool.poetry.dependencies]
        starlette = "^0.52.1"
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert "ambiguous" in res.reason


# ── Lockfile hint ──────────────────────────────────────────────────────


def test_uv_lockfile_hint(pyproject, tmp_path):
    """uv.lock presence → uv lock --upgrade-package hint."""
    (tmp_path / "uv.lock").write_text("")
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette==0.52.1",
        ]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert "uv lock" in res.lockfile_hint


def test_poetry_lockfile_hint(pyproject):
    """A7-P1: poetry hint must use ``poetry update <pkg>`` — the
    previous ``poetry lock --no-update`` doesn't pick up version
    changes."""
    pyproject.write_text(textwrap.dedent("""
        [tool.poetry.dependencies]
        starlette = "^0.52.1"
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert "poetry update starlette" in res.lockfile_hint


def test_uv_hint_names_the_package(pyproject, tmp_path):
    """A7-P1: uv hint must use ``--upgrade-package <pkg>`` — bare
    ``uv sync`` respects the lock's existing pin and skips bumps."""
    (tmp_path / "uv.lock").write_text("")
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette==0.52.1",
        ]
    """).lstrip())
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert "uv lock --upgrade-package starlette" in res.lockfile_hint


# ── Audit emission ─────────────────────────────────────────────────────


def test_audit_event_payload_shape(pyproject, monkeypatch):
    pyproject.write_text(textwrap.dedent("""
        [project]
        dependencies = [
            "starlette==0.52.1",
        ]
    """).lstrip())
    emissions = []
    def _fake_record(*args, **kw):
        emissions.append({"args": args, "kw": kw})
    monkeypatch.setattr(
        "app.identity.continuity_ledger.record_event", _fake_record,
    )
    res = pw.apply_bump(
        package="starlette", to_version="1.0.1",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert len(emissions) == 1
    detail = emissions[0]["kw"]["detail"]
    assert detail["subkind"] == "pyproject_bump"
    assert detail["package"] == "starlette"
    assert detail["to_version"] == "1.0.1"
