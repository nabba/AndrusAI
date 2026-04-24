"""Tests for ``_try_attachment_hint_route`` — the pre-LLM routing
override that forces "structured attachment + enrichment verb"
requests to the research crew.

Origin: 2026-04-24 task #72 misrouted a "merge PDF + populate sales
leaders" request to the coding crew, which spent 24 minutes failing
to read the attachment before giving up with a plan-only reply.
"""
from __future__ import annotations

import pytest

from app.agents.commander.orchestrator import _try_attachment_hint_route


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def pdf_attachment(name="doc.pdf"):
    return {"contentType": "application/pdf", "filename": name, "id": name}


def csv_attachment(name="data.csv"):
    return {"contentType": "text/csv", "filename": name}


def image_attachment(name="pic.jpg"):
    return {"contentType": "image/jpeg", "filename": name}


# ══════════════════════════════════════════════════════════════════════
# Positive cases — should force research
# ══════════════════════════════════════════════════════════════════════

class TestTriggers:

    @pytest.mark.parametrize("text", [
        "please take your PSP list and add non-duplicate PSPs from "
        "attached document to your list and then populate sales leaders fields",
        "merge this file with the previous list",
        "enrich the attached spreadsheet with LinkedIn URLs",
        "populate the table with email addresses from the PDF",
        "dedupe the records in the attached CSV against the existing list",
        "cross-reference the PDF against the prior report",
        "please combine these datasets",
    ])
    def test_pdf_plus_enrich_verb_forces_research(self, text):
        decisions = _try_attachment_hint_route(text, [pdf_attachment()])
        assert decisions is not None
        assert decisions[0]["crew"] == "research"
        # The hardened STEP-1 directive replaced the old polite tag.
        assert "MATRIX ENRICHMENT TASK" in decisions[0]["task"]
        assert "STEP 1 (MANDATORY FIRST ACTION)" in decisions[0]["task"]
        assert decisions[0]["difficulty"] == 7

    def test_csv_also_triggers(self):
        decisions = _try_attachment_hint_route(
            "merge this list with my existing table", [csv_attachment()],
        )
        assert decisions is not None
        assert decisions[0]["crew"] == "research"

    def test_filename_extension_fallback_without_contenttype(self):
        """Signal sometimes delivers attachments without an explicit
        contentType — we should still catch them by filename ext."""
        att = {"filename": "prospects.xlsx", "id": "abc123.xlsx"}
        decisions = _try_attachment_hint_route(
            "populate email fields for entries in this spreadsheet", [att],
        )
        assert decisions is not None
        assert decisions[0]["crew"] == "research"

    def test_task_prompt_includes_orchestrator_directive(self):
        decisions = _try_attachment_hint_route(
            "merge the attached list", [pdf_attachment()],
        )
        task = decisions[0]["task"]
        # STEP-1 directive must name the tool + forbid the common
        # misfires (delegation / MCP / web-search-first).
        assert "research_orchestrator" in task
        assert "read_attachment" in task
        assert "Do NOT call ``delegate_work_to_coworker``" in task
        assert "Do NOT call ``mcp_search_servers``" in task
        assert "Do NOT call ``web_search`` first" in task

    def test_user_request_preserved_after_directive(self):
        """The original user text must appear verbatim under the
        USER REQUEST header — otherwise the coordinator can't figure
        out what was actually asked for."""
        user_text = "please merge this PDF into my prior PSP list"
        decisions = _try_attachment_hint_route(user_text, [pdf_attachment()])
        task = decisions[0]["task"]
        assert "═══ USER REQUEST ═══" in task
        assert user_text in task


# ══════════════════════════════════════════════════════════════════════
# Negative cases — should fall through to LLM routing
# ══════════════════════════════════════════════════════════════════════

class TestNonTriggers:

    def test_no_attachments(self):
        assert _try_attachment_hint_route(
            "add non-duplicate PSPs to my list", [],
        ) is None

    def test_non_structured_attachment(self):
        # A JPG attachment with enrich verbs shouldn't trigger —
        # probably a screenshot / image analysis task.
        assert _try_attachment_hint_route(
            "merge the attached list", [image_attachment()],
        ) is None

    def test_pdf_without_enrich_verb(self):
        # "Summarize this PDF" is NOT enrichment.
        assert _try_attachment_hint_route(
            "summarize the attached PDF", [pdf_attachment()],
        ) is None

    def test_enrich_verb_without_list_noun(self):
        # "Enrich the attached document" with no list/table/report
        # noun is ambiguous — let the LLM router decide.
        assert _try_attachment_hint_route(
            "enrich this", [pdf_attachment()],
        ) is None

    def test_empty_text(self):
        assert _try_attachment_hint_route("", [pdf_attachment()]) is None
        assert _try_attachment_hint_route("   ", [pdf_attachment()]) is None

    def test_coding_task_with_pdf_falls_through(self):
        """"Read this PDF and write code to parse it" is genuinely
        coding — should NOT force research."""
        assert _try_attachment_hint_route(
            "write a python script that reads this PDF", [pdf_attachment()],
        ) is None


# ══════════════════════════════════════════════════════════════════════
# The exact original failure — regression guard
# ══════════════════════════════════════════════════════════════════════

class TestOriginalFailure:
    """This is the literal task text that misrouted on 2026-04-24.
    If this ever returns None again, we've regressed."""

    def test_psp_tender_exact_prompt(self):
        text = (
            "please take your PSP list and add non duplicate psp-s from "
            "attached document to your list and then please pupulate sales "
            "leaders fields and sales leader linkedin page links"
        )
        att = {
            "contentType": "application/pdf",
            "filename": "BD-PSP tender-240426-111841.pdf",
            "id": "9SYgcQuhM0ZZ53GL_vvd.pdf",
            "size": 812277,
        }
        decisions = _try_attachment_hint_route(text, [att])
        assert decisions is not None, (
            "REGRESSION: the exact PSP-tender prompt that misrouted in "
            "production is again falling through — investigate verb/noun "
            "dictionaries in _ATTACHMENT_ENRICH_VERBS / _ATTACHMENT_LIST_NOUNS."
        )
        assert decisions[0]["crew"] == "research"
