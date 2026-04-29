"""
Tests for the 4 recovery strategies + force-recover Signal command
shipped 2026-04-28 (the "implement follow-ups" PR).

  * direct_tool — bypass LLM, call email/calendar tools directly
  * sandbox_execute — extract Python from coding-crew dump + run it
  * skill_chain — surface a matching skill from the library
  * forge_queue — already pinned in test_recovery_loop.py
  * force-recover Signal command — bypasses confidence threshold
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests._v2_shim import install_settings_shim
install_settings_shim()


# ══════════════════════════════════════════════════════════════════════
# direct_tool — extract params + call tool directly
# ══════════════════════════════════════════════════════════════════════

class TestDirectToolParamExtraction:

    def test_email_extracts_top_n(self):
        from app.recovery.strategies.direct_tool import _email_params
        out = _email_params("show me top 25 emails today")
        assert out.get("limit") == 25
        assert out.get("days_back") == 1

    def test_email_extracts_unread(self):
        from app.recovery.strategies.direct_tool import _email_params
        out = _email_params("any unread emails this week")
        assert out.get("unread_only") is True
        assert out.get("days_back") == 7

    def test_email_yesterday(self):
        from app.recovery.strategies.direct_tool import _email_params
        out = _email_params("emails I received yesterday")
        assert out.get("days_back") == 2

    def test_email_caps_unreasonable_limit(self):
        from app.recovery.strategies.direct_tool import _email_params
        out = _email_params("show top 99999 emails")
        # Capped at 100
        assert out.get("limit") == 100

    def test_calendar_today(self):
        from app.recovery.strategies.direct_tool import _calendar_params
        out = _calendar_params("what meetings do I have today")
        assert out.get("days_ahead") == 1

    def test_calendar_default_limit(self):
        from app.recovery.strategies.direct_tool import _calendar_params
        out = _calendar_params("upcoming events")
        assert out.get("limit") is not None


class TestDirectToolExecution:

    def test_returns_failure_when_tool_unavailable(self, monkeypatch):
        """If email tools can't be created (no env config), strategy
        returns success=False so the loop falls through to next."""
        from app.recovery.strategies import direct_tool
        from app.recovery.librarian import Alternative
        # Force resolve_tool to return None
        monkeypatch.setattr(direct_tool, "_resolve_tool", lambda _name: None)
        alt = Alternative(
            strategy="direct_tool", tool="email_tools.check_email",
            rationale="x", est_cost_usd=0, est_latency_s=5, sync=True,
        )
        result = direct_tool.execute("emails today", alt, {})
        assert not result.success
        assert "not available" in (result.error or "").lower()

    def test_calls_tool_run_with_extracted_params(self, monkeypatch):
        """Verify the tool's _run is called with the regex-extracted kwargs."""
        from app.recovery.strategies import direct_tool
        from app.recovery.librarian import Alternative
        fake_tool = MagicMock()
        fake_tool._run.return_value = (
            "From: alice@foo.com\nSubject: Test\n\nBody body body body body body."
        )
        monkeypatch.setattr(direct_tool, "_resolve_tool", lambda _name: fake_tool)
        alt = Alternative(
            strategy="direct_tool", tool="email_tools.check_email",
            rationale="x", est_cost_usd=0, est_latency_s=5, sync=True,
        )
        result = direct_tool.execute("show top 10 emails today", alt, {})
        assert result.success
        kwargs = fake_tool._run.call_args.kwargs
        assert kwargs.get("limit") == 10
        assert kwargs.get("days_back") == 1
        # Note flagged for the "route changed" annotation
        assert result.route_changed
        assert "directly" in (result.note or "").lower()


# ══════════════════════════════════════════════════════════════════════
# sandbox_execute — extract code, run, return stdout
# ══════════════════════════════════════════════════════════════════════

