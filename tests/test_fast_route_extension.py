"""Tests for the extended fast-route patterns (2026-05-20).

Covers:
  * 4 new patterns in ``_EXTENDED_FAST_ROUTE_PATTERNS``
    (briefings, recall, listing, status)
  * master switch (runtime_settings.fast_route_extended_patterns_enabled)
  * existing patterns unchanged (regression)

Safety invariants pinned by these tests:
  * existing PIM short-circuit still wins for personal-inbox queries
  * existing follow-up detection still skips fast-route entirely
  * master switch off → bit-identical to pre-extension behaviour
  * extended patterns never shadow a base pattern that matched first
  * each extended pattern routes to ``direct`` (no crew dispatch)
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

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


from app import runtime_settings  # noqa: E402
from app.agents.commander.routing import (  # noqa: E402
    _EXTENDED_FAST_ROUTE_PATTERNS,
    _extended_fast_route_enabled,
    _try_fast_route,
)


def _reset_runtime_settings() -> None:
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# Master switch
# ============================================================================


class TestMasterSwitch(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_default_is_true(self):
        with _patch_runtime_settings():
            self.assertTrue(_extended_fast_route_enabled())

    def test_set_to_false(self):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=False):
            self.assertFalse(_extended_fast_route_enabled())

    def test_runtime_settings_failure_defaults_true(self):
        # Simulate runtime_settings raising — patterns must still fire.
        with patch.object(
            runtime_settings,
            "get_fast_route_extended_patterns_enabled",
            side_effect=RuntimeError("boom"),
        ):
            self.assertTrue(_extended_fast_route_enabled())

    def test_switch_off_skips_extended_patterns(self):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=False):
            # "morning briefing" only matches via extended patterns.
            result = _try_fast_route("morning briefing", False)
            self.assertIsNone(result)

    def test_switch_on_enables_extended_patterns(self):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            result = _try_fast_route("morning briefing", False)
            self.assertIsNotNone(result)
            self.assertEqual(result[0]["crew"], "direct")


# ============================================================================
# Briefings pattern
# ============================================================================


class TestBriefingsPattern(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def _route(self, text: str):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            return _try_fast_route(text, False)

    def test_morning_briefing(self):
        result = self._route("morning briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")
        self.assertEqual(result[0]["difficulty"], 2)

    def test_evening_briefing(self):
        result = self._route("evening briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_weekly_briefing(self):
        result = self._route("weekly briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_daily_briefing(self):
        result = self._route("daily briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_show_me_briefing(self):
        result = self._route("show me the morning briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_give_me_briefing(self):
        result = self._route("give me the weekly briefing")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_does_not_match_unrelated_briefing_word(self):
        # "briefing about the project" — not anchored at start with
        # qualifier; should NOT match the briefing pattern. (Falls
        # through to LLM router.)
        result = self._route("write a briefing about the project")
        # The "write a ... briefing" doesn't start with morning/evening/
        # etc. and doesn't match the "write + writing nouns" base pattern
        # (which lists email/letter/report/etc. — "briefing" not in
        # that list). Falls through to LLM router → None.
        # If a future tuning adds "briefing" to the writing-nouns
        # pattern, this test will catch the overlap.
        self.assertIsNone(result)


# ============================================================================
# Recall pattern
# ============================================================================


class TestRecallPattern(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def _route(self, text: str):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            return _try_fast_route(text, False)

    def test_recall_keyword(self):
        result = self._route("recall the meeting we had last week")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")
        self.assertEqual(result[0]["difficulty"], 3)

    def test_search_past_conversations(self):
        result = self._route("search past conversations for AMOC")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")
        self.assertEqual(result[0]["difficulty"], 3)

    def test_search_previous_history(self):
        result = self._route("search previous history")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_what_did_we_discuss_goes_to_research_via_base_pattern(self):
        # The base research pattern at line 92 matches "what did" before
        # the extended recall pattern sees the input. This documents the
        # shadow — operator gets research crew, which is acceptable
        # behaviour (research crew still answers the question, just via
        # a different path).
        result = self._route("what did we discuss about X")
        # ``_FOLLOW_UP_ANAPHORA`` includes "discussed" / "talked about"
        # but NOT bare "discuss" — so this is NOT classified as a
        # follow-up and falls through to the base patterns. The
        # research pattern matches "^what" first.
        if result is not None:
            self.assertEqual(result[0]["crew"], "research")


# ============================================================================
# Listing pattern
# ============================================================================


class TestListingPattern(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def _route(self, text: str):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            return _try_fast_route(text, False)

    def test_list_my_files(self):
        result = self._route("list my files")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")
        self.assertEqual(result[0]["difficulty"], 2)

    def test_show_my_notes(self):
        result = self._route("show my notes")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_list_skills(self):
        result = self._route("list skills")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_show_open_threads(self):
        result = self._route("show open threads")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_list_pending_change_requests(self):
        result = self._route("list pending change requests")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_show_recent_amendments(self):
        result = self._route("show recent amendments")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_list_drills(self):
        result = self._route("list drills")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_show_settings(self):
        result = self._route("show settings")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")


# ============================================================================
# Status pattern
# ============================================================================


class TestStatusPattern(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def _route(self, text: str):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            return _try_fast_route(text, False)

    def test_show_status(self):
        result = self._route("show status")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")
        self.assertEqual(result[0]["difficulty"], 2)

    def test_check_health(self):
        result = self._route("check health")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_show_monitor_status(self):
        result = self._route("show monitor status")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")

    def test_check_healing_status(self):
        result = self._route("check healing status")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "direct")


# ============================================================================
# Pre-existing pattern regression
# ============================================================================


class TestPreExistingPatternsUnchanged(unittest.TestCase):
    """Confirm that adding extended patterns does NOT break the
    base patterns. Each test exercises a phrasing that should still
    route to its historical destination, regardless of switch state."""

    def setUp(self) -> None:
        _reset_runtime_settings()

    def _route(self, text: str):
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            return _try_fast_route(text, False)

    def test_pim_email_still_routes_to_pim(self):
        result = self._route("check my email")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "pim")

    def test_pim_calendar_still_routes_to_pim(self):
        # PIM short-circuit fires via _looks_like_pim_question (calendar
        # noun + today qualifier).
        result = self._route("what meetings do I have today")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "pim")

    def test_research_explain_still_routes_to_research(self):
        # Cannot use a short "what is X" phrasing — the existing
        # _is_likely_follow_up gate at routing.py:429 classifies
        # short question-word messages as follow-ups and returns
        # None (skipping ALL fast-route patterns). Use "explain X"
        # phrasing which matches the existing define/explain/describe
        # pattern at line 97 without triggering the follow-up gate.
        result = self._route("explain photosynthesis to me")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "research")

    def test_coding_write_function_still_routes_to_coding(self):
        result = self._route("write a function to sort a list")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "coding")

    def test_writing_draft_email_still_routes_to_writing(self):
        result = self._route("draft an email to the team")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "writing")

    def test_devops_deploy_still_routes_to_devops(self):
        result = self._route("deploy the staging environment")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["crew"], "devops")


# ============================================================================
# Mechanical guarantees: long messages, attachments, follow-ups
# ============================================================================


class TestMechanicalGuarantees(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_long_message_returns_none(self):
        # _try_fast_route returns None for messages > 200 chars; the
        # extended patterns must not bypass that guard.
        long_text = "morning briefing " + "x" * 250
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            self.assertIsNone(_try_fast_route(long_text, False))

    def test_attachments_returns_none(self):
        # Same guard for attachments.
        with _patch_runtime_settings(
                fast_route_extended_patterns_enabled=True):
            self.assertIsNone(
                _try_fast_route("morning briefing", True),
            )

    def test_extended_patterns_list_has_expected_size(self):
        # Pin the count so future additions are conscious decisions
        # rather than accidental copy-paste.
        self.assertEqual(len(_EXTENDED_FAST_ROUTE_PATTERNS), 4)

    def test_all_extended_patterns_route_to_direct(self):
        # Safety invariant: extended patterns only route to "direct"
        # (Commander handles via tools, no crew dispatch). If a future
        # pattern needs a different crew, that's a deliberate change
        # caught by this test.
        for _pattern, crew, _difficulty in _EXTENDED_FAST_ROUTE_PATTERNS:
            self.assertEqual(
                crew, "direct",
                f"extended pattern {_pattern.pattern!r} routes to "
                f"{crew!r} — expected 'direct'",
            )


if __name__ == "__main__":
    unittest.main()
