"""Gap 4 — decade_recall tests.

Covers:
  * scan_all_sources walks every source from offset 0 → EOF
  * Cursor persists across scans (idempotent re-scan = no-op)
  * Truncated/rotated source resets cursor to 0
  * PII redaction strips email + phone BEFORE tokenization
  * recall_history filters by scope
  * recall_history filters by kind
  * recall_history honors window_years cutoff
  * recall_history newest-first ordering
  * summary() per-scope per-year counts
  * Master switch off → recall_history returns [] + scan returns skip
  * Vendor-rotation tolerance: no embedding-model dependency in
    the indexer or retrieval module
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# decade_recall itself is stdlib-only, but the runtime_settings module
# (which the enabled() helpers consult) imports pydantic_settings via
# app.config. Skip the suite on hosts without pydantic_settings.
pytest.importorskip("pydantic_settings")


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    from app import paths as _paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", workspace)
    return workspace


def _write_source(workspace: Path, rel_path: str, rows: list[dict]) -> Path:
    p = workspace / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_master_switch_off_returns_skip(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval

    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: False
    )
    assert indexer.scan_all_sources()["skipped_reason"] == "master_switch_off"
    assert retrieval.recall_history("anything") == []


def test_scan_indexes_every_source(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    now = datetime.now(timezone.utc)
    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": now.isoformat(), "kind": "substrate_migration", "summary": "host moved foo"},
            {"ts": now.isoformat(), "kind": "annual_reflection", "summary": "2026 essay"},
        ],
    )
    _write_source(
        isolated_workspace,
        "change_requests/audit.jsonl",
        [
            {"ts": now.isoformat(), "event": "cr_created", "cr_id": "abc123"},
            {"ts": now.isoformat(), "event": "cr_applied", "cr_id": "abc123"},
        ],
    )

    out = indexer.scan_all_sources()
    assert out["total_indexed"] == 4
    assert out["sources"]["continuity"]["indexed"] == 2
    assert out["sources"]["changes"]["indexed"] == 2


def test_cursor_idempotent_rescan(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "x", "summary": "one"},
            {"ts": "2026-01-02T00:00:00+00:00", "kind": "y", "summary": "two"},
        ],
    )
    indexer.scan_all_sources()
    second = indexer.scan_all_sources()
    # Second pass: no new lines, cursor unchanged
    assert second["total_indexed"] == 0
    assert second["sources"]["continuity"]["lines_processed"] == 0


def test_truncation_resets_cursor(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    src = _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "old", "summary": "old"},
        ],
    )
    indexer.scan_all_sources()

    # Truncate + replace
    src.write_text(
        json.dumps(
            {"ts": "2026-05-01T00:00:00+00:00", "kind": "fresh", "summary": "fresh"}
        )
        + "\n"
    )
    out = indexer.scan_all_sources()
    assert out["sources"]["continuity"]["indexed"] == 1


def test_pii_redaction_at_indexing(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {
                "ts": "2026-05-24T00:00:00+00:00",
                "kind": "manual",
                "summary": "operator email is andrus@example.com and phone +358501234567",
            }
        ],
    )
    indexer.scan_all_sources()
    idx_path = isolated_workspace / "decade_recall" / "index.jsonl"
    body = idx_path.read_text()
    assert "andrus@example.com" not in body
    assert "+358501234567" not in body
    assert "<email>" in body
    assert "<phone>" in body


def test_recall_history_filters_by_scope(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    now = datetime.now(timezone.utc).isoformat()
    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [{"ts": now, "kind": "x", "summary": "kaicart customer launch"}],
    )
    _write_source(
        isolated_workspace,
        "change_requests/audit.jsonl",
        [{"ts": now, "event": "cr_applied", "summary": "kaicart pricing fix"}],
    )
    indexer.scan_all_sources()

    # All scopes
    all_results = retrieval.recall_history("kaicart")
    assert len(all_results) == 2

    # Only continuity
    cont_results = retrieval.recall_history("kaicart", scopes=["continuity"])
    assert len(cont_results) == 1
    assert cont_results[0].scope == "continuity"


def test_recall_history_filters_by_kind(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    now = datetime.now(timezone.utc).isoformat()
    _write_source(
        isolated_workspace,
        "change_requests/audit.jsonl",
        [
            {"ts": now, "event": "cr_created", "summary": "kaicart"},
            {"ts": now, "event": "cr_applied", "summary": "kaicart"},
        ],
    )
    indexer.scan_all_sources()
    applied = retrieval.recall_history("kaicart", kinds={"cr_applied"})
    assert len(applied) == 1
    assert applied[0].kind == "cr_applied"


def test_recall_history_honors_window_years(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    very_old = "2015-01-01T00:00:00+00:00"
    recent = datetime.now(timezone.utc).isoformat()
    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": very_old, "kind": "x", "summary": "kaicart from 2015"},
            {"ts": recent, "kind": "y", "summary": "kaicart from today"},
        ],
    )
    indexer.scan_all_sources()
    results = retrieval.recall_history("kaicart", window_years=5)
    # 2015 falls outside the 5-year window
    assert len(results) == 1
    assert "today" in results[0].preview


def test_recall_history_newest_first(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "a", "summary": "kaicart"},
            {"ts": "2026-05-01T00:00:00+00:00", "kind": "b", "summary": "kaicart"},
            {"ts": "2026-03-01T00:00:00+00:00", "kind": "c", "summary": "kaicart"},
        ],
    )
    indexer.scan_all_sources()
    results = retrieval.recall_history("kaicart")
    assert [r.ts for r in results] == [
        "2026-05-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
    ]


def test_summary_per_scope_per_year(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [
            {"ts": "2025-01-01T00:00:00+00:00", "kind": "x", "summary": "row1"},
            {"ts": "2025-05-01T00:00:00+00:00", "kind": "y", "summary": "row2"},
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "z", "summary": "row3"},
        ],
    )
    _write_source(
        isolated_workspace,
        "change_requests/audit.jsonl",
        [{"ts": "2026-01-15T00:00:00+00:00", "event": "cr", "summary": "row4"}],
    )
    indexer.scan_all_sources()
    s = retrieval.summary()
    assert s["total"] == 4
    assert s["by_scope_year"]["continuity"]["2025"] == 2
    assert s["by_scope_year"]["continuity"]["2026"] == 1
    assert s["by_scope_year"]["changes"]["2026"] == 1


def test_rebuild_index_wipes_and_rescans(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [{"ts": "2026-05-01T00:00:00+00:00", "kind": "x", "summary": "row1"}],
    )
    indexer.scan_all_sources()

    out = indexer.rebuild_index()
    assert out["total_indexed"] == 1
    # The cursor should have been reset + advanced
    cursors_path = isolated_workspace / "decade_recall" / "cursors.json"
    assert cursors_path.exists()


def test_recall_history_returns_empty_for_unknown_query(monkeypatch, isolated_workspace):
    from app.decade_recall import indexer, retrieval
    monkeypatch.setattr(
        "app.runtime_settings.get_decade_recall_enabled", lambda: True
    )

    _write_source(
        isolated_workspace,
        "identity/continuity_ledger.jsonl",
        [{"ts": "2026-05-01T00:00:00+00:00", "kind": "x", "summary": "kaicart"}],
    )
    indexer.scan_all_sources()
    assert retrieval.recall_history("zzzzzz_nonexistent_term") == []


def test_no_embedding_model_dependency():
    """Goodhart guard: decade_recall MUST NOT depend on any LLM or
    embedding model. That's the load-bearing design choice — the
    index survives every Sonnet 4.5 → 5.0 → 6 transition without
    re-baselining."""
    from app.decade_recall import indexer, retrieval

    indexer_src = open(indexer.__file__).read()
    retrieval_src = open(retrieval.__file__).read()
    for forbidden in (
        "openai",
        "anthropic",
        "ollama",
        "chromadb",
        "sentence_transformers",
        "embedding",
    ):
        # Allow the word "embedding" in comments/docstrings — only forbid
        # actual imports/calls.
        for src_name, src in [("indexer", indexer_src), ("retrieval", retrieval_src)]:
            assert (
                f"from {forbidden}" not in src and f"import {forbidden}" not in src
            ), (
                f"decade_recall.{src_name} must not depend on {forbidden!r} — "
                f"breaks vendor-rotation tolerance"
            )


def test_master_switch_default_on():
    """Default ON because observational + cheap. Operator opts OUT
    via runtime_settings if needed."""
    from app import runtime_settings

    assert runtime_settings.get_decade_recall_enabled() is True


def test_six_sources_registered():
    """The full source list must be discoverable from the module."""
    from app.decade_recall import indexer

    assert len(indexer.SOURCES) == 6
    scopes = {s.scope for s in indexer.SOURCES}
    assert scopes == {
        "continuity",
        "changes",
        "drills",
        "executor",
        "agreement",
        "governance",
    }
