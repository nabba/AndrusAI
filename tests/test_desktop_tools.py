"""
test_desktop_tools.py — Unit tests for macOS desktop automation tools.

Run: pytest tests/test_desktop_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockBridge


class TestDesktopToolsFactory:
    """Test tool creation and graceful degradation."""

    def test_returns_tools_when_bridge_available(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        assert len(tools) == 7
        names = {t.name for t in tools}
        assert "run_applescript" in names
        assert "run_jxa" in names
        assert "screen_capture" in names
        assert "clipboard" in names
        assert "run_shortcut" in names
        assert "open_on_mac" in names
        assert "manage_window" in names

    def test_returns_empty_when_bridge_unavailable(self):
        bridge = MockBridge()
        bridge.set_unavailable()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        assert tools == []

    def test_returns_empty_when_no_bridge(self):
        with patch("app.bridge_client.get_bridge", return_value=None):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        assert tools == []

    def test_returns_empty_on_import_error(self):
        with patch("app.bridge_client.get_bridge", side_effect=ImportError):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        assert tools == []


class TestAppleScriptTool:

    def test_run_applescript_returns_output(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "result text", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        applescript = next(t for t in tools if t.name == "run_applescript")
        result = applescript._run(script='tell application "Finder" to get name of front window')
        assert "result text" in result

    def test_run_applescript_handles_error(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"error": "exec_failed", "detail": "permission denied"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        applescript = next(t for t in tools if t.name == "run_applescript")
        result = applescript._run(script='bad script')
        assert "Error:" in result

    def test_run_applescript_shows_stderr_on_empty_stdout(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "", "stderr": "syntax error"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        applescript = next(t for t in tools if t.name == "run_applescript")
        result = applescript._run(script='bad')
        assert "AppleScript error" in result


class TestScreenCaptureTool:

    def test_screen_capture_calls_screencapture(self):
        bridge = MockBridge()
        bridge.set_execute_result("screencapture -x", {"stdout": "", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        capture = next(t for t in tools if t.name == "screen_capture")
        result = capture._run(filename="test.png")
        assert "Screenshot saved" in result


class TestClipboardTool:

    def test_clipboard_read(self):
        bridge = MockBridge()
        bridge.set_execute_result("pbpaste", {"stdout": "clipboard content"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        clip = next(t for t in tools if t.name == "clipboard")
        result = clip._run(action="read")
        assert "clipboard content" in result

    def test_clipboard_write(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "", "stderr": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        clip = next(t for t in tools if t.name == "clipboard")
        result = clip._run(action="write", content="hello")
        assert "Clipboard set" in result

    def test_clipboard_invalid_action(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        clip = next(t for t in tools if t.name == "clipboard")
        result = clip._run(action="invalid")
        assert "Invalid action" in result


class TestOpenTool:

    def test_open_url(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        open_tool = next(t for t in tools if t.name == "open_on_mac")
        result = open_tool._run(target="https://example.com")
        assert "Opened" in result

    def test_open_app(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        open_tool = next(t for t in tools if t.name == "open_on_mac")
        result = open_tool._run(target="Safari")
        assert "Opened" in result


class TestWindowManagerTool:

    def test_list_windows(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": "Finder, Safari, Terminal"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        wm = next(t for t in tools if t.name == "manage_window")
        result = wm._run(action="list")
        assert "Finder" in result

    def test_focus_app(self):
        bridge = MockBridge()
        bridge.set_execute_result("osascript -e", {"stdout": ""})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        wm = next(t for t in tools if t.name == "manage_window")
        result = wm._run(action="focus", app_name="Safari")
        assert "completed" in result or "Safari" in result

    def test_invalid_action_without_app(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.desktop_tools import create_desktop_tools
            tools = create_desktop_tools("test")
        wm = next(t for t in tools if t.name == "manage_window")
        result = wm._run(action="focus", app_name="")
        assert "Invalid" in result