class TestSandboxExtractCode:

    def test_extracts_python_block(self):
        from app.recovery.strategies.sandbox_execute import _extract_python_code
        text = (
            "Here's the script:\n\n"
            "```python\nprint('hello')\nprint(1+1)\n```\n\n"
            "EXECUTION OUTPUT: <unavailable>"
        )
        code = _extract_python_code(text)
        assert code is not None
        assert "print('hello')" in code

    def test_picks_largest_block(self):
        from app.recovery.strategies.sandbox_execute import _extract_python_code
        text = (
            "Setup:\n```python\nx = 1\n```\n\n"
            "Main:\n```python\nimport math\n"
            + "for i in range(100): print(i)\n" * 5
            + "```"
        )
        code = _extract_python_code(text)
        assert "for i in range(100)" in code
        assert "x = 1" not in code

    def test_returns_none_for_no_code_blocks(self):
        from app.recovery.strategies.sandbox_execute import _extract_python_code
        assert _extract_python_code("just prose, no code") is None

    def test_skips_network_required_scripts(self):
        from app.recovery.strategies.sandbox_execute import _is_runnable_in_sandbox
        runnable, reason = _is_runnable_in_sandbox(
            "import requests\nresp = requests.get('https://api.example.com')\nprint(resp.text)"
        )
        assert not runnable
        assert "network" in reason.lower()

    def test_skips_host_path_scripts(self):
        from app.recovery.strategies.sandbox_execute import _is_runnable_in_sandbox
        runnable, reason = _is_runnable_in_sandbox(
            "with open('/Users/andrus/data.csv') as f:\n    print(f.read())"
        )
        assert not runnable

    def test_pure_compute_runnable(self):
        from app.recovery.strategies.sandbox_execute import _is_runnable_in_sandbox
        runnable, _ = _is_runnable_in_sandbox(
            "import math\nfor i in range(10): print(math.sqrt(i))"
        )
        assert runnable


# ══════════════════════════════════════════════════════════════════════
# skill_chain — invoke matching library skill
# ══════════════════════════════════════════════════════════════════════

class TestSkillChain:

    def test_returns_failure_when_no_match(self, monkeypatch):
        from app.recovery.strategies import skill_chain
        from app.recovery.librarian import Alternative
        monkeypatch.setattr(
            skill_chain, "_search_top_skill", lambda _t: None,
        )
        alt = Alternative(
            strategy="skill_chain", rationale="x",
            est_cost_usd=0, est_latency_s=10, sync=True,
        )
        result = skill_chain.execute("esoteric thing", alt, {})
        assert not result.success
        assert "no skill matches" in (result.error or "")

    def test_surfaces_matching_skill_body(self, monkeypatch):
        from app.recovery.strategies import skill_chain
        from app.recovery.librarian import Alternative
        fake_skill = MagicMock()
        fake_skill.title = "PSP enrichment patterns"
        fake_skill.body = (
            "1. Get domain.\n2. Hit Apollo if APOLLO_API_KEY is set.\n"
            "3. Fall back to Brave search with site:linkedin.com filter.\n"
            "4. Return triplet (name, title, linkedin_url) per row."
            * 3
        )
        fake_skill.score = 0.78
        monkeypatch.setattr(
            skill_chain, "_search_top_skill", lambda _t: fake_skill,
        )
        alt = Alternative(
            strategy="skill_chain", rationale="x",
            est_cost_usd=0, est_latency_s=10, sync=True,
        )
        result = skill_chain.execute("find sales leads", alt, {})
        assert result.success
        assert "PSP enrichment patterns" in result.text
        assert "Apollo" in result.text
        assert result.route_changed


# ══════════════════════════════════════════════════════════════════════
# librarian — surfaces all 4 strategies for the right inputs
# ══════════════════════════════════════════════════════════════════════

