"""Tests for the 2026-04-26 unified ``/<kb>/documents`` endpoints.

User asked for a per-document list view in the Knowledge tab showing
title, author, themes, and added date. Backend exposes one endpoint
per KB; React component normalizes responses into a common shape.

Each endpoint must return either a list or a wrapped {documents: [...]}
shape, where every entry carries — at minimum — title, themes, chunks,
and added_at. Author is optional (some KBs don't have it).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# Endpoint registration — static analysis (no app import needed)
# ══════════════════════════════════════════════════════════════════════

class TestDocumentsEndpointsRegistered:
    """Each KB router must expose a ``/documents`` route. We don't need
    a live gateway — text inspection of the router files is enough to
    pin the regression."""

    @pytest.mark.parametrize("path,decorator", [
        ("app/api/kb.py",            '@router.get("/documents")'),
        ("app/api/fiction.py",       '@fiction_router.get("/documents")'),
        ("app/philosophy/api.py",    '@philosophy_router.get("/documents")'),
        ("app/episteme/api.py",      '@episteme_router.get("/documents")'),
        ("app/experiential/api.py",  '@experiential_router.get("/documents")'),
    ])
    def test_route_exists(self, path, decorator):
        text = (REPO / path).read_text()
        assert decorator in text, (
            f"{path} must declare {decorator!r} so the React dashboard's "
            f"KBDocumentList component can fetch its document list."
        )


# ══════════════════════════════════════════════════════════════════════
# Frontend endpoint constants — must match backend paths
# ══════════════════════════════════════════════════════════════════════

class TestFrontendEndpointConstants:
    """The dashboard's endpoints.ts must export *Documents() helpers
    pointing at the backend routes. Stops the same kind of frontend/
    backend drift that caused /philosophy/stats vs /philosophy/status."""

    @pytest.mark.parametrize("name,expected_path", [
        ("kbDocuments",           "/kb/documents"),
        ("philosophyDocuments",   "/philosophy/documents"),
        ("epistemeDocuments",     "/episteme/documents"),
        ("fictionDocuments",      "/fiction/documents"),
        ("experientialDocuments", "/experiential/documents"),
    ])
    def test_endpoint_constant_present(self, name, expected_path):
        text = (REPO / "dashboard-react" / "src" / "api" / "endpoints.ts").read_text()
        # `name: () => `/path`,`
        pattern = rf"{name}:\s*\(\)\s*=>\s*`{re.escape(expected_path)}`"
        assert re.search(pattern, text), (
            f"endpoints.ts must export {name}() returning {expected_path!r} — "
            f"the KBDocumentList component depends on it."
        )


# ══════════════════════════════════════════════════════════════════════
# Response-shape contract — added_at + themes are present
# ══════════════════════════════════════════════════════════════════════

class TestPhilosophyResponseShape:
    """Static check that the /philosophy/texts handler builds rows with
    added_at and themes — the regression class is "we added the field
    in plan but forgot in code". Inspecting the source string keeps
    the test fast and self-contained (no chromadb fixture needed)."""

    def test_philosophy_includes_added_at(self):
        text = (REPO / "app" / "philosophy" / "api.py").read_text()
        assert '"added_at"' in text, (
            "philosophy /texts response must include added_at"
        )
        assert "fromtimestamp" in text and "st_mtime" in text, (
            "philosophy /texts must derive added_at from filesystem mtime"
        )

    def test_philosophy_includes_themes(self):
        text = (REPO / "app" / "philosophy" / "api.py").read_text()
        assert '"themes"' in text, (
            "philosophy /texts response must include themes (tradition + era)"
        )


class TestEpistemeResponseShape:

    def test_episteme_includes_added_at(self):
        text = (REPO / "app" / "episteme" / "api.py").read_text()
        assert "added_at" in text, "episteme /texts must include added_at"

    def test_episteme_includes_themes(self):
        text = (REPO / "app" / "episteme" / "api.py").read_text()
        assert '"themes"' in text or "themes" in text, (
            "episteme /texts must include themes"
        )


class TestKbDocumentsResponseShape:

    def test_kb_documents_normalized(self):
        text = (REPO / "app" / "api" / "kb.py").read_text()
        # Must pull category + tags into themes, ingested_at → added_at
        assert "added_at" in text
        assert "themes" in text


class TestFictionDocumentsResponseShape:

    def test_fiction_uses_existing_chromadb_metadata(self):
        text = (REPO / "app" / "api" / "fiction.py").read_text()
        # Themes and book_title come straight from chunk metadata —
        # the fiction ingester writes them at upload time
        assert "book_title" in text
        assert "themes" in text
        assert "ingested_at" in text


# ══════════════════════════════════════════════════════════════════════
# DocumentList normalizer — frontend handles wrapped vs bare-array shapes
# ══════════════════════════════════════════════════════════════════════

class TestKBDocumentListNormalizer:
    """The React adapter normalizes responses across {documents: [...]},
    {texts: [...]}, and bare arrays. Static check that the handler
    branches exist in the component source."""

    def test_handles_wrapped_documents(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KBDocumentList.tsx"
        ).read_text()
        assert "Array.isArray(r.documents)" in text

    def test_handles_wrapped_texts(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KBDocumentList.tsx"
        ).read_text()
        assert "Array.isArray(r.texts)" in text

    def test_handles_bare_array(self):
        text = (
            REPO / "dashboard-react" / "src" / "components"
            / "KBDocumentList.tsx"
        ).read_text()
        assert "Array.isArray(raw)" in text
