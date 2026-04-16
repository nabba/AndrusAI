"""
test_schedule_tools.py — Unit tests for user-configurable schedule management.

Run: pytest tests/test_schedule_tools.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def schedule_file(tmp_path):
    """Redirect schedule storage to temp directory."""
    sched_path = tmp_path / "schedules.json"
    with patch("app.tools.schedule_manager_tools._SCHEDULES_PATH", sched_path):
        yield sched_path


class TestScheduleToolsFactory:

    def test_returns_four_tools(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"create_schedule", "list_schedules", "delete_schedule", "trigger_schedule"}


class TestCreateSchedule:

    def test_create_valid_schedule(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        create = next(t for t in tools if t.name == "create_schedule")
        result = create._run(
            name="morning-email",
            cron="0 9 * * *",
            task="Check email and summarize",
        )
        assert "created" in result.lower()
        assert "morning-email" in result

        # Verify file was written
        data = json.loads(schedule_file.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "morning-email"
        assert data[0]["cron"] == "0 9 * * *"
        assert data[0]["enabled"] is True

    def test_create_duplicate_name_rejected(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        create = next(t for t in tools if t.name == "create_schedule")
        create._run(name="test", cron="0 9 * * *", task="task1")
        result = create._run(name="test", cron="0 10 * * *", task="task2")
        assert "already exists" in result

    def test_invalid_cron_rejected(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        create = next(t for t in tools if t.name == "create_schedule")
        result = create._run(name="bad", cron="invalid cron", task="task")
        assert "Invalid cron" in result

    def test_valid_cron_expressions(self, schedule_file):
        from app.tools.schedule_manager_tools import _validate_cron
        assert _validate_cron("0 9 * * *") is True      # daily 9am
        assert _validate_cron("*/5 * * * *") is True     # every 5 min
        assert _validate_cron("0 9 * * 1-5") is True     # weekdays 9am
        assert _validate_cron("bad") is False
        assert _validate_cron("") is False


class TestListSchedules:

    def test_list_empty(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        ls = next(t for t in tools if t.name == "list_schedules")
        result = ls._run()
        assert "No scheduled" in result

    def test_list_shows_schedules(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        create = next(t for t in tools if t.name == "create_schedule")
        ls = next(t for t in tools if t.name == "list_schedules")

        create._run(name="job1", cron="0 9 * * *", task="Task one")
        create._run(name="job2", cron="0 17 * * *", task="Task two")
        result = ls._run()

        assert "2 schedule(s)" in result
        assert "job1" in result
        assert "job2" in result
        assert "[ON]" in result


class TestDeleteSchedule:

    def test_delete_existing(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        create = next(t for t in tools if t.name == "create_schedule")
        delete = next(t for t in tools if t.name == "delete_schedule")
        ls = next(t for t in tools if t.name == "list_schedules")

        create._run(name="to-delete", cron="0 9 * * *", task="task")
        result = delete._run(name="to-delete")
        assert "deleted" in result.lower()

        # Verify it's gone
        list_result = ls._run()
        assert "No scheduled" in list_result

    def test_delete_nonexistent(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        delete = next(t for t in tools if t.name == "delete_schedule")
        result = delete._run(name="ghost")
        assert "not found" in result


class TestTriggerSchedule:

    def test_trigger_nonexistent(self, schedule_file):
        from app.tools.schedule_manager_tools import create_schedule_tools
        tools = create_schedule_tools("test")
        trigger = next(t for t in tools if t.name == "trigger_schedule")
        result = trigger._run(name="nonexistent")
        assert "not found" in result


class TestRegisterUserSchedules:

    def test_register_loads_from_file(self, schedule_file):
        # Write schedules directly to file
        schedule_file.write_text(json.dumps([
            {"name": "job1", "cron": "0 9 * * *", "task": "test task", "enabled": True},
            {"name": "job2", "cron": "0 17 * * *", "task": "test task 2", "enabled": False},
        ]))

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = None  # No existing job

        from app.tools.schedule_manager_tools import register_user_schedules
        count = register_user_schedules(mock_scheduler)

        assert count == 1  # Only job1 is enabled
        mock_scheduler.add_job.assert_called_once()

    def test_register_empty_file(self, schedule_file):
        mock_scheduler = MagicMock()
        from app.tools.schedule_manager_tools import register_user_schedules
        count = register_user_schedules(mock_scheduler)
        assert count == 0

    def test_register_skips_existing_jobs(self, schedule_file):
        schedule_file.write_text(json.dumps([
            {"name": "existing", "cron": "0 9 * * *", "task": "task", "enabled": True},
        ]))

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = MagicMock()  # Job already exists

        from app.tools.schedule_manager_tools import register_user_schedules
        count = register_user_schedules(mock_scheduler)
        assert count == 0
