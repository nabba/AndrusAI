"""Regression tests for the SubIA-audit 2026-05-23 ledger fixes.

Pins:
  * ``ecosystem_snapshot``, ``chromadb_corruption``, and
    ``capability_regression`` are registered in
    ``IDENTITY_EVENT_KINDS``.
  * The four production call sites that used to import nonexistent
    ``emit_event`` / ``append_event`` now use ``record_event`` and a
    plain ``record_event(kind=K, actor=A, summary=S, detail=D)``
    call succeeds.

A future refactor that drops a kind from the frozenset or renames
``record_event`` will fail these tests instead of silently dropping
emissions under the producers' try/except.
"""
from __future__ import annotations

import pathlib

import pytest


# ── Kind-registration pins ───────────────────────────────────────────


_NEW_KINDS_2026_05_23 = (
    "ecosystem_snapshot",
    "chromadb_corruption",
    "capability_regression",
)


def test_new_kinds_registered() -> None:
    from app.identity.continuity_ledger import IDENTITY_EVENT_KINDS

    for kind in _NEW_KINDS_2026_05_23:
        assert kind in IDENTITY_EVENT_KINDS, (
            f"{kind!r} must be in IDENTITY_EVENT_KINDS — its producer "
            f"wraps record_event in try/except, so an unregistered "
            f"kind silently drops the emission."
        )


# ── Producer end-to-end smoke tests ──────────────────────────────────


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """Redirect the ledger file to a tmp path so the test never touches
    the live ledger. Returns the path so we can read it back."""
    from app.identity import continuity_ledger as cl

    p = tmp_path / "ledger.jsonl"
    cl._reset_for_tests(p)
    yield p
    cl._reset_for_tests(None)


def _read_kinds(path: pathlib.Path) -> list[str]:
    import json

    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line)["kind"])
        except Exception:
            continue
    return out


def test_chromadb_integrity_emits_chromadb_corruption(isolated_ledger):
    from app.memory.chromadb_integrity import _emit_ledger_event

    _emit_ledger_event(
        kind="chromadb_corruption",
        summary="quarantined memory (PRAGMA integrity_check failed)",
        detail={"kb_name": "memory", "reason": "integrity_check_failed"},
        actor="chromadb_integrity.test",
    )

    kinds = _read_kinds(isolated_ledger)
    assert "chromadb_corruption" in kinds


def test_ecosystem_snapshot_emits_ecosystem_snapshot(isolated_ledger):
    from app.upgrade_lifecycle.ecosystem_snapshot import (
        EcosystemSnapshot,
        _emit_ledger_event,
    )

    snap = EcosystemSnapshot(
        year=2027,
        generated_at="2027-01-01T00:00:00Z",
        python_eol={"days_until_eol": 365},
    )
    _emit_ledger_event(snap, kind="ecosystem_snapshot", subkind="annual")

    kinds = _read_kinds(isolated_ledger)
    assert "ecosystem_snapshot" in kinds


def test_requirements_writer_emits_ecosystem_snapshot(isolated_ledger):
    from app.upgrade_lifecycle.requirements_writer import _emit_audit

    _emit_audit(
        package="anthropic",
        to_version="1.2.3",
        requestor="test",
        reason="patch bump",
        diff=("+ anthropic==1.2.3\n", "- anthropic==1.2.2\n"),
    )

    kinds = _read_kinds(isolated_ledger)
    assert "ecosystem_snapshot" in kinds


def test_capability_regression_emits_capability_regression(isolated_ledger):
    from app.capability_regression.scheduler_job import _maybe_emit_landmark

    class _Stub:
        has_regression = True
        tools_deleted = ["tool_a", "tool_b"]
        models_truly_deleted = []
        prev_captured_at = "2026-05-01T00:00:00Z"
        curr_captured_at = "2026-05-23T00:00:00Z"

    _maybe_emit_landmark(_Stub())  # type: ignore[arg-type]

    kinds = _read_kinds(isolated_ledger)
    assert "capability_regression" in kinds


def test_no_regression_skip_when_no_regression(isolated_ledger):
    """The producer must NOT emit when has_regression=False — pin so the
    fix doesn't accidentally invert the guard."""
    from app.capability_regression.scheduler_job import _maybe_emit_landmark

    class _Stub:
        has_regression = False
        tools_deleted: list[str] = []
        models_truly_deleted: list[str] = []
        prev_captured_at = ""
        curr_captured_at = ""

    _maybe_emit_landmark(_Stub())  # type: ignore[arg-type]

    kinds = _read_kinds(isolated_ledger)
    assert "capability_regression" not in kinds


# ── API-name pins ────────────────────────────────────────────────────


def test_continuity_ledger_record_event_exists() -> None:
    """Pin the public function name. The four producers fixed
    2026-05-23 had used ``emit_event`` / ``append_event`` which never
    existed. A future rename would re-introduce that class of bug."""
    from app.identity import continuity_ledger as cl

    assert hasattr(cl, "record_event")
    assert not hasattr(cl, "emit_event"), (
        "emit_event must NOT be re-introduced — that name was used by "
        "the four broken sites fixed 2026-05-23; resurrecting it would "
        "invite the same bug class."
    )
    assert not hasattr(cl, "append_event"), (
        "append_event must NOT be re-introduced — same rationale as "
        "emit_event."
    )
