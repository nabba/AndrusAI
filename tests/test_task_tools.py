"""
test_task_tools.py — Unit tests for SQLite-based task management tools.

Run: pytest tests/test_task_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def task_db(tmp_path):
    """Redirect task DB to temp directory."""
    db_path = tmp_path / "tasks.db"
    with patch("app.tools.task_tools._DB_PATH", db_path):
        yield db_path


class TestTaskToolsFactory:

    def test_returns_five_tools(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"create_task", "list_tasks", "update_task", "complete_task", "search_tasks"}


class TestCreateTask:

    def test_create_basic_task(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        result = create._run(title="Buy groceries")
        assert "created" in result.lower()
        assert "#" in result

    def test_create_task_with_all_fields(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        result = create._run(
            title="Deploy v2.0",
            description="Deploy the new version",
            priority="high",
            due_date="2026-05-01",
            labels="work,deploy"
        )
        assert "Deploy v2.0" in result
        assert "high" in result

    def test_invalid_priority_defaults_to_medium(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        result = create._run(title="Test", priority="extreme")
        assert "medium" in result


class TestListTasks:

    def test_list_empty_database(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        ls = next(t for t in tools if t.name == "list_tasks")
        result = ls._run(status="all")
        assert "No tasks found" in result

    def test_list_after_creating(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        ls = next(t for t in tools if t.name == "list_tasks")

        create._run(title="Task A", priority="high")
        create._run(title="Task B", priority="low")
        result = ls._run(status="all")

        assert "2 task(s)" in result
        assert "Task A" in result
        assert "Task B" in result

    def test_list_active_only(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        complete = next(t for t in tools if t.name == "complete_task")
        ls = next(t for t in tools if t.name == "list_tasks")

        create._run(title="Active task")
        result_create = create._run(title="Done task")
        # Extract task ID from result
        import re
        match = re.search(r"#(\d+)", result_create)
        if match:
            complete._run(task_id=int(match.group(1)))

        result = ls._run(status="active")
        assert "Active task" in result

    def test_list_by_priority(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        ls = next(t for t in tools if t.name == "list_tasks")

        create._run(title="Low task", priority="low")
        create._run(title="High task", priority="high")
        result = ls._run(status="all", priority="high")

        assert "High task" in result
        assert "Low task" not in result


class TestCompleteTask:

    def test_complete_task(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        complete = next(t for t in tools if t.name == "complete_task")

        create._run(title="My task")
        result = complete._run(task_id=1)
        assert "[x]" in result

    def test_complete_nonexistent(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        complete = next(t for t in tools if t.name == "complete_task")
        result = complete._run(task_id=999)
        assert "not found" in result


class TestUpdateTask:

    def test_update_priority(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        update = next(t for t in tools if t.name == "update_task")

        create._run(title="Change me")
        result = update._run(task_id=1, priority="urgent")
        assert "URGENT" in result

    def test_update_no_valid_fields(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        update = next(t for t in tools if t.name == "update_task")
        result = update._run(task_id=1)
        assert "No valid fields" in result


class TestSearchTasks:

    def test_search_by_title(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        create = next(t for t in tools if t.name == "create_task")
        search = next(t for t in tools if t.name == "search_tasks")

        create._run(title="Deploy backend")
        create._run(title="Fix frontend bug")
        result = search._run(query="backend")

        assert "1 match" in result
        assert "Deploy backend" in result

    def test_search_no_matches(self, task_db):
        from app.tools.task_tools import create_task_tools
        tools = create_task_tools("test")
        search = next(t for t in tools if t.name == "search_tasks")
        result = search._run(query="nonexistent")
        assert "No tasks matching" in result
