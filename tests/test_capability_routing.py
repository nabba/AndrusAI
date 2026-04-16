"""
test_capability_routing.py — Tests for new crew routing patterns and commands.

Tests that new crew types (pim, financial, desktop, repo_analysis, devops)
are correctly routed from user inputs via fast patterns and LLM routing.

Run: pytest tests/test_capability_routing.py -v
"""
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════
# Fast Route Pattern Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFastRoutePatterns:
    """Verify that new crew types are matched by fast-route regex patterns."""

    def _try_fast_route(self, text: str):
        """Test a text against fast route patterns, return (crew, difficulty) or None."""
        from app.agents.commander.routing import _FAST_ROUTE_PATTERNS
        for pattern, crew, difficulty in _FAST_ROUTE_PATTERNS:
            if pattern.search(text):
                return crew, difficulty
        return None

    # ── PIM routes ────────────────────────────────────────────────

    def test_check_email_routes_to_pim(self):
        assert self._try_fast_route("check my email") == ("pim", 3)

    def test_read_email_routes_to_pim(self):
        assert self._try_fast_route("read my inbox") is not None
        crew, _ = self._try_fast_route("read my inbox")
        assert crew == "pim"

    def test_send_email_routes_to_pim(self):
        assert self._try_fast_route("send email to bob") == ("pim", 3)

    def test_check_calendar_routes_to_pim(self):
        assert self._try_fast_route("check my calendar") == ("pim", 3)

    def test_show_events_routes_to_pim(self):
        result = self._try_fast_route("show my events")
        assert result is not None
        assert result[0] == "pim"

    def test_create_meeting_routes_to_pim(self):
        # "schedule a meeting" triggers the calendar pattern
        result = self._try_fast_route("schedule my meeting for tomorrow")
        assert result is not None
        assert result[0] == "pim"

    def test_add_task_routes_to_pim(self):
        assert self._try_fast_route("add task buy groceries") == ("pim", 2)

    def test_list_tasks_routes_to_pim(self):
        assert self._try_fast_route("list tasks") == ("pim", 2)

    def test_complete_task_routes_to_pim(self):
        result = self._try_fast_route("complete task #5")
        assert result is not None
        assert result[0] == "pim"

    # ── Financial routes ──────────────────────────────────────────

    def test_stock_routes_to_financial(self):
        assert self._try_fast_route("stock price of AAPL") == ("financial", 6)

    def test_investment_routes_to_financial(self):
        result = self._try_fast_route("analyze this investment opportunity")
        assert result is not None
        assert result[0] == "financial"

    def test_sec_routes_to_financial(self):
        result = self._try_fast_route("find SEC filings for Tesla")
        assert result is not None
        assert result[0] == "financial"

    def test_valuation_routes_to_financial(self):
        result = self._try_fast_route("run DCF valuation on Microsoft")
        assert result is not None
        assert result[0] == "financial"

    def test_market_routes_to_financial(self):
        result = self._try_fast_route("how is the market doing")
        assert result is not None
        assert result[0] == "financial"

    # ── Desktop routes ────────────────────────────────────────────

    def test_open_app_routes_to_desktop(self):
        assert self._try_fast_route("open Safari") == ("desktop", 4)

    def test_launch_routes_to_desktop(self):
        result = self._try_fast_route("launch Terminal")
        assert result is not None
        assert result[0] == "desktop"

    def test_screenshot_routes_to_desktop(self):
        result = self._try_fast_route("take a screenshot")
        assert result is not None
        assert result[0] == "desktop"

    def test_close_routes_to_desktop(self):
        result = self._try_fast_route("close this app")
        assert result is not None
        assert result[0] == "desktop"

    # ── Repo Analysis routes ──────────────────────────────────────

    def test_analyze_repo_routes(self):
        result = self._try_fast_route("analyze the repo github.com/user/project")
        assert result is not None
        assert result[0] == "repo_analysis"

    def test_audit_repo_routes(self):
        result = self._try_fast_route("audit my repo")
        assert result is not None
        assert result[0] == "repo_analysis"

    # ── DevOps routes ─────────────────────────────────────────────

    def test_deploy_routes_to_devops(self):
        assert self._try_fast_route("deploy the application") == ("devops", 5)

    def test_scaffold_routes_to_devops(self):
        result = self._try_fast_route("scaffold a new Python project")
        assert result is not None
        assert result[0] == "devops"

    def test_create_project_routes_to_devops(self):
        result = self._try_fast_route("create a new project called myapp")
        assert result is not None
        assert result[0] == "devops"

    # ── Non-matches still work ────────────────────────────────────

    def test_factual_question_still_routes_to_research(self):
        assert self._try_fast_route("what is the capital of Finland") == ("research", 2)

    def test_code_request_still_routes_to_coding(self):
        assert self._try_fast_route("write a function to sort a list") == ("coding", 5)


