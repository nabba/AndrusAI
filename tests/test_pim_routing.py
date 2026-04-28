"""Tests for the 2026-04-28 PIM short-circuit in fast_route.

Regression — over a single calendar day, the same kind of question
that worked yesterday started misrouting today:

  Yesterday:  "any important e-mails I have got over weekend?"
              → routed to PIM crew → real digest delivered
  Today:      "what are the most important emails I have received today.
              please rank and give me top 25"
              → routed to research crew (no email tools)
              → "I do not have access to your email account" refusal

Root cause — the strict PIM rule
    ^(?:check|read|send|reply|forward)\\s+(?:my\\s+)?(?:email|inbox|mail)
only matches imperatives starting with check/read/send/reply/forward.
The user's question started with "what" so it matched the generic
question-word rule first → research crew → no email tools.

Fix — _looks_like_pim_question scans for the conjunction of:
  * a PIM noun (email, inbox, mailbox, gmail, calendar, meetings,
    tasks, todos, …)
  * AND a personal qualifier (my / today / weekend / important /
    urgent / top N / unread / recent / rank / …)
When both signal classes are present, the prompt is unambiguously
about the user's mailbox/calendar/tasks, regardless of how it's
phrased ("what are…", "any …", "rank …", etc.).
"""
from __future__ import annotations

import pytest

from tests._v2_shim import install_settings_shim
install_settings_shim()

from app.agents.commander.routing import (
    _looks_like_pim_question,
    _try_fast_route,
)


# ══════════════════════════════════════════════════════════════════════
# _looks_like_pim_question — the new heuristic
# ══════════════════════════════════════════════════════════════════════

class TestLooksLikePimQuestion:

    @pytest.mark.parametrize("prompt", [
        # The exact phrasings from yesterday + today's regression
        "any important e-mails I have got over weekend i need to pay attention to?",
        "what are the most important emails I have received today",
        "what are the most important emails I have received today. please rank and give me top 25",
        # Common phrasings that should also work
        "show me my unread emails",
        "what's in my inbox",
        "rank my emails by priority",
        "any urgent emails today",
        "any emails received this morning",
        "top 10 emails to reply to",
        "what meetings do I have today",
        "any important meetings this week",
        "what tasks are due today",
        # Estonian / multilingual still fine because keywords are English
        "important emails this weekend",
    ])
    def test_real_pim_phrasings_detected(self, prompt):
        assert _looks_like_pim_question(prompt) is True, (
            f"PIM detector should fire on real-world phrasing: {prompt!r}"
        )

    @pytest.mark.parametrize("prompt", [
        # Research-shaped questions that happen to mention "email" — must NOT route to PIM
        "research email marketing best practices in B2B SaaS",
        "what are the most popular email marketing tools",
        "compare gmail and outlook for business use",
        "explain how IMAP works",
        "history of email protocols",
        # Generic factual questions
        "what is the capital of France",
        "what time is it in Tokyo",
        "who was the first president of Estonia",
        # Coding / writing / unrelated
        "write a function to parse json",
        "implement the fibonacci sequence",
        "tell me a joke",
    ])
    def test_non_pim_prompts_not_detected(self, prompt):
        assert _looks_like_pim_question(prompt) is False, (
            f"PIM detector should NOT fire on non-PIM prompt: {prompt!r}"
        )

    def test_email_noun_alone_not_enough(self):
        """The presence of an email noun alone shouldn't fire PIM —
        the qualifier (my/today/important/etc.) is what makes it
        unambiguously about the user's personal mailbox."""
        assert _looks_like_pim_question("explain emails") is False
        assert _looks_like_pim_question("how do calendars work") is False

    def test_qualifier_alone_not_enough(self):
        """A qualifier without a PIM noun shouldn't fire either —
        otherwise "what's important today" would route to PIM."""
        assert _looks_like_pim_question("what is important today") is False
        assert _looks_like_pim_question("ranking algorithms") is False

    def test_empty_input(self):
        assert _looks_like_pim_question("") is False
        assert _looks_like_pim_question(None) is False  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════
# _try_fast_route — end-to-end routing decision
# ══════════════════════════════════════════════════════════════════════

class TestFastRouteDispatchesEmailToPim:
    """The PIM short-circuit must route email-shaped questions to the
    PIM crew, even when the prompt starts with what/who/when/where."""

    def test_today_emails_top_25_routes_to_pim(self):
        """The exact prompt that triggered the regression."""
        out = _try_fast_route(
            "what are the most important emails I have received today. please rank and give me top 25",
            has_attachments=False,
        )
        assert out is not None, "should fast-route, not fall through to LLM"
        assert len(out) == 1
        assert out[0]["crew"] == "pim", (
            f"expected pim, got {out[0]['crew']!r} — regression has returned"
        )

    def test_weekend_emails_routes_to_pim(self):
        """The exact prompt from yesterday's successful answer."""
        out = _try_fast_route(
            "any important e-mails I have got over weekend i need to pay attention to?",
            has_attachments=False,
        )
        assert out is not None
        assert out[0]["crew"] == "pim"

    def test_show_inbox_still_pim(self):
        """The original (working) PIM rule keeps working."""
        out = _try_fast_route(
            "check my inbox",
            has_attachments=False,
        )
        assert out is not None
        assert out[0]["crew"] == "pim"

    def test_research_about_emails_stays_research(self):
        """A genuine research question that mentions 'email' must
        NOT be diverted to PIM — no personal qualifier present."""
        out = _try_fast_route(
            "what is email marketing",
            has_attachments=False,
        )
        # Either fast-routes to research or falls through (None) —
        # both are acceptable. The forbidden answer is 'pim'.
        if out is not None:
            assert out[0]["crew"] != "pim", (
                "Generic email knowledge questions must not route to PIM."
            )

    def test_calendar_today_routes_to_pim(self):
        out = _try_fast_route(
            "what meetings do I have today",
            has_attachments=False,
        )
        assert out is not None
        assert out[0]["crew"] == "pim"

    def test_long_prompt_short_circuits_skipped(self):
        """The fast-route function bails on >200 char prompts. PIM
        short-circuit also lives behind that gate, so very long
        prompts go through LLM routing — that's the right behavior
        because long inputs need the commander's nuance."""
        long_prompt = "what are " + ("very important emails " * 30)
        out = _try_fast_route(long_prompt, has_attachments=False)
        assert out is None  # falls through to LLM router
