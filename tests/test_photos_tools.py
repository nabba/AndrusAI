"""Tests for app/tools/photos_tools.py — macOS Photos via AppleScript."""
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()


class TestCreatePhotosTools:
    def test_returns_empty_when_no_bridge(self, monkeypatch):
        monkeypatch.setattr("app.bridge_client.get_bridge", lambda _: None)
        from app.tools.photos_tools import create_photos_tools
        assert create_photos_tools("pim") == []

    def test_returns_empty_when_bridge_unavailable(self, monkeypatch):
        bridge = MagicMock()
        bridge.is_available.return_value = False
        monkeypatch.setattr("app.bridge_client.get_bridge", lambda _: bridge)
        from app.tools.photos_tools import create_photos_tools
        assert create_photos_tools("pim") == []

    def test_returns_three_tools_when_bridge_ok(self, monkeypatch):
        pytest.importorskip("crewai")
        bridge = MagicMock()
        bridge.is_available.return_value = True
        monkeypatch.setattr("app.bridge_client.get_bridge", lambda _: bridge)
        from app.tools.photos_tools import create_photos_tools
        tools = create_photos_tools("pim")
        names = {t.name for t in tools}
        assert names == {"photos_list_albums", "photos_count", "photos_recent"}


class TestToolExecution:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        pytest.importorskip("crewai")
        self.bridge = MagicMock()
        self.bridge.is_available.return_value = True
        self.calls = []

        def fake_execute(command, timeout=30):
            self.calls.append({"command": command, "timeout": timeout})
            return self.response

        self.bridge.execute = fake_execute
        self.response = {"stdout": "", "stderr": "", "returncode": 0}
        monkeypatch.setattr("app.bridge_client.get_bridge", lambda _: self.bridge)

        from app.tools.photos_tools import create_photos_tools
        self.tools = {t.name: t for t in create_photos_tools("pim")}

    def test_list_albums_parses_newline_output(self):
        self.response = {"stdout": "Favorites\nReykjavik 2024\nHelsinki winter\n",
                         "stderr": "", "returncode": 0}
        out = self.tools["photos_list_albums"]._run()
        assert "Albums (3)" in out
        assert "Favorites" in out
        assert "Reykjavik 2024" in out
        # Verifies AppleScript invoked via osascript
        assert self.calls[0]["command"][0] == "osascript"

    def test_list_albums_empty_library(self):
        self.response = {"stdout": "", "stderr": "", "returncode": 0}
        out = self.tools["photos_list_albums"]._run()
        assert "No albums found" in out

    def test_count_library_wide(self):
        self.response = {"stdout": "42073", "stderr": "", "returncode": 0}
        out = self.tools["photos_count"]._run()
        assert "library: 42073 photos" in out

    def test_count_in_specific_album(self):
        self.response = {"stdout": "128", "stderr": "", "returncode": 0}
        out = self.tools["photos_count"]._run(album="Reykjavik 2024")
        assert "Reykjavik 2024" in out
        assert "128 photos" in out
        # AppleScript should be parameterized with the album name
        script = self.calls[0]["command"][2]
        assert "Reykjavik 2024" in script

    def test_count_album_not_found(self):
        self.response = {"stdout": "ERROR: album not found", "stderr": "", "returncode": 0}
        out = self.tools["photos_count"]._run(album="ghost")
        assert "ERROR: album not found" in out

    def test_recent_photos_parses_tab_records(self):
        stdout = (
            "photo-id-1\tSaturday, 12 April 2026 at 14:33\tIMG_0001\n"
            "photo-id-2\tSunday, 13 April 2026 at 09:15\t\n"
        )
        self.response = {"stdout": stdout, "stderr": "", "returncode": 0}
        out = self.tools["photos_recent"]._run(n=2)
        assert "Last 2 photos" in out
        assert "IMG_0001" in out
        assert "12 April 2026" in out

    def test_recent_photos_clamps_range(self):
        self.response = {"stdout": "x\t2026\tname\n", "stderr": "", "returncode": 0}
        self.tools["photos_recent"]._run(n=9999)   # Should clamp to 100
        self.tools["photos_recent"]._run(n=-5)     # Should clamp to 1
        # Verify the number in the rendered AppleScript
        script_high = self.calls[0]["command"][2]
        script_low = self.calls[1]["command"][2]
        assert "100" in script_high
        assert " 1\n" in script_low or "1\n" in script_low

    def test_bridge_error_surfaces(self):
        # bridge_client returns {"error": "permission_denied", "detail": "..."} on 403
        self.response = {"error": "permission_denied",
                         "detail": "pim lacks 'execute' permission"}
        out = self.tools["photos_list_albums"]._run()
        assert "Photos error" in out
        assert "execute" in out


class TestAppleScriptSafety:
    def test_quote_escapes_backslashes_and_quotes(self):
        from app.tools.photos_tools import _quote_applescript_string
        out = _quote_applescript_string('alb "2024" \\test')
        assert out == 'alb \\"2024\\" \\\\test'

    def test_count_escapes_album_names_with_quotes(self, monkeypatch):
        pytest.importorskip("crewai")
        bridge = MagicMock()
        bridge.is_available.return_value = True
        scripts = []

        def fake_execute(command, timeout=30):
            scripts.append(command[2])
            return {"stdout": "0", "stderr": "", "returncode": 0}

        bridge.execute = fake_execute
        monkeypatch.setattr("app.bridge_client.get_bridge", lambda _: bridge)

        from app.tools.photos_tools import create_photos_tools
        tools = {t.name: t for t in create_photos_tools("pim")}
        tools["photos_count"]._run(album='Trip "summer"')
        # The inserted album string must be escaped so AppleScript doesn't break
        assert 'Trip \\"summer\\"' in scripts[0]