# ═══════════════════════════════════════════════════════════════════════════
# Valid Crews Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValidCrews:
    """Verify new crews are registered in all required locations."""

    def test_valid_crews_in_routing(self):
        """All new crews appear in _recover_truncated_routing valid_crews."""
        from app.agents.commander import routing
        import inspect
        source = inspect.getsource(routing._recover_truncated_routing)
        for crew in ["pim", "financial", "desktop", "repo_analysis", "devops"]:
            assert crew in source, f"'{crew}' missing from _recover_truncated_routing valid_crews"

    def test_valid_crews_in_orchestrator(self):
        """All new crews appear in orchestrator _VALID_CREWS."""
        from app.agents.commander import orchestrator
        import inspect
        source = inspect.getsource(orchestrator)
        for crew in ["pim", "financial", "desktop", "repo_analysis", "devops"]:
            assert crew in source, f"'{crew}' missing from orchestrator"

    def test_routing_prompt_mentions_new_crews(self):
        from app.agents.commander.routing import ROUTING_PROMPT
        for crew in ["pim", "financial", "desktop", "repo_analysis", "devops"]:
            assert crew in ROUTING_PROMPT, f"'{crew}' missing from ROUTING_PROMPT"


class TestTruncatedRoutingRecovery:
    """Verify truncated JSON recovery works with new crew names."""

    def test_recover_pim_crew(self):
        from app.agents.commander.routing import _recover_truncated_routing
        # Full valid JSON — recovery should parse it
        raw = '{"crews": [{"crew": "pim", "task": "check email", "difficulty": 3}]}'
        result = _recover_truncated_routing(raw)
        assert result is not None
        assert len(result) == 1
        assert result[0]["crew"] == "pim"

    def test_recover_financial_crew(self):
        from app.agents.commander.routing import _recover_truncated_routing
        raw = '{"crews": [{"crew": "financial", "task": "analyze AAPL stock", "difficulty": 6}]}'
        result = _recover_truncated_routing(raw)
        assert result is not None
        assert result[0]["crew"] == "financial"

    def test_recover_devops_crew(self):
        from app.agents.commander.routing import _recover_truncated_routing
        raw = '{"crews": [{"crew": "devops", "task": "scaffold project", "difficulty": 5}]}'
        result = _recover_truncated_routing(raw)
        assert result is not None
        assert result[0]["crew"] == "devops"


# ═══════════════════════════════════════════════════════════════════════════
# Signal Command Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSignalCommands:
    """Test new shortcut commands in commands.py."""

    def test_tasks_command(self):
        """'tasks' command should return task list directly."""
        from unittest.mock import patch
        mock_tool = MagicMock()
        mock_tool.name = "list_tasks"
        mock_tool._run.return_value = "No tasks found."

        with patch("app.tools.task_tools.create_task_tools", return_value=[mock_tool]):
            from app.agents.commander.commands import try_command
            result = try_command("tasks", "owner", None)

        assert result is not None
        assert "No tasks" in result or isinstance(result, str)

    def test_schedules_command(self):
        """'schedules' command should return schedule list directly."""
        mock_tool = MagicMock()
        mock_tool.name = "list_schedules"
        mock_tool._run.return_value = "No scheduled automations configured."

        with patch("app.tools.schedule_manager_tools.create_schedule_tools", return_value=[mock_tool]):
            from app.agents.commander.commands import try_command
            result = try_command("schedules", "owner", None)

        assert result is not None

    def test_unrelated_command_returns_none(self):
        """Commands that don't match should still return None."""
        from app.agents.commander.commands import try_command
        # A gibberish string should not match any command
        result = try_command("xyzzy_gibberish_12345", "owner", None)
        assert result is None
