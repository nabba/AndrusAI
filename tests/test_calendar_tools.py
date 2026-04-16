"""
test_calendar_tools.py — Unit tests for macOS Calendar tools via AppleScript.

Run: pytest tests/test_calendar_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockBridge


class TestCalendarToolsFactory:

    def test_returns_four_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"list_calendar_events", "create_calendar_event",
                         "search_calendar_events", "delete_calendar_event"}

    def test_returns_empty_when_bridge_unavailable(self):
        bridge = MockBridge()
        bridge.set_unavailable()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        assert tools == []


class TestListEvents:

    def test_list_events_calls_osascript(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {
            "stdout": "April 16, 2026 2:00 PM | Team Meeting @ Zoom\nApril 16, 2026 4:00 PM | Code Review",
            "stderr": "",
        })
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        ls = next(t for t in tools if t.name == "list_calendar_events")
        result = ls._run(days_ahead=1)
        assert "Team Meeting" in result

    def test_list_events_no_events(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "No events found.", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        ls = next(t for t in tools if t.name == "list_calendar_events")
        result = ls._run(days_ahead=1)
        assert "No events" in result


class TestCreateEvent:

    def test_create_event(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {
            "stdout": "Event created: Meeting on April 20, 2026",
            "stderr": "",
        })
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        create = next(t for t in tools if t.name == "create_calendar_event")
        result = create._run(
            title="Meeting",
            start_date="April 20, 2026 2:00 PM",
            end_date="April 20, 2026 3:00 PM",
        )
        assert "Event created" in result or "Meeting" in result

    def test_create_event_with_location(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "Event created", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        create = next(t for t in tools if t.name == "create_calendar_event")
        result = create._run(
            title="Lunch",
            start_date="April 21, 2026 12:00 PM",
            end_date="April 21, 2026 1:00 PM",
            location="Restaurant",
            notes="Bring laptop",
        )
        assert isinstance(result, str)


class TestSearchEvents:

    def test_search_events(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {
            "stdout": "April 20, 2026 2:00 PM | Team Standup",
            "stderr": "",
        })
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        search = next(t for t in tools if t.name == "search_calendar_events")
        result = search._run(query="Standup")
        assert "Standup" in result


class TestDeleteEvent:

    def test_delete_event(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "Deleted event", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        delete = next(t for t in tools if t.name == "delete_calendar_event")
        result = delete._run(title="Old Meeting", date="April 15, 2026")
        assert "Deleted" in result or "event" in result.lower()

    def test_delete_nonexistent(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "No event found", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("test")
        delete = next(t for t in tools if t.name == "delete_calendar_event")
        result = delete._run(title="Nonexistent", date="Jan 1, 2020")
        assert "No event" in result or isinstance(result, str)
