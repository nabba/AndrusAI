"""Tests for app.privacy.aggregator — Gap #7 unified subject audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.privacy import aggregator as agg  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(agg, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(agg, "_enabled", lambda: True)
    return tmp_path


def test_subject_type_validation() -> None:
    with pytest.raises(ValueError):
        agg.audit_subject("nonsense", "x")


def test_subject_id_validation() -> None:
    with pytest.raises(ValueError):
        agg.audit_subject("person", "")


def test_audit_empty_workspace_returns_zero(_tmp_workspace: Path) -> None:
    result = agg.audit_subject("person", "ghost@example.com")
    assert result["total_references"] == 0
    assert result["adapters"]  # adapters still listed; just zero references


def test_audit_skipped_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(agg, "_enabled", lambda: False)
    result = agg.audit_subject("person", "x@example.com")
    assert result["enabled"] is False
    assert result["total_references"] == 0


def test_audit_browse_domain_references(_tmp_workspace: Path) -> None:
    events_dir = _tmp_workspace / "browse" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "2026-05-24.jsonl").write_text("\n".join([
        json.dumps({"url": "https://example.com/a", "ts": 100}),
        json.dumps({"url": "https://example.com/b", "ts": 200}),
        json.dumps({"url": "https://other.test/", "ts": 300}),
    ]) + "\n")
    result = agg.audit_subject("domain", "example.com")
    browse = next(a for a in result["adapters"] if a["adapter"] == "browse")
    assert browse["n_references"] == 2
    assert result["total_references"] == 2


def test_audit_audit_log_sender_references(_tmp_workspace: Path) -> None:
    audit_path = _tmp_workspace / "audit.log"
    audit_path.write_text("\n".join([
        json.dumps({"event": "request_received", "sender_id": "alice@example.com", "ts": "2026-05-20T12:00:00Z"}),
        json.dumps({"event": "request_received", "sender_id": "bob@example.com", "ts": "2026-05-20T13:00:00Z"}),
        json.dumps({"event": "request_received", "sender_id": "alice@example.com", "ts": "2026-05-21T08:00:00Z"}),
    ]) + "\n")
    result = agg.audit_subject("person", "alice@example.com")
    audit = next(a for a in result["adapters"] if a["adapter"] == "audit_log")
    assert audit["n_references"] == 2


def test_audit_audit_log_excludes_old_rows(_tmp_workspace: Path, monkeypatch) -> None:
    """Rows older than 365d are excluded — keeps the probe bounded."""
    audit_path = _tmp_workspace / "audit.log"
    audit_path.write_text(
        json.dumps({"event": "request_received", "sender_id": "ancient", "ts": "2020-01-01T00:00:00Z"})
        + "\n"
    )
    result = agg.audit_subject("sender_id", "ancient")
    audit = next(a for a in result["adapters"] if a["adapter"] == "audit_log")
    assert audit["n_references"] == 0


def test_forget_requires_exact_phrase() -> None:
    with pytest.raises(ValueError):
        agg.forget_subject("person", "alice@example.com", confirm_phrase="forget alice@example.com")


def test_forget_with_correct_phrase_runs(_tmp_workspace: Path, monkeypatch) -> None:
    """A correct phrase invokes every relevant adapter's forget path.
    We patch the person_model forget to confirm it was called."""
    called = {}

    def fake_forget(person_id: str, path=None) -> bool:
        called["person_model"] = person_id
        return True

    monkeypatch.setattr("app.companion.person_model.forget", fake_forget)
    result = agg.forget_subject(
        "person", "alice@example.com",
        confirm_phrase="FORGET person:alice@example.com",
    )
    assert result["enabled"] is True
    pm = next(r for r in result["results"] if r["adapter"] == "person_model")
    assert pm["n_removed"] == 1
    assert called == {"person_model": "alice@example.com"}


def test_forget_audit_log_returns_explanatory_error(_tmp_workspace: Path, monkeypatch) -> None:
    """audit.log is append-only; the adapter must surface this as an
    error rather than pretending the forget happened."""
    result = agg.forget_subject(
        "sender_id", "alice",
        confirm_phrase="FORGET sender_id:alice",
    )
    audit_row = next(r for r in result["results"] if r["adapter"] == "audit_log")
    assert audit_row["n_removed"] == 0
    assert audit_row["error"] is not None
    assert "append-only" in audit_row["error"]


def test_audit_browse_does_not_match_unrelated_domain(_tmp_workspace: Path) -> None:
    events_dir = _tmp_workspace / "browse" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "2026-05-24.jsonl").write_text(
        json.dumps({"url": "https://example.com/", "ts": 1}) + "\n"
    )
    result = agg.audit_subject("domain", "missing.org")
    browse = next(a for a in result["adapters"] if a["adapter"] == "browse")
    assert browse["n_references"] == 0


def test_audit_only_walks_supported_adapters(_tmp_workspace: Path) -> None:
    """A subject_type=domain shouldn't walk person_model."""
    events_dir = _tmp_workspace / "browse" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "2026-05-24.jsonl").write_text("")
    result = agg.audit_subject("domain", "example.com")
    names = {a["adapter"] for a in result["adapters"]}
    assert "person_model" not in names
    assert "browse" in names


def test_forget_only_walks_supported_adapters() -> None:
    result = agg.forget_subject(
        "domain", "example.com",
        confirm_phrase="FORGET domain:example.com",
    )
    names = {r["adapter"] for r in result["results"]}
    assert "person_model" not in names
    assert "browse" in names


def test_corrupt_audit_lines_do_not_crash(_tmp_workspace: Path) -> None:
    audit_path = _tmp_workspace / "audit.log"
    audit_path.write_text(
        "not json\n" +
        json.dumps({"event": "request_received", "sender_id": "x", "ts": "2026-05-20T00:00:00Z"})
        + "\n"
    )
    result = agg.audit_subject("sender_id", "x")
    audit = next(a for a in result["adapters"] if a["adapter"] == "audit_log")
    assert audit["n_references"] == 1
