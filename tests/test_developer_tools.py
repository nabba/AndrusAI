"""
test_developer_tools.py — Unit tests for repo analysis, project builder,
deployment, and CI/CD tools.

Run: pytest tests/test_developer_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockBridge


# ═══════════════════════════════════════════════════════════════════════════
# Repo Analysis Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestRepoAnalysisToolsFactory:

    def test_returns_five_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert "clone_repo" in names
        assert "analyze_repo_structure" in names
        assert "repo_metrics" in names
        assert "github_cli" in names
        assert "generate_architecture_diagram" in names

    def test_returns_empty_without_bridge(self):
        with patch("app.bridge_client.get_bridge", return_value=None):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        assert tools == []


class TestCloneRepoTool:

    def test_clone_expands_github_shorthand(self):
        bridge = MockBridge()
        bridge.set_execute_result("rm -rf", {"stdout": ""})
        bridge.set_execute_result("git clone", {"stdout": "Cloning..."})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        clone = next(t for t in tools if t.name == "clone_repo")
        result = clone._run(repo_url="facebook/react")
        assert "cloned" in result.lower()

    def test_clone_handles_error(self):
        bridge = MockBridge()
        bridge.set_execute_result("rm -rf", {"stdout": ""})
        bridge.set_execute_result("git clone", {"error": "auth_failed", "detail": "Access denied"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        clone = next(t for t in tools if t.name == "clone_repo")
        result = clone._run(repo_url="private/repo")
        assert "Error" in result


class TestAnalyzeRepoStructure:

    def test_analyzes_file_list(self):
        bridge = MockBridge()
        # Override list_files to return rich data
        bridge.list_files = lambda path, pattern, recursive: {"files": [
            {"name": "src/main.py"}, {"name": "src/utils.py"},
            {"name": "tests/test_main.py"}, {"name": "package.json"},
            {"name": "tsconfig.json"}, {"name": "README.md"},
            {"name": "Dockerfile"}, {"name": ".github/workflows/ci.yml"},
        ]}
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        analyze = next(t for t in tools if t.name == "analyze_repo_structure")
        result = analyze._run(repo_path="/tmp/crewai-repos/test")

        assert "Repository Analysis" in result
        assert "Total files: 8" in result
        assert "Python" in result
        assert "Node.js" in result or "TypeScript" in result
        assert "Docker" in result


class TestRepoMetrics:

    def test_repo_metrics_health_checks(self):
        bridge = MockBridge()
        # test -e returns success for some files
        bridge.set_execute_result("test -e", {"stdout": ""})
        bridge.set_execute_result("sh -c", {"stdout": "1234 total"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        metrics = next(t for t in tools if t.name == "repo_metrics")
        result = metrics._run(repo_path="/tmp/test")
        assert "Repository Metrics" in result
        assert "Project Health" in result


class TestGitHubCLI:

    def test_gh_command(self):
        bridge = MockBridge()
        bridge.set_execute_result("gh repo", {"stdout": "owner/repo1\nowner/repo2", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        gh = next(t for t in tools if t.name == "github_cli")
        result = gh._run(command="repo list")
        assert "repo1" in result


class TestArchitectureDiagram:

    def test_generates_dot_format(self):
        bridge = MockBridge()
        bridge.list_files = lambda p, pat, recursive: {"files": [
            {"name": "src/main.py"}, {"name": "src/utils/helpers.py"},
            {"name": "tests/test_main.py"}, {"name": "docs/README.md"},
        ]}
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.repo_analysis_tools import create_repo_analysis_tools
            tools = create_repo_analysis_tools("test")
        diagram = next(t for t in tools if t.name == "generate_architecture_diagram")
        result = diagram._run(repo_path="/tmp/test", focus="directories")
        assert "digraph" in result
        assert "rankdir" in result
        assert "src" in result


# ═══════════════════════════════════════════════════════════════════════════
# Project Builder Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestProjectBuilderFactory:

    def test_returns_two_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            tools = create_project_builder_tools("test")
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"scaffold_project", "build_project"}


class TestScaffoldProject:

    def test_scaffold_python_cli(self):
        bridge = MockBridge()
        bridge.set_execute_result("mkdir -p", {"stdout": ""})
        bridge.set_execute_result("git init", {"stdout": "Initialized"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            tools = create_project_builder_tools("test")
        scaffold = next(t for t in tools if t.name == "scaffold_project")
        result = scaffold._run(name="myapp", template="python-cli")
        assert "created" in result.lower()
        assert "myapp" in result

    def test_scaffold_all_templates_valid(self):
        from app.tools.project_builder_tools import _TEMPLATES
        assert "python-cli" in _TEMPLATES
        assert "python-api" in _TEMPLATES
        assert "node-api" in _TEMPLATES
        assert "static-site" in _TEMPLATES
        for name, tmpl in _TEMPLATES.items():
            assert "description" in tmpl
            assert "files" in tmpl
            assert len(tmpl["files"]) > 0

    def test_scaffold_invalid_template(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            tools = create_project_builder_tools("test")
        scaffold = next(t for t in tools if t.name == "scaffold_project")
        result = scaffold._run(name="myapp", template="invalid-template")
        assert "Unknown template" in result


class TestBuildProject:

    def test_build_detect_python(self):
        bridge = MockBridge()
        # First test -f call succeeds (has pyproject.toml)
        bridge.set_execute_result("test -f", {"stdout": ""})
        bridge.set_execute_result("sh -c", {"stdout": "Successfully installed..."})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            tools = create_project_builder_tools("test")
        build = next(t for t in tools if t.name == "build_project")
        result = build._run(project_path="/tmp/test", action="install")
        assert isinstance(result, str)

    def test_build_invalid_action(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.project_builder_tools import create_project_builder_tools
            tools = create_project_builder_tools("test")
        build = next(t for t in tools if t.name == "build_project")
        result = build._run(project_path="/tmp/test", action="invalid_action")
        assert "Unknown action" in result


# ═══════════════════════════════════════════════════════════════════════════
# Deployment Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestDeploymentToolsFactory:

    def test_returns_three_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.deployment_tools import create_deployment_tools
            tools = create_deployment_tools("test")
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"github_create_and_push", "docker_build", "deploy"}


class TestDeployTool:

    def test_deploy_unknown_target(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.deployment_tools import create_deployment_tools
            tools = create_deployment_tools("test")
        deploy = next(t for t in tools if t.name == "deploy")
        result = deploy._run(project_path="/tmp/test", target="unknown_cloud")
        assert "Unknown target" in result


# ═══════════════════════════════════════════════════════════════════════════
# CI/CD Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestCICDToolsFactory:

    def test_returns_three_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.ci_cd_tools import create_ci_cd_tools
            tools = create_ci_cd_tools("test")
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"generate_github_actions", "generate_dockerfile", "generate_makefile"}


class TestGenerateGitHubActions:

    def test_generates_python_workflow(self):
        bridge = MockBridge()
        # test -f succeeds for pyproject.toml
        bridge.set_execute_result("test -f", {"stdout": ""})
        bridge.set_execute_result("mkdir -p", {"stdout": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.ci_cd_tools import create_ci_cd_tools
            tools = create_ci_cd_tools("test")
        gen = next(t for t in tools if t.name == "generate_github_actions")
        result = gen._run(project_path="/tmp/test")
        assert "ci.yml" in result.lower() or "CI" in result


class TestWorkflowTemplates:

    def test_python_ci_workflow_is_valid_yaml(self):
        from app.tools.ci_cd_tools import _python_ci_workflow
        wf = _python_ci_workflow(False, "ci")
        assert "name: CI" in wf
        assert "pytest" in wf
        assert "python-version" in wf

    def test_node_ci_workflow_is_valid_yaml(self):
        from app.tools.ci_cd_tools import _node_ci_workflow
        wf = _node_ci_workflow(False, "ci")
        assert "name: CI" in wf
        assert "npm" in wf
        assert "node-version" in wf

    def test_python_makefile_has_targets(self):
        from app.tools.ci_cd_tools import _python_makefile
        mf = _python_makefile()
        assert "install:" in mf
        assert "test:" in mf
        assert "build:" in mf
        assert "clean:" in mf

    def test_dockerfile_templates(self):
        from app.tools.ci_cd_tools import _python_dockerfile, _node_dockerfile
        py = _python_dockerfile()
        node = _node_dockerfile()
        assert "FROM python:" in py
        assert "FROM node:" in node
        assert "EXPOSE" in node
