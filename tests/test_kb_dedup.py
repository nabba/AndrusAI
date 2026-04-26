"""Tests for the 2026-04-26 duplicate-detection on KB uploads.

Goal: re-uploading the same document is never a silent re-ingest. The
backend returns 409 Conflict with structured detail, and callers
either honor the rejection or re-submit with overwrite=true.

Coverage:
  * compute_content_hash — stable, deterministic
  * find_duplicate — filename match (filesystem + chromadb), hash
    match, no-match, and graceful failure on bad inputs
  * Each upload handler exposes an ``overwrite`` parameter
  * Each handler imports + uses the dedup helper before ingesting
  * Frontend uploadFormData throws UploadError on 409 with parsed detail
  * Frontend FileUploadZone shows the duplicate prompt + replace button
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim
install_settings_shim()

from app.api.kb_dedup import (
    DuplicateMatch,
    compute_content_hash,
    find_duplicate,
    hash_for_metadata,
)


REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# Hash helpers
# ══════════════════════════════════════════════════════════════════════

class TestComputeContentHash:

    def test_empty_bytes_known_digest(self):
        # SHA-256 of empty input is a famous fixed value
        assert compute_content_hash(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_deterministic(self):
        h1 = compute_content_hash(b"hello world")
        h2 = compute_content_hash(b"hello world")
        assert h1 == h2

    def test_distinct_inputs_distinct_hashes(self):
        assert compute_content_hash(b"a") != compute_content_hash(b"b")

    def test_hex_output(self):
        h = compute_content_hash(b"x")
        assert re.match(r"^[0-9a-f]{64}$", h), "must be 64-char hex"


class TestHashForMetadata:

    def test_returns_dict_with_content_hash_key(self):
        out = hash_for_metadata(b"hello")
        assert "content_hash" in out
        assert out["content_hash"] == compute_content_hash(b"hello")


# ══════════════════════════════════════════════════════════════════════
# find_duplicate — filesystem layer
# ══════════════════════════════════════════════════════════════════════

class TestFindDuplicateFilesystem:

    def test_no_existing_file_no_dup(self, tmp_path):
        out = find_duplicate(
            new_content=b"abc",
            new_filename="doc.md",
            existing_files_dir=tmp_path,
        )
        assert out is None

    def test_existing_file_same_content_matches_by_hash(self, tmp_path):
        (tmp_path / "doc.md").write_bytes(b"abc")
        out = find_duplicate(
            new_content=b"abc",
            new_filename="doc.md",
            existing_files_dir=tmp_path,
        )
        assert isinstance(out, DuplicateMatch)
        assert out.matched_by == "content_hash"
        assert out.existing_filename == "doc.md"
        assert out.added_at is not None  # filesystem mtime

    def test_existing_file_different_content_matches_by_filename(self, tmp_path):
        (tmp_path / "doc.md").write_bytes(b"old content")
        out = find_duplicate(
            new_content=b"new content",
            new_filename="doc.md",
            existing_files_dir=tmp_path,
        )
        assert isinstance(out, DuplicateMatch)
        assert out.matched_by == "filename"

    def test_dir_does_not_exist_silently_no_dup(self, tmp_path):
        # tmp_path/missing/ doesn't exist — must not raise
        out = find_duplicate(
            new_content=b"abc",
            new_filename="doc.md",
            existing_files_dir=tmp_path / "missing",
        )
        assert out is None


# ══════════════════════════════════════════════════════════════════════
# find_duplicate — ChromaDB layer (mocked)
# ══════════════════════════════════════════════════════════════════════

class TestFindDuplicateCollection:
    """The collection.get(where=...) interface is mocked so tests don't
    need a live ChromaDB. The contract we exercise: filename lookup
    first, then content-hash lookup; both return DuplicateMatch with
    the right ``matched_by`` field."""

    def test_filename_match_returns_dup(self):
        col = MagicMock()
        col.get.return_value = {
            "metadatas": [{
                "source_file": "Foo.md",
                "ingested_at": "2026-04-20T12:00:00+00:00",
            }]
        }
        out = find_duplicate(
            new_content=b"unrelated",
            new_filename="Foo.md",
            collection=col,
            filename_meta_key="source_file",
        )
        assert isinstance(out, DuplicateMatch)
        assert out.matched_by == "filename"
        assert out.existing_filename == "Foo.md"
        assert out.added_at == "2026-04-20T12:00:00+00:00"

    def test_hash_match_when_filename_misses(self):
        h = compute_content_hash(b"hello")
        col = MagicMock()
        # First call (filename lookup) → no hit; second (hash) → hit.
        col.get.side_effect = [
            {"metadatas": []},
            {"metadatas": [{
                "source_file": "old_name.md",
                "content_hash": h,
                "ingested_at": "2026-04-21T10:00:00+00:00",
            }]},
        ]
        out = find_duplicate(
            new_content=b"hello",
            new_filename="renamed.md",
            collection=col,
            filename_meta_key="source_file",
        )
        assert isinstance(out, DuplicateMatch)
        assert out.matched_by == "content_hash"
        assert out.existing_filename == "old_name.md"

    def test_no_match_returns_none(self):
        col = MagicMock()
        col.get.return_value = {"metadatas": []}
        out = find_duplicate(
            new_content=b"x",
            new_filename="never_seen.md",
            collection=col,
            filename_meta_key="source_file",
        )
        assert out is None

    def test_collection_exception_swallowed(self):
        """Dedup is best-effort. If ChromaDB throws, we MUST NOT block
        the upload — it falls through to "no duplicate"."""
        col = MagicMock()
        col.get.side_effect = RuntimeError("chroma down")
        out = find_duplicate(
            new_content=b"x",
            new_filename="doc.md",
            collection=col,
        )
        assert out is None


class TestDuplicateMatchAsDetail:

    def test_serializable_for_409_response(self):
        d = DuplicateMatch(
            matched_by="filename",
            existing_filename="abc.md",
            added_at="2026-04-26T00:00:00Z",
            hint="overwrite=true to replace",
        )
        out = d.as_detail()
        assert out["error"] == "duplicate"
        assert out["matched_by"] == "filename"
        assert out["existing_filename"] == "abc.md"
        assert "hint" in out


# ══════════════════════════════════════════════════════════════════════
# Wiring — every file-upload handler accepts overwrite + uses the helper
# ══════════════════════════════════════════════════════════════════════

class TestUploadHandlersHaveOverwriteParam:

    @pytest.mark.parametrize("path", [
        "app/api/kb.py",
        "app/api/fiction.py",
        "app/philosophy/api.py",
        "app/episteme/api.py",
    ])
    def test_overwrite_param_declared(self, path):
        text = (REPO / path).read_text()
        # Every upload handler must accept overwrite as a parameter so
        # the frontend's "Replace" button can opt in.
        assert "overwrite: bool = False" in text or "overwrite: bool=False" in text, (
            f"{path}: upload handler must declare ``overwrite: bool = False`` "
            f"so the dashboard can re-submit duplicates with overwrite=true."
        )

    @pytest.mark.parametrize("path", [
        "app/api/kb.py",
        "app/api/fiction.py",
        "app/philosophy/api.py",
        "app/episteme/api.py",
    ])
    def test_imports_dedup_helper(self, path):
        text = (REPO / path).read_text()
        assert "from app.api.kb_dedup import find_duplicate" in text, (
            f"{path}: must import find_duplicate from app.api.kb_dedup."
        )

    @pytest.mark.parametrize("path", [
        "app/api/kb.py",
        "app/api/fiction.py",
        "app/philosophy/api.py",
        "app/episteme/api.py",
    ])
    def test_returns_409_on_duplicate(self, path):
        text = (REPO / path).read_text()
        # Either via raise HTTPException(status_code=409, ...) or
        # raise HTTPException(409, ...). episteme uses a return-dict
        # protocol because it loops; the wrapper raises 409 there.
        has_409 = (
            "status_code=409" in text
            or "HTTPException(409" in text
            or 'raise HTTPException(409' in text
        )
        assert has_409, (
            f"{path}: must raise 409 Conflict when find_duplicate returns "
            f"a match (and overwrite is not set)."
        )


# ══════════════════════════════════════════════════════════════════════
# Frontend — UploadError + duplicate-prompt UI
# ══════════════════════════════════════════════════════════════════════

class TestFrontendDuplicatePrompt:

    def test_upload_error_class_present(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KnowledgeBases.tsx"
        ).read_text()
        assert "class UploadError" in text, (
            "KnowledgeBases.tsx must export a custom UploadError that "
            "carries the parsed 409 detail to the upload zone."
        )

    def test_409_handler_path(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KnowledgeBases.tsx"
        ).read_text()
        assert "err.status === 409" in text, (
            "Upload zone must branch on status === 409 to show the "
            "duplicate-detected prompt instead of a raw error string."
        )

    def test_replace_button_passes_overwrite(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KnowledgeBases.tsx"
        ).read_text()
        # When user clicks Replace we re-call doUpload with overwrite=true,
        # which appends overwrite=true to the FormData.
        assert "fd.append('overwrite', 'true')" in text
        assert "doUpload(duplicate.files, true)" in text
