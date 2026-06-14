"""Tests for Markdown / plain-text attachment extraction.

Origin: the Signal attachment pipeline (forwarder → /signal/inbound →
handle_task → commander.handle → _process_attachments → extract_attachment)
was already fully wired, and the router's _RESEARCH_ATTACHMENT_TYPES already
listed ``text/markdown`` + ``text/plain`` as first-class attachment types —
but ``attachment_reader.extract_attachment`` had no extractor for them, so an
attached ``.md`` document returned "Unsupported file type." instead of its
contents. These tests pin the text-family extractor that closes that gap.
"""
from __future__ import annotations

import pytest

from app.tools import attachment_reader as ar


@pytest.fixture
def attach_dir(tmp_path, monkeypatch):
    """Redirect the module's attachment storage dir to a scratch path."""
    monkeypatch.setattr(ar, "ATTACHMENTS_DIR", tmp_path)
    return tmp_path


# ── Core request: an attached .md document is read into context ──────────

def test_md_by_content_type_returned_verbatim(attach_dir):
    (attach_dir / "notes.md").write_text(
        "# Title\n\n- bullet\n\nBody **bold**.\n", encoding="utf-8"
    )
    out = ar.extract_attachment("notes.md", "text/markdown")
    # Markdown is returned verbatim — the agent gets the real document.
    assert "# Title" in out
    assert "**bold**" in out
    assert "Unsupported" not in out


def test_md_by_extension_when_content_type_missing(attach_dir):
    (attach_dir / "notes.md").write_text("# Heading\n", encoding="utf-8")
    out = ar.extract_attachment("notes.md", "")
    assert "# Heading" in out


def test_md_content_type_with_charset_param(attach_dir):
    (attach_dir / "notes.md").write_text("# Heading\n", encoding="utf-8")
    out = ar.extract_attachment("notes.md", "text/markdown; charset=utf-8")
    assert "# Heading" in out


def test_bare_id_filename_with_ctype_resolves_suffix(attach_dir):
    """signal-cli stores <id>.md but the envelope filename may be the bare id."""
    (attach_dir / "id777.md").write_text("# stored as id+suffix\n", encoding="utf-8")
    out = ar.extract_attachment("id777", "text/markdown")
    assert "stored as id+suffix" in out


def test_plain_txt(attach_dir):
    (attach_dir / "log.txt").write_text("plain text line\n", encoding="utf-8")
    out = ar.extract_attachment("log.txt", "text/plain")
    assert "plain text line" in out


# ── Robustness ───────────────────────────────────────────────────────────

def test_utf8_bom_is_stripped(attach_dir):
    (attach_dir / "bom.md").write_bytes(b"\xef\xbb\xbf# WithBOM\n")
    out = ar.extract_attachment("bom.md", "text/markdown")
    assert out.startswith("# WithBOM")


def test_empty_file_reports_empty(attach_dir):
    (attach_dir / "empty.md").write_text("", encoding="utf-8")
    out = ar.extract_attachment("empty.md", "text/markdown")
    assert "empty" in out.lower()


def test_oversized_file_is_truncated(attach_dir):
    (attach_dir / "big.md").write_text("A" * (ar._MAX_EXTRACT_CHARS + 500), encoding="utf-8")
    out = ar.extract_attachment("big.md", "text/markdown")
    assert "truncated at" in out
    assert len(out) <= ar._MAX_EXTRACT_CHARS + 80


def test_non_utf8_bytes_degrade_gracefully(attach_dir):
    (attach_dir / "bin.md").write_bytes(b"# ok \xff\xfe not utf8\n")
    out = ar.extract_attachment("bin.md", "text/markdown")
    assert "# ok" in out  # replacement chars, no exception


# ── Guards stay intact ───────────────────────────────────────────────────

def test_unknown_type_still_unsupported(attach_dir):
    (attach_dir / "thing.xyz").write_text("data", encoding="utf-8")
    out = ar.extract_attachment("thing.xyz", "application/x-weird")
    assert "Unsupported" in out


def test_path_traversal_blocked():
    out = ar.extract_attachment("../../etc/passwd", "text/plain")
    assert "not found" in out.lower() or "Unsupported" in out


# ── Dispatch registration ────────────────────────────────────────────────

def test_text_family_registered_in_dispatch_maps():
    for mime in ("text/markdown", "text/x-markdown", "text/plain"):
        assert ar._EXTRACTORS.get(mime) is ar.extract_text
    for ext in (".md", ".markdown", ".txt"):
        assert ar._EXT_MAP.get(ext) is ar.extract_text
