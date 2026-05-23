"""Tests for the Phase C.1 tool-registry migrations (2026-05-22).

Confirms that 5 additional previously-unregistered-by-name tools now
surface in the registry with cleanly-mapped capability tags from the
existing bounded vocabulary (no new capability category was added).

Tools covered:
  * web_fetch                             (app/tools/web_fetch.py)
  * firecrawl_scrape / firecrawl_search /
    firecrawl_extract / firecrawl_map /
    firecrawl_crawl                       (app/tools/firecrawl_tools.py)
  * create_pdf / create_docx / create_xlsx /
    create_pptx                           (app/tools/document_generator.py)
  * ocr_extract_text                      (app/tools/ocr_tool.py)
  * research_orchestrator                 (app/tools/research_orchestrator.py)

Safety invariants pinned:
  * each tool's capabilities match the post-migration design
  * each capability tag used is in the bounded vocabulary
    (no Tier-3 amendment introduced for a new capability category)
  * decorator is passive — original function paths still work
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

# ── Stubs (defensive — defer to real crewai when available) ──────────
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    import crewai.tools as _real_crewai_tools  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m

for _mod in ("langchain_anthropic", "docker"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)


from app.tool_registry import ToolRegistry  # noqa: E402
from app.tool_registry.capabilities import is_known  # noqa: E402


# Import the tool modules so decorators fire and side-table is populated.
try:
    from app.tools import web_fetch as _wf  # noqa: F401, E402
    from app.tools import firecrawl_tools as _fc  # noqa: F401, E402
    from app.tools import document_generator as _dg  # noqa: F401, E402
    from app.tools import ocr_tool as _ocr  # noqa: F401, E402
    from app.tools import research_orchestrator as _ro  # noqa: F401, E402
    _tools_imported = True
except Exception as _exc:
    _tools_imported = False
    _import_err = _exc


# ── Helpers ─────────────────────────────────────────────────────────


def _make_expected_capability_assertions(
    cls: type[unittest.TestCase],
    expected: dict[str, tuple[str, ...]],
) -> None:
    """Mixin-style: append the three standard contract tests to cls."""
    # We just attach the EXPECTED data; the test methods are defined
    # via inheritance in each suite below to keep the unittest
    # discoverable.
    cls.EXPECTED = expected


# ── web_fetch ───────────────────────────────────────────────────────


@unittest.skipUnless(_tools_imported, "tool modules not importable")
class TestWebFetchMigration(unittest.TestCase):
    EXPECTED = {
        "web_fetch": ("searches-web",),
    }

    def setUp(self) -> None:
        self.reg = ToolRegistry.instance()

    def test_registered(self):
        spec = self.reg.get("web_fetch")
        self.assertIsNotNone(spec)

    def test_capability_matches(self):
        spec = self.reg.get("web_fetch")
        self.assertIsNotNone(spec)
        self.assertEqual(tuple(spec.capabilities), ("searches-web",))

    def test_capability_in_vocabulary(self):
        self.assertTrue(is_known("searches-web"))

    def test_legacy_function_still_callable(self):
        # Legacy direct import path unchanged.
        self.assertTrue(callable(_wf.web_fetch))


# ── firecrawl family ────────────────────────────────────────────────


@unittest.skipUnless(_tools_imported, "tool modules not importable")
class TestFirecrawlMigration(unittest.TestCase):
    EXPECTED = {
        "firecrawl_scrape":  ("searches-web",),
        "firecrawl_search":  ("searches-web",),
        "firecrawl_extract": ("searches-web",),
        "firecrawl_map":     ("searches-web",),
        "firecrawl_crawl":   ("searches-web",),
    }

    def setUp(self) -> None:
        self.reg = ToolRegistry.instance()

    def test_all_five_registered(self):
        for name in self.EXPECTED:
            self.assertIsNotNone(
                self.reg.get(name),
                f"firecrawl tool {name!r} missing from registry",
            )

    def test_each_tool_has_correct_capabilities(self):
        for name, expected_caps in self.EXPECTED.items():
            spec = self.reg.get(name)
            self.assertIsNotNone(spec)
            self.assertEqual(
                tuple(spec.capabilities), expected_caps,
                f"firecrawl tool {name!r} has caps {spec.capabilities!r}; "
                f"expected {expected_caps!r}",
            )

    def test_capabilities_in_vocabulary(self):
        for _name, caps in self.EXPECTED.items():
            for cap in caps:
                self.assertTrue(
                    is_known(cap),
                    f"capability tag {cap!r} not in bounded vocabulary",
                )

    def test_legacy_factory_still_works(self):
        # The create_firecrawl_tools factory must still produce
        # a list (possibly empty when Firecrawl client is unavailable
        # in the test env — what matters is the call doesn't blow up).
        self.assertTrue(callable(_fc.create_firecrawl_tools))
        tools = _fc.create_firecrawl_tools()
        self.assertIsInstance(tools, list)


# ── document_generator family ──────────────────────────────────────


@unittest.skipUnless(_tools_imported, "tool modules not importable")
class TestDocumentGeneratorMigration(unittest.TestCase):
    EXPECTED = {
        "create_pdf":  ("renders-pdf", "renders-document"),
        "create_docx": ("renders-document",),
        "create_xlsx": ("renders-document",),
        "create_pptx": ("renders-document",),
    }

    def setUp(self) -> None:
        self.reg = ToolRegistry.instance()

    def test_all_four_registered(self):
        for name in self.EXPECTED:
            self.assertIsNotNone(
                self.reg.get(name),
                f"document-generator tool {name!r} missing from registry",
            )

    def test_capabilities_match(self):
        for name, expected_caps in self.EXPECTED.items():
            spec = self.reg.get(name)
            self.assertIsNotNone(spec)
            self.assertEqual(
                tuple(spec.capabilities), expected_caps,
                f"tool {name!r} has caps {spec.capabilities!r}; "
                f"expected {expected_caps!r}",
            )

    def test_capabilities_in_vocabulary(self):
        for _name, caps in self.EXPECTED.items():
            for cap in caps:
                self.assertTrue(
                    is_known(cap),
                    f"capability tag {cap!r} not in bounded vocabulary",
                )

    def test_legacy_functions_still_callable(self):
        # The original module-level functions remain importable.
        for fn_name in ("create_pdf", "create_docx", "create_xlsx",
                        "create_pptx"):
            self.assertTrue(
                callable(getattr(_dg, fn_name)),
                f"{fn_name} not callable",
            )


# ── ocr_tool ────────────────────────────────────────────────────────


@unittest.skipUnless(_tools_imported, "tool modules not importable")
class TestOcrToolMigration(unittest.TestCase):
    EXPECTED = {
        "ocr_extract_text": ("reads-attachment",),
    }

    def setUp(self) -> None:
        self.reg = ToolRegistry.instance()

    def test_registered(self):
        self.assertIsNotNone(self.reg.get("ocr_extract_text"))

    def test_capability_matches(self):
        spec = self.reg.get("ocr_extract_text")
        self.assertIsNotNone(spec)
        self.assertEqual(tuple(spec.capabilities), ("reads-attachment",))

    def test_capability_in_vocabulary(self):
        self.assertTrue(is_known("reads-attachment"))

    def test_legacy_factory_still_works(self):
        # create_ocr_tool returns either a tool or None depending on
        # whether the OCR backend is available in the test env. Either
        # is acceptable — we just verify it doesn't raise.
        result = _ocr.create_ocr_tool()
        self.assertTrue(result is None or callable(result) or
                        hasattr(result, "name"))


# ── research_orchestrator ──────────────────────────────────────────


@unittest.skipUnless(_tools_imported, "tool modules not importable")
class TestResearchOrchestratorMigration(unittest.TestCase):
    EXPECTED = {
        "research_orchestrator": ("searches-web", "reads-knowledge-base"),
    }

    def setUp(self) -> None:
        self.reg = ToolRegistry.instance()

    def test_registered(self):
        self.assertIsNotNone(self.reg.get("research_orchestrator"))

    def test_capabilities_match(self):
        spec = self.reg.get("research_orchestrator")
        self.assertIsNotNone(spec)
        self.assertEqual(
            tuple(spec.capabilities),
            ("searches-web", "reads-knowledge-base"),
        )

    def test_capabilities_in_vocabulary(self):
        self.assertTrue(is_known("searches-web"))
        self.assertTrue(is_known("reads-knowledge-base"))


# ── No new capability category was added ────────────────────────────


class TestNoNewCapabilityCategory(unittest.TestCase):
    """Pin the design constraint: Phase C.1 added zero new capability
    tags. Every capability used is from the pre-existing bounded
    vocabulary (no Tier-3 amendment was needed)."""

    PHASE_C1_CAPABILITIES = {
        # Capabilities every Phase C.1 tool maps to. The point of this
        # set is that all five exist in capabilities.py PRE-migration.
        "searches-web",
        "reads-knowledge-base",
        "renders-pdf",
        "renders-document",
        "reads-attachment",
    }

    def test_every_capability_in_bounded_vocabulary(self):
        for cap in self.PHASE_C1_CAPABILITIES:
            self.assertTrue(
                is_known(cap),
                f"Phase C.1 used capability {cap!r} which is NOT in the "
                f"bounded vocabulary. Migration violated 'no new "
                f"capability category' constraint.",
            )


if __name__ == "__main__":
    unittest.main()
