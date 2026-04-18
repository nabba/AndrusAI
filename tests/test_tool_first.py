"""Tests for Tool-First enforcement: preamble directive, refusal detection,
retry loop, and tool-manifest injection into agent backstory."""
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.crews import base_crew  # noqa: E402
from app.souls.loader import METACOGNITIVE_PREAMBLE  # noqa: E402


class TestMetacognitivePreamble:
    def test_tool_first_principle_present(self):
        assert "Tool-First Principle" in METACOGNITIVE_PREAMBLE
        assert "CALL THE TOOL" in METACOGNITIVE_PREAMBLE or "call it" in METACOGNITIVE_PREAMBLE.lower()

    def test_preamble_warns_against_refusal_patterns(self):
        lowered = METACOGNITIVE_PREAMBLE.lower()
        # At least three common refusal phrases must be explicitly called out
        assert "i don't have access" in lowered
        assert "i can't" in lowered
        assert "i'm unable" in lowered

    def test_preamble_promotes_chaining(self):
        assert "chain" in METACOGNITIVE_PREAMBLE.lower()

    def test_preamble_still_has_confidence_check(self):
        # Tool-First should be ADDITIVE — the original self-awareness layer stays.
        assert "CONFIDENCE" in METACOGNITIVE_PREAMBLE


class TestRefusalDetection:
    @pytest.mark.parametrize("text", [
        "I don't have access to your email.",
        "I can't help with that task.",
        "I'm unable to retrieve that information.",
        "I cannot do that.",
        "Unfortunately, I cannot complete this request.",
        "As an AI, I don't have real-time access.",
        "I apologize, but I can't access your calendar.",
    ])
    def test_refusal_patterns_detected(self, text):
        assert base_crew._looks_like_refusal(text) is True

    @pytest.mark.parametrize("text", [
        "Here are your unread emails: ...",
        "Your next meeting is at 14:00.",
        "I found 3 matching messages.",
        "Based on the search results, the answer is 42.",
    ])
    def test_normal_responses_not_flagged(self, text):
        assert base_crew._looks_like_refusal(text) is False

    def test_empty_response_not_flagged(self):
        assert base_crew._looks_like_refusal("") is False
        assert base_crew._looks_like_refusal(None) is False

    def test_refusal_after_tool_use_is_allowed(self):
        # If the agent genuinely tried and then reported it couldn't find anything,
        # we DON'T retry — the Action:/Observation: markers prove it tried.
        reasoned = (
            "Thought: Let me search.\n"
            "Action: search_email\n"
            "Action Input: {}\n"
            "Observation: No matching messages found.\n"
            "Final Answer: I'm unable to find any matches."
        )
        assert base_crew._looks_like_refusal(reasoned) is False


class TestRetryPromptBuilder:
    def test_includes_tool_names(self):
        class FakeTool:
            def __init__(self, n, d):
                self.name, self.description = n, d

        tools = [
            FakeTool("read_email", "Read recent emails from the IMAP inbox"),
            FakeTool("search_email", "Search email by keyword"),
        ]
        out = base_crew._build_retry_prompt(
            "check my inbox",
            tools,
            "I can't access your email.",
        )
        assert "read_email" in out
        assert "search_email" in out
        assert "check my inbox" in out
        assert "Tool-First protocol" in out

    def test_caps_tool_list_length(self):
        tools = []
        for i in range(50):
            t = MagicMock()
            t.name = f"tool_{i}"
            t.description = "x"
            tools.append(t)
        out = base_crew._build_retry_prompt("task", tools, "I can't.")
        # Should not explode in size
        assert len(out) < 10_000
        # Should include a reasonable number of tools
        assert out.count("tool_") >= 20

    def test_empty_tool_list_handled(self):
        out = base_crew._build_retry_prompt("task", [], "I can't.")
        assert "bug" in out.lower()  # marker noting an empty list is unexpected


class TestToolManifestInjection:
    def test_append_manifest_adds_marker(self):
        class FakeTool:
            def __init__(self, n, d):
                self.name, self.description = n, d

        tools = [FakeTool("read_email", "Read recent emails"),
                 FakeTool("search_email", "Search inbox")]
        out = base_crew._append_tool_manifest("You are a PIM agent.", tools)
        assert "Your Tools (Tool-First Protocol)" in out
        assert "`read_email`" in out
        assert "Read recent emails" in out
        assert "`search_email`" in out
        assert "You are a PIM agent." in out

    def test_append_manifest_idempotent(self):
        class FakeTool:
            def __init__(self, n):
                self.name, self.description = n, "x"

        initial = "Base backstory."
        once = base_crew._append_tool_manifest(initial, [FakeTool("tool_a")])
        twice = base_crew._append_tool_manifest(once, [FakeTool("tool_a"),
                                                        FakeTool("tool_b")])
        # Second call refreshes rather than appending a second manifest block
        assert twice.count(base_crew._TOOL_MANIFEST_MARKER) == 1
        assert "tool_b" in twice

    def test_empty_tools_unchanged(self):
        assert base_crew._append_tool_manifest("backstory", []) == "backstory"

    def test_long_description_truncated(self):
        class FakeTool:
            def __init__(self):
                self.name = "verbose"
                self.description = "x" * 500
        out = base_crew._append_tool_manifest("", [FakeTool()])
        # Description line should be bounded
        line = next(l for l in out.splitlines() if "`verbose`" in l)
        assert len(line) < 160


class TestAgentPatchInjectsManifest:
    """Verify the monkey-patched Agent.__init__ enriches the backstory at
    construction time — end-to-end for the injection path."""

    def test_backstory_gets_manifest_when_tools_present(self, monkeypatch):
        pytest.importorskip("crewai")
        # Reset patch state
        base_crew._agent_patched = False
        base_crew._tool_plugins.clear()
        base_crew._plugin_tools_cache = None

        captured = {}

        class FakeAgent:
            def __init__(self, *, role="r", goal="g", backstory="b", tools=None, **kw):
                captured["backstory"] = backstory
                captured["tools"] = list(tools) if tools else []
                self.tools = captured["tools"]

        import sys
        import types as _types
        fake_mod = _types.ModuleType("crewai_tool_first_test")
        fake_mod.Agent = FakeAgent
        original = sys.modules.get("crewai")
        sys.modules["crewai"] = fake_mod
        try:
            base_crew._patch_agent_for_plugins()

            class FakeTool:
                def __init__(self, n):
                    self.name, self.description = n, "does thing"

            FakeAgent(
                role="pim",
                goal="manage email and calendar",
                backstory="You are a PIM agent.",
                tools=[FakeTool("read_email"), FakeTool("send_email")],
            )
            assert "Your Tools (Tool-First Protocol)" in captured["backstory"]
            assert "`read_email`" in captured["backstory"]
        finally:
            if original is None:
                sys.modules.pop("crewai", None)
            else:
                sys.modules["crewai"] = original
            base_crew._agent_patched = False
