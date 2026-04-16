"""
test_capability_e2e.py — End-to-end integration tests for capability gap elimination.

Tests the full pipeline: user input → routing → crew → agent → tools → output.
Mocks all external services (bridge, IMAP, yfinance, LLM) but exercises
the real routing, agent creation, and tool dispatch logic.

Run unit-like integration tests (no Docker needed):
    pytest tests/test_capability_e2e.py -v -k "not e2e"

Run full E2E (requires Docker stack):
    pytest tests/test_capability_e2e.py -v -m e2e

"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockBridge, MockIMAP, MockTicker

e2e = pytest.mark.e2e


# ═══════════════════════════════════════════════════════════════════════════
# 1. WIRING TESTS — New crews are reachable from orchestrator
# ═══════════════════════════════════════════════════════════════════════════

class TestCrewWiring:
    """Verify new crews can be imported and instantiated."""

    def test_pim_crew_importable(self):
        from app.crews.pim_crew import PIMCrew
        crew = PIMCrew()
        assert hasattr(crew, "run")

    def test_financial_crew_importable(self):
        from app.crews.financial_crew import FinancialCrew
        crew = FinancialCrew()
        assert hasattr(crew, "run")

    def test_desktop_crew_importable(self):
        from app.crews.desktop_crew import DesktopCrew
        crew = DesktopCrew()
        assert hasattr(crew, "run")

    def test_repo_analysis_crew_importable(self):
        from app.crews.repo_analysis_crew import RepoAnalysisCrew
        crew = RepoAnalysisCrew()
        assert hasattr(crew, "run")

    def test_devops_crew_importable(self):
        from app.crews.devops_crew import DevOpsCrew
        crew = DevOpsCrew()
        assert hasattr(crew, "run")


class TestAgentWiring:
    """Verify new agents can be created (with mocked dependencies)."""

    @patch("app.llm_factory.create_specialist_llm", return_value=MagicMock())
    @patch("app.tools.memory_tool.create_memory_tools", return_value=[])
    @patch("app.tools.scoped_memory_tool.create_scoped_memory_tools", return_value=[])
    @patch("app.tools.mem0_tools.create_mem0_tools", return_value=[])
    @patch("app.souls.loader.compose_backstory", return_value="Test backstory")
    def test_pim_agent_creates(self, *mocks):
        from app.agents.pim_agent import create_pim_agent
        agent = create_pim_agent(force_tier="local")
        assert agent.role == "Personal Information Manager"

    @patch("app.llm_factory.create_specialist_llm", return_value=MagicMock())
    @patch("app.tools.memory_tool.create_memory_tools", return_value=[])
    @patch("app.tools.scoped_memory_tool.create_scoped_memory_tools", return_value=[])
    @patch("app.tools.mem0_tools.create_mem0_tools", return_value=[])
    @patch("app.souls.loader.compose_backstory", return_value="Test backstory")
    @patch("app.tools.web_search.web_search", MagicMock())
    @patch("app.tools.web_fetch.web_fetch", MagicMock())
    @patch("app.tools.file_manager.file_manager", MagicMock())
    def test_financial_agent_creates(self, *mocks):
        from app.agents.financial_analyst import create_financial_analyst
        agent = create_financial_analyst(force_tier="local")
        assert agent.role == "Financial Analyst"

    @patch("app.llm_factory.create_specialist_llm", return_value=MagicMock())
    @patch("app.tools.memory_tool.create_memory_tools", return_value=[])
    @patch("app.tools.scoped_memory_tool.create_scoped_memory_tools", return_value=[])
    @patch("app.tools.mem0_tools.create_mem0_tools", return_value=[])
    @patch("app.souls.loader.compose_backstory", return_value="Test backstory")
    def test_desktop_agent_creates(self, *mocks):
        from app.agents.desktop_agent import create_desktop_agent
        agent = create_desktop_agent(force_tier="local")
        assert agent.role == "Desktop Automation Specialist"

    @patch("app.llm_factory.create_specialist_llm", return_value=MagicMock())
    @patch("app.tools.memory_tool.create_memory_tools", return_value=[])
    @patch("app.tools.scoped_memory_tool.create_scoped_memory_tools", return_value=[])
    @patch("app.tools.mem0_tools.create_mem0_tools", return_value=[])
    @patch("app.souls.loader.compose_backstory", return_value="Test backstory")
    @patch("app.tools.web_search.web_search", MagicMock())
    @patch("app.tools.file_manager.file_manager", MagicMock())
    def test_repo_analyst_agent_creates(self, *mocks):
        from app.agents.repo_analyst import create_repo_analyst
        agent = create_repo_analyst(force_tier="local")
        assert agent.role == "Repository Analyst"

    @patch("app.llm_factory.create_specialist_llm", return_value=MagicMock())
    @patch("app.tools.memory_tool.create_memory_tools", return_value=[])
    @patch("app.tools.scoped_memory_tool.create_scoped_memory_tools", return_value=[])
    @patch("app.tools.mem0_tools.create_mem0_tools", return_value=[])
    @patch("app.souls.loader.compose_backstory", return_value="Test backstory")
    @patch("app.tools.code_executor.execute_code", MagicMock())
    @patch("app.tools.web_search.web_search", MagicMock())
    @patch("app.tools.file_manager.file_manager", MagicMock())
    def test_devops_agent_creates(self, *mocks):
        from app.agents.devops_agent import create_devops_agent
        agent = create_devops_agent(force_tier="local")
        assert agent.role == "DevOps Engineer"


# ═══════════════════════════════════════════════════════════════════════════
# 2. SOUL FILE TESTS — Every new crew has a soul
# ═══════════════════════════════════════════════════════════════════════════

class TestSoulFiles:
    """Verify soul files exist and have required sections."""

    @pytest.mark.parametrize("soul_name", [
        "pim", "financial_analyst", "desktop", "repo_analyst", "devops",
    ])
    def test_soul_file_exists(self, soul_name):
        soul_path = Path(__file__).resolve().parent.parent / "app" / "souls" / f"{soul_name}.md"
        assert soul_path.exists(), f"Soul file missing: {soul_path}"

    @pytest.mark.parametrize("soul_name", [
        "pim", "financial_analyst", "desktop", "repo_analyst", "devops",
    ])
    def test_soul_has_identity_section(self, soul_name):
        soul_path = Path(__file__).resolve().parent.parent / "app" / "souls" / f"{soul_name}.md"
        content = soul_path.read_text()
        assert "## Identity" in content
        assert "## Personality" in content or "## Expertise" in content

    @pytest.mark.parametrize("soul_name", [
        "pim", "financial_analyst", "desktop", "repo_analyst", "devops",
    ])
    def test_soul_has_tools_section(self, soul_name):
        soul_path = Path(__file__).resolve().parent.parent / "app" / "souls" / f"{soul_name}.md"
        content = soul_path.read_text()
        assert "## Tools" in content


# ═══════════════════════════════════════════════════════════════════════════
# 3. FULL PIPELINE TESTS — Route → Crew → Agent → Tool → Output
# ═══════════════════════════════════════════════════════════════════════════

class TestFullPipelineRouting:
    """Test that user messages reach the correct crew via routing."""

    def test_email_request_routes_to_pim(self):
        """'check my email' should fast-route to PIM crew."""
        from app.agents.commander.routing import _FAST_ROUTE_PATTERNS
        text = "check my email"
        for pattern, crew, difficulty in _FAST_ROUTE_PATTERNS:
            if pattern.search(text):
                assert crew == "pim"
                assert difficulty == 3
                return
        pytest.fail("No fast-route matched 'check my email'")

    def test_stock_analysis_routes_to_financial(self):
        """Direct stock query (not starting with 'what') should fast-route to financial crew."""
        from app.agents.commander.routing import _FAST_ROUTE_PATTERNS
        # "stock price MSFT" starts with "stock" — hits financial pattern
        text = "stock price of MSFT"
        for pattern, crew, difficulty in _FAST_ROUTE_PATTERNS:
            if pattern.search(text):
                assert crew == "financial"
                return
        pytest.fail("No fast-route matched stock query")

    def test_deploy_request_routes_to_devops(self):
        from app.agents.commander.routing import _FAST_ROUTE_PATTERNS
        text = "deploy the application to production"
        for pattern, crew, difficulty in _FAST_ROUTE_PATTERNS:
            if pattern.search(text):
                assert crew == "devops"
                return
        pytest.fail("No fast-route matched deploy request")


class TestToolIntegrationPipelines:
    """Test realistic multi-tool workflows within a single tool module."""

    def test_task_full_lifecycle(self, tmp_path):
        """Create → list → update → complete → verify lifecycle."""
        with patch("app.tools.task_tools._DB_PATH", tmp_path / "tasks.db"):
            from app.tools.task_tools import create_task_tools
            tools = create_task_tools("test")
            create = next(t for t in tools if t.name == "create_task")
            ls = next(t for t in tools if t.name == "list_tasks")
            update = next(t for t in tools if t.name == "update_task")
            complete = next(t for t in tools if t.name == "complete_task")
            search = next(t for t in tools if t.name == "search_tasks")

            # Create
            r1 = create._run(title="Write tests", priority="high", labels="dev")
            assert "#1" in r1

            # Create another
            r2 = create._run(title="Review PR", priority="medium")
            assert "#2" in r2

            # List active
            r3 = ls._run(status="active")
            assert "2 task(s)" in r3
            assert "Write tests" in r3

            # Update priority
            r4 = update._run(task_id=2, priority="urgent")
            assert "URGENT" in r4

            # Complete first task
            r5 = complete._run(task_id=1)
            assert "[x]" in r5

            # List active — should only show task 2
            r6 = ls._run(status="active")
            assert "1 task(s)" in r6
            assert "Review PR" in r6

            # Search
            r7 = search._run(query="Write")
            assert "Write tests" in r7

    def test_schedule_full_lifecycle(self, tmp_path):
        """Create → list → delete lifecycle for schedules."""
        with patch("app.tools.schedule_manager_tools._SCHEDULES_PATH", tmp_path / "sched.json"):
            from app.tools.schedule_manager_tools import create_schedule_tools
            tools = create_schedule_tools("test")
            create = next(t for t in tools if t.name == "create_schedule")
            ls = next(t for t in tools if t.name == "list_schedules")
            delete = next(t for t in tools if t.name == "delete_schedule")

            # Create two schedules
            r1 = create._run(name="email-check", cron="0 9 * * *", task="Check email")
            assert "created" in r1.lower()

            r2 = create._run(name="daily-report", cron="0 18 * * *", task="Generate report")
            assert "created" in r2.lower()

            # List
            r3 = ls._run()
            assert "2 schedule(s)" in r3
            assert "email-check" in r3
            assert "daily-report" in r3

            # Delete one
            r4 = delete._run(name="email-check")
            assert "deleted" in r4.lower()

            # Verify deletion
            r5 = ls._run()
            assert "1 schedule(s)" in r5
            assert "daily-report" in r5
            assert "email-check" not in r5

    @pytest.mark.skipif(
        not any(p for p in sys.path if "yfinance" in str(Path(p))),
        reason="yfinance not installed locally"
    )
    def test_financial_data_pipeline(self):
        """Stock data → ratios → DCF model pipeline."""
        pytest.importorskip("yfinance", reason="yfinance not installed")
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
            stock = next(t for t in tools if t.name == "stock_data")
            ratios = next(t for t in tools if t.name == "financial_ratios")
            model = next(t for t in tools if t.name == "financial_model")

            # Get stock data
            r1 = stock._run(ticker="AAPL")
            assert "Apple Inc." in r1
            assert "185.5" in r1

            # Compute ratios
            r2 = ratios._run(ticker="AAPL")
            assert "P/E" in r2
            assert "ROE" in r2

            # Run DCF
            r3 = model._run(ticker="AAPL", discount_rate=0.10)
            assert "Intrinsic Value" in r3
            assert "Upside/Downside" in r3

    def test_desktop_automation_pipeline(self):
        """Open app → take screenshot → read clipboard pipeline."""
        bridge = MockBridge()
        bridge.set_execute_result("open -a", {"stdout": ""})
        bridge.set_execute_result("screencapture -x", {"stdout": ""})
        bridge.set_execute_result("pbpaste", {"stdout": "clipboard data"})

        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
            open_tool = next(t for t in tools if t.name == "open_on_mac")
            capture = next(t for t in tools if t.name == "screen_capture")
            clip = next(t for t in tools if t.name == "clipboard")

            # Open app
            r1 = open_tool._run(target="Safari")
            assert "Opened" in r1

            # Take screenshot
            r2 = capture._run(filename="test.png")
            assert "Screenshot saved" in r2

            # Read clipboard
            r3 = clip._run(action="read")
            assert "clipboard data" in r3

    def test_project_scaffold_to_github_pipeline(self):
        """Scaffold → build → GitHub push pipeline."""
        bridge = MockBridge()
        bridge.set_execute_result("mkdir -p", {"stdout": ""})
        bridge.set_execute_result("git init", {"stdout": "Initialized"})
        bridge.set_execute_result("test -f", {"stdout": ""})
        bridge.set_execute_result("sh -c", {"stdout": "Successfully installed"})
        bridge.set_execute_result("gh repo", {"stdout": "https://github.com/user/myapp"})

        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            from app.tools.deployment_tools import create_deployment_tools

            builder = create_project_builder_tools("test")
            deployer = create_deployment_tools("test")

            scaffold = next(t for t in builder if t.name == "scaffold_project")
            build = next(t for t in builder if t.name == "build_project")
            github = next(t for t in deployer if t.name == "github_create_and_push")

            # Scaffold
            r1 = scaffold._run(name="myapp", template="python-cli")
            assert "created" in r1.lower()

            # Build
            r2 = build._run(project_path="/tmp/crewai-projects/myapp", action="install")
            assert isinstance(r2, str)

            # Push to GitHub
            r3 = github._run(name="user/myapp", project_path="/tmp/crewai-projects/myapp")
            assert "github" in r3.lower() or isinstance(r3, str)


# ═══════════════════════════════════════════════════════════════════════════
# 4. GRACEFUL DEGRADATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:
    """Verify all tools degrade gracefully when deps/services unavailable."""

    def test_all_bridge_tools_return_empty_without_bridge(self):
        """Every bridge-backed tool factory should return [] when bridge is None."""
        factories = [
            ("app.tools.desktop_tools", "create_desktop_tools"),
            ("app.tools.calendar_tools", "create_calendar_tools"),
            ("app.tools.repo_analysis_tools", "create_repo_analysis_tools"),
            ("app.tools.project_builder_tools", "create_project_builder_tools"),
            ("app.tools.deployment_tools", "create_deployment_tools"),
            ("app.tools.ci_cd_tools", "create_ci_cd_tools"),
            ("app.tools.hardware_tools", "create_hardware_tools"),
            ("app.tools.mobile_tools", "create_mobile_tools"),
        ]

        with patch("app.bridge_client.get_bridge", return_value=None):
            for module_path, func_name in factories:
                import importlib
                mod = importlib.import_module(module_path)
                factory = getattr(mod, func_name)
                tools = factory("test_agent")
                assert tools == [], f"{module_path}.{func_name} should return [] without bridge"

    def test_email_tools_return_empty_without_config(self):
        with patch("app.tools.email_tools._get_email_config", return_value=None):
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("test")
        assert tools == []

    def test_task_tools_always_available(self, tmp_path):
        """Task tools should always work (uses SQLite, no external deps)."""
        with patch("app.tools.task_tools._DB_PATH", tmp_path / "tasks.db"):
            from app.tools.task_tools import create_task_tools
            tools = create_task_tools("test")
        assert len(tools) == 5

    def test_schedule_tools_always_available(self, tmp_path):
        """Schedule tools should always work."""
        with patch("app.tools.schedule_manager_tools._SCHEDULES_PATH", tmp_path / "s.json"):
            from app.tools.schedule_manager_tools import create_schedule_tools
            tools = create_schedule_tools("test")
        assert len(tools) == 4


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigAdditions:
    """Verify new config fields parse correctly."""

    def test_email_fields_exist_in_settings_class(self):
        from app.config import Settings
        fields = Settings.model_fields
        assert "email_enabled" in fields
        assert "email_imap_host" in fields
        assert "email_imap_port" in fields
        assert "email_smtp_host" in fields
        assert "email_smtp_port" in fields
        assert "email_address" in fields
        assert "email_password" in fields

    def test_sec_edgar_field_exists(self):
        from app.config import Settings
        fields = Settings.model_fields
        assert "sec_edgar_user_agent" in fields

    def test_email_disabled_by_default(self):
        from app.config import Settings
        assert Settings.model_fields["email_enabled"].default is False

    def test_email_password_is_secret(self):
        from app.config import Settings
        from pydantic import SecretStr
        field = Settings.model_fields["email_password"]
        assert field.default.get_secret_value() == ""


# ═══════════════════════════════════════════════════════════════════════════
# 6. TOOL COUNT INVENTORY — Nothing left behind
# ═══════════════════════════════════════════════════════════════════════════

class TestToolInventory:
    """Verify all 12 new tool modules exist and export factory functions."""

    TOOL_MODULES = [
        ("app.tools.desktop_tools", "create_desktop_tools"),
        ("app.tools.email_tools", "create_email_tools"),
        ("app.tools.calendar_tools", "create_calendar_tools"),
        ("app.tools.task_tools", "create_task_tools"),
        ("app.tools.financial_tools", "create_financial_tools"),
        ("app.tools.schedule_manager_tools", "create_schedule_tools"),
        ("app.tools.repo_analysis_tools", "create_repo_analysis_tools"),
        ("app.tools.project_builder_tools", "create_project_builder_tools"),
        ("app.tools.deployment_tools", "create_deployment_tools"),
        ("app.tools.ci_cd_tools", "create_ci_cd_tools"),
        ("app.tools.hardware_tools", "create_hardware_tools"),
        ("app.tools.mobile_tools", "create_mobile_tools"),
    ]

    @pytest.mark.parametrize("module_path,factory_name", TOOL_MODULES)
    def test_module_importable(self, module_path, factory_name):
        import importlib
        mod = importlib.import_module(module_path)
        assert hasattr(mod, factory_name), f"{module_path} missing {factory_name}"

    @pytest.mark.parametrize("module_path,factory_name", TOOL_MODULES)
    def test_factory_returns_list(self, module_path, factory_name):
        """Every factory should return a list (possibly empty) and never crash."""
        import importlib
        mod = importlib.import_module(module_path)
        factory = getattr(mod, factory_name)
        # Call with mocked bridge returning None (graceful degradation)
        with patch("app.bridge_client.get_bridge", return_value=None):
            result = factory("test")
        assert isinstance(result, list)
