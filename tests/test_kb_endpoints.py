"""Tests for the 2026-04-26 KB endpoint + async-handler fixes.

Failure modes the user reported:

  * Dashboard "Failed to load" on Knowledge Base / Philosophy / Literature
    — caused by frontend hitting /philosophy/stats + /fiction/stats which
    returned 404 (backend mounted them as /status).
  * "Upload failed: 504 Gateway timeout" on episteme upload — caused by
    ingest_file() running synchronously inside an async handler and
    blocking the asyncio event loop for the whole 30-90s embed+store
    process. While blocked, every other request to the gateway timed
    out, including the dashboard's auto-refresh polling for the other
    KBs (cascading "Failed to load" cards).

These tests pin both classes of regression:
  * Route aliasing — /philosophy/stats and /fiction/stats reachable
  * Sync-in-async absence — handlers wrap the heavy work in
    asyncio.to_thread so the loop stays responsive.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
# Route aliases — frontend ↔ backend path agreement
# ══════════════════════════════════════════════════════════════════════

class TestKbRouteAliases:
    """The dashboard's React endpoints constants (philosophyStats →
    /philosophy/stats, fictionStatus → /fiction/status) must each
    correspond to at least one backend route. Frontend constants live
    in dashboard-react/src/api/endpoints.ts; backend routes are
    defined via @router.get(...) decorators."""

    def test_philosophy_stats_route_exists(self):
        """philosophy/api.py must answer at /stats (in addition to /status)."""
        text = (REPO / "app" / "philosophy" / "api.py").read_text()
        # Either an explicit /stats decorator or a duplicate decorator on /status
        assert '@philosophy_router.get("/stats")' in text, (
            "philosophy router must expose /stats — the React dashboard "
            "polls philosophyStats() → /philosophy/stats. Without this "
            "the Philosophy KB card shows 'Failed to load'."
        )

    def test_fiction_stats_route_exists(self):
        """fiction/api.py — frontend uses fictionStatus(), but symmetry
        with episteme/aesthetics/tensions also pulls /stats. Cover both
        so renaming the frontend constant doesn't break things again."""
        text = (REPO / "app" / "api" / "fiction.py").read_text()
        assert '@fiction_router.get("/stats")' in text, (
            "fiction router must expose /stats alongside /status."
        )


# ══════════════════════════════════════════════════════════════════════
# Sync-in-async absence — every embed+store call must use to_thread
# ══════════════════════════════════════════════════════════════════════

class TestUploadHandlersDoNotBlockLoop:
    """Heavy ingestion work (chunking, embedding, ChromaDB write) MUST
    run on a thread pool, not directly in the async handler. Otherwise
    a single large upload freezes the gateway and everything else
    times out — the 2026-04-26 504 cascade.

    Done by static analysis: walk the upload-handler files, find every
    call to a blocking ingestion API (ingest_file, store.add_pattern,
    etc.) inside an async function, and assert the call is wrapped in
    ``asyncio.to_thread``.
    """

    @pytest.mark.parametrize("path,blocking_calls", [
        ("app/episteme/api.py",      ["ingest_file"]),
        ("app/aesthetics/api.py",    ["add_pattern"]),
        ("app/tensions/api.py",      ["add_tension"]),
        ("app/experiential/api.py",  ["add_entry"]),
        ("app/api/fiction.py",       ["ingest_book", "ingest_library"]),
        ("app/philosophy/api.py",    [],  # philosophy uses async wrappers throughout
                                     ),
    ])
    def test_blocking_calls_use_to_thread(self, path, blocking_calls):
        text = (REPO / path).read_text()
        for call_name in blocking_calls:
            # Every line that mentions the blocking call must either
            # be a definition / import / comment, OR be inside a
            # to_thread wrapper. Defensive: also accept "result = call(...)"
            # if it's preceded by a to_thread on the same line.
            for m in re.finditer(rf"\b{call_name}\b", text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                line = text[line_start:line_end]
                stripped = line.strip()
                if stripped.startswith(("#", "from ", "import ", "def ",
                                        '"""', "'''", "*", "raise ",
                                        "logger.")):
                    continue
                # The actual invocation (e.g. ingest_file(dest)) — must
                # be wrapped in asyncio.to_thread on the same line.
                if "(" in stripped and "to_thread" not in stripped:
                    pytest.fail(
                        f"{path}: blocking call {call_name!r} on line:\n"
                        f"  {stripped}\n"
                        f"...is not wrapped in asyncio.to_thread. This "
                        f"freezes the event loop during embed+store and "
                        f"causes 504s + cascading 'Failed to load' on "
                        f"other dashboard cards."
                    )

    def test_episteme_upload_uses_to_thread_explicitly(self):
        """The exact line that caused the regression — pin it specifically."""
        text = (REPO / "app" / "episteme" / "api.py").read_text()
        assert "await _asyncio.to_thread(ingest_file" in text or \
               "await asyncio.to_thread(ingest_file" in text, (
            "episteme/api.py:_process_one_upload must wrap ingest_file "
            "in asyncio.to_thread. Direct sync call freezes the event "
            "loop for the whole 30-90s embed+store and triggers 504s."
        )

    def test_handler_decorators_remain_async(self):
        """Sanity — the upload functions themselves should still be
        async (we want to_thread inside async, not turn the whole
        function sync)."""
        for path in [
            "app/episteme/api.py", "app/aesthetics/api.py",
            "app/tensions/api.py", "app/experiential/api.py",
        ]:
            text = (REPO / path).read_text()
            tree = ast.parse(text)
            handlers = [
                node for node in ast.walk(tree)
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and any(
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("post", "put")
                    for d in node.decorator_list
                )
            ]
            assert handlers, f"{path}: no POST/PUT handlers found"
            for h in handlers:
                if h.name in ("upload_pattern", "upload_tension",
                              "upload_entry", "upload_texts"):
                    assert isinstance(h, ast.AsyncFunctionDef), (
                        f"{path}:{h.name} must remain async — the fix is "
                        f"to_thread INSIDE the async function, not switching "
                        f"to a sync handler."
                    )