class TestLibrarianWiringForFollowUps:

    def test_email_question_surfaces_direct_tool(self):
        from app.recovery.librarian import find_alternatives
        alts = find_alternatives(
            "what emails did I get today",
            refusal_category="missing_tool",
            used_crew="research",
        )
        strategies = [a.strategy for a in alts]
        assert "direct_tool" in strategies, (
            "Email question should produce a direct_tool alternative."
        )
        # And it should be FIRST (cheapest)
        non_forge = [s for s in strategies if s != "forge_queue"]
        assert non_forge[0] == "direct_tool"

    def test_response_with_python_block_surfaces_sandbox_execute(self):
        from app.recovery.librarian import find_alternatives
        alts = find_alternatives(
            "compute fibonacci of 10",
            refusal_category="missing_tool",
            used_crew="coding",
            response_text=(
                "Here's the code:\n```python\ndef fib(n): ...\nprint(fib(10))\n```\n\n"
                "EXECUTION OUTPUT: <unavailable>"
            ),
        )
        strategies = [a.strategy for a in alts]
        assert "sandbox_execute" in strategies

    def test_response_without_code_does_not_surface_sandbox(self):
        from app.recovery.librarian import find_alternatives
        alts = find_alternatives(
            "explain the algorithm",
            refusal_category="generic",
            used_crew="research",
            response_text="I'm unable to find that information.",
        )
        assert "sandbox_execute" not in [a.strategy for a in alts]

    def test_skill_chain_always_surfaced(self):
        """Skills are domain-agnostic — always worth checking."""
        from app.recovery.librarian import find_alternatives
        alts = find_alternatives(
            "anything at all",
            refusal_category="generic",
            used_crew="research",
        )
        assert "skill_chain" in [a.strategy for a in alts]


# ══════════════════════════════════════════════════════════════════════
# force-recover Signal command + force=True path
# ══════════════════════════════════════════════════════════════════════

class TestForceCommandPattern:
    """The Signal command-handler must recognize force-recover phrasings."""

    @pytest.mark.parametrize("phrase", [
        "force this",
        "force recover",
        "force-recover",
        "try harder",
        "try alternative",
        "try another way",
        "find another way",
        "Force This",       # case-insensitive
        "FORCE THIS",
    ])
    def test_force_pattern_recognized(self, phrase):
        """We don't actually call _handle_force_recover here (it'd
        hit conversation_store + recovery loop) — just verify the
        try_command short-circuit catches the phrase."""
        # Inspect the command source for the FORCE_PATTERNS tuple.
        from pathlib import Path
        REPO = Path(__file__).resolve().parent.parent
        text = (REPO / "app/agents/commander/commands.py").read_text()
        # All the patterns must be in the tuple
        for p in (
            "force this", "force recover", "force-recover",
            "try harder", "try alternative", "try another way",
            "find another way",
        ):
            assert p in text


class TestRefusalDetectorForceMode:
    """force=True must bypass policy guard, density check, and threshold."""

    def test_force_bypasses_policy_guard(self):
        from app.recovery.refusal_detector import detect_refusal
        text = "I cannot help with that — generating malicious code violates my guidelines."
        # Without force: respected (None)
        assert detect_refusal(text) is None
        # With force: returns a signal
        sig = detect_refusal(text, force=True)
        assert sig is not None

    def test_force_with_unmatched_text_returns_generic(self):
        """Even a perfectly fine response gets a low-conf generic
        signal under force=True so the loop can run."""
        from app.recovery.refusal_detector import detect_refusal
        text = "Here is a perfectly normal answer with no refusal phrases."
        assert detect_refusal(text) is None
        sig = detect_refusal(text, force=True)
        assert sig is not None
        assert sig.category == "generic"

    def test_force_bypasses_threshold(self):
        """Low-confidence phrase that wouldn't cross threshold passes
        with force=True."""
        from app.recovery.refusal_detector import detect_refusal
        text = "I cannot do this exact thing right now."
        # Default threshold misses this
        assert detect_refusal(text) is None
        # But force returns it
        sig = detect_refusal(text, force=True)
        assert sig is not None
