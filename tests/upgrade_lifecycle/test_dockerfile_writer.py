"""Tests for app.upgrade_lifecycle.dockerfile_writer (P0#4).

PROGRAM §63 follow-up. Covers:

  1. Master switch OFF
  2. Disallowed requestor
  3. Malformed version
  4. Bumps tag-only Dockerfile in place
  5. Bumps SHA-pinned Dockerfile + drops the pin + adds TODO comment
  6. Preserves variant suffix (e.g. ``-slim``)
  7. Preserves AS clauses (multi-stage builds)
  8. Refuses when there's no ``FROM python:`` line
  9. Refuses when multiple ``FROM python:`` lines present
  10. baseline_mismatch when from_version disagrees
  11. already_at_version short-circuit
  12. Diff lines reported for audit
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.upgrade_lifecycle import dockerfile_writer as dw


@pytest.fixture
def dockerfile(tmp_path, monkeypatch):
    path = tmp_path / "Dockerfile"
    monkeypatch.setenv("DOCKERFILE_PATH", str(path))
    monkeypatch.setattr(dw, "_enabled", lambda: True)
    return path


# ── 1: Master switch ────────────────────────────────────────────────────


def test_master_switch_off(dockerfile, monkeypatch):
    monkeypatch.setattr(dw, "_enabled", lambda: False)
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "master_switch_off"


# ── 2: Disallowed requestor ────────────────────────────────────────────


def test_disallowed_requestor(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="evil_module", reason="test",
    )
    assert res.ok is False
    assert "requestor_not_allowed" in res.reason


# ── 3: Malformed version ───────────────────────────────────────────────


def test_malformed_version(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="$(rm -rf /)",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "malformed_version"


# ── 4: Bump tag-only Dockerfile ────────────────────────────────────────


def test_bumps_tag_only_dockerfile(dockerfile):
    dockerfile.write_text(textwrap.dedent("""
        ARG SOME_ARG=1
        FROM python:3.13-slim
        WORKDIR /app
    """).strip() + "\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="EOL approaching",
    )
    assert res.ok is True
    assert res.old_version == "3.13"
    assert res.new_version == "3.14"
    assert res.sha_pin_dropped is False
    text = dockerfile.read_text()
    assert "FROM python:3.14-slim" in text
    assert "FROM python:3.13" not in text
    # Surrounding lines untouched
    assert "ARG SOME_ARG=1" in text
    assert "WORKDIR /app" in text


# ── 5: SHA-pinned Dockerfile ───────────────────────────────────────────


def test_bumps_sha_pinned_dockerfile_drops_pin(dockerfile):
    sha = "d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664"
    dockerfile.write_text(
        f"FROM python:3.13-slim@sha256:{sha}\nWORKDIR /app\n"
    )
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="EOL",
    )
    assert res.ok is True
    assert res.sha_pin_dropped is True
    text = dockerfile.read_text()
    assert "FROM python:3.14-slim" in text
    # Old digest gone from the FROM line itself
    for line in text.splitlines():
        if line.startswith("FROM python:"):
            assert "sha256" not in line
    # TODO comment added (operator must re-pin)
    assert "TODO" in text
    assert "re-pin" in text.lower()


# ── 6: Preserves variant suffix ────────────────────────────────────────


def test_preserves_variant_suffix(dockerfile):
    dockerfile.write_text("FROM python:3.13-bookworm\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert "FROM python:3.14-bookworm" in dockerfile.read_text()


# ── 7: AS clauses (multi-stage) ────────────────────────────────────────


def test_preserves_as_clause(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim AS builder\nRUN echo hi\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    text = dockerfile.read_text()
    assert "FROM python:3.14-slim" in text
    # AS keyword should still be present (case-preserved)
    assert "AS builder" in text or "as builder" in text


# ── 8: No FROM python: line ────────────────────────────────────────────


def test_refuses_when_no_python_line(dockerfile):
    dockerfile.write_text("FROM alpine:latest\nWORKDIR /app\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert res.reason == "no_python_from_line"


# ── 9 (D#b): Multi-stage Dockerfile support ───────────────────────────


def test_multi_stage_same_version_bumps_in_lockstep(dockerfile):
    """Multi-stage build with SAME Python version → both FROM lines bumped."""
    dockerfile.write_text(
        "FROM python:3.13-slim AS builder\n"
        "RUN pip wheel\n"
        "FROM python:3.13-slim AS runtime\n"
    )
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    text = dockerfile.read_text()
    assert text.count("FROM python:3.14-slim") == 2
    assert "3.13" not in text


def test_multi_stage_different_versions_refused(dockerfile):
    """Multi-stage build with DIFFERENT Python versions → refused."""
    dockerfile.write_text(
        "FROM python:3.13-slim AS builder\n"
        "RUN pip wheel\n"
        "FROM python:3.11-slim AS runtime\n"
    )
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert "multiple_python_from_lines_different_versions" in res.reason
    assert "3.11" in res.reason
    assert "3.13" in res.reason
    # File untouched
    text = dockerfile.read_text()
    assert "FROM python:3.13" in text
    assert "FROM python:3.11" in text


# ── 10: Baseline mismatch ──────────────────────────────────────────────


def test_baseline_mismatch_refuses(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="3.14",
        from_version="3.11",   # actual file has 3.13
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is False
    assert "baseline_mismatch" in res.reason


def test_baseline_match_succeeds(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="3.14", from_version="3.13",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True


# ── 11: already_at_version short-circuit ───────────────────────────────


def test_already_at_version_returns_ok_without_writing(dockerfile):
    """Trying to bump 3.13 → 3.13 succeeds idempotently."""
    dockerfile.write_text("FROM python:3.13-slim\n")
    original = dockerfile.read_text()
    res = dw.apply_bump(
        to_version="3.13",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert res.reason == "already_at_version"
    # File contents identical
    assert dockerfile.read_text() == original


# ── 12: Diff lines reported ────────────────────────────────────────────


def test_diff_lines_reported(dockerfile):
    dockerfile.write_text("FROM python:3.13-slim\n")
    res = dw.apply_bump(
        to_version="3.14",
        requestor="upgrade_lifecycle", reason="test",
    )
    assert res.ok is True
    assert any("3.13" in d for d in res.diff_lines)
    assert any("3.14" in d for d in res.diff_lines)
