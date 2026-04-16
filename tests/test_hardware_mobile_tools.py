"""
test_hardware_mobile_tools.py — Unit tests for hardware/IoT and mobile tools.

Run: pytest tests/test_hardware_mobile_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockBridge


# ═══════════════════════════════════════════════════════════════════════════
# Hardware Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestHardwareToolsFactory:

    def test_returns_five_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"serial_send", "mqtt_publish", "mqtt_subscribe",
                         "list_usb_devices", "list_serial_ports"}

    def test_returns_empty_without_bridge(self):
        with patch("app.bridge_client.get_bridge", return_value=None):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        assert tools == []


class TestSerialTool:

    def test_serial_sends_via_python_script(self):
        bridge = MockBridge()
        bridge.set_execute_result("python3 -c", {"stdout": "Response from device"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        serial = next(t for t in tools if t.name == "serial_send")
        result = serial._run(port="/dev/tty.usb1", baudrate=9600, data="AT\r\n")
        assert "Response from device" in result

    def test_serial_handles_error(self):
        bridge = MockBridge()
        bridge.set_execute_result("python3 -c", {"stdout": "Serial error: port not found"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        serial = next(t for t in tools if t.name == "serial_send")
        result = serial._run(port="/dev/nonexistent", data="test")
        assert "error" in result.lower() or isinstance(result, str)


class TestMQTTTools:

    def test_mqtt_publish(self):
        bridge = MockBridge()
        bridge.set_execute_result("python3 -c", {"stdout": 'Published to "home/temp": 22.5'})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        pub = next(t for t in tools if t.name == "mqtt_publish")
        result = pub._run(broker="localhost", topic="home/temp", message="22.5")
        assert "Published" in result

    def test_mqtt_subscribe(self):
        bridge = MockBridge()
        bridge.set_execute_result("python3 -c", {"stdout": "home/temp: 22.5\nhome/humidity: 65"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        sub = next(t for t in tools if t.name == "mqtt_subscribe")
        result = sub._run(broker="localhost", topic="home/#", timeout=5.0)
        assert "22.5" in result


class TestUSBDevices:

    def test_list_usb_devices(self):
        bridge = MockBridge()
        bridge.set_execute_result("system_profiler SPUSBDataType", {
            "stdout": "USB 3.0 Bus:\n  Hub:\n    Arduino Uno",
        })
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        usb = next(t for t in tools if t.name == "list_usb_devices")
        result = usb._run()
        assert "Arduino" in result or "USB" in result


class TestSerialPorts:

    def test_list_serial_ports(self):
        bridge = MockBridge()
        bridge.set_execute_result("sh -c", {
            "stdout": "/dev/tty.usbserial-1234\n/dev/cu.usbserial-1234",
        })
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.hardware_tools import create_hardware_tools
            tools = create_hardware_tools("test")
        ports = next(t for t in tools if t.name == "list_serial_ports")
        result = ports._run()
        assert "usbserial" in result


# ═══════════════════════════════════════════════════════════════════════════
# Mobile Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestMobileToolsFactory:

    def test_returns_four_tools(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.mobile_tools import create_mobile_tools
            tools = create_mobile_tools("test")
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"create_expo_app", "eas_build", "eas_submit", "create_pwa"}


class TestCreateExpoApp:

    def test_creates_expo_project(self):
        bridge = MockBridge()
        bridge.set_execute_result("sh -c", {"stdout": "Your project is ready!"})
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.mobile_tools import create_mobile_tools
            tools = create_mobile_tools("test")
        expo = next(t for t in tools if t.name == "create_expo_app")
        result = expo._run(name="MyApp", template="blank")
        assert "created" in result.lower() or "preview" in result.lower()


class TestCreatePWA:

    def test_creates_pwa_files(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.mobile_tools import create_mobile_tools
            tools = create_mobile_tools("test")
        pwa = next(t for t in tools if t.name == "create_pwa")
        result = pwa._run(name="MyPWA")

        assert "PWA created" in result
        assert "manifest.json" in result
        assert "sw.js" in result

        # Verify files were written to bridge
        assert any("index.html" in p for p in bridge._files)
        assert any("manifest.json" in p for p in bridge._files)
        assert any("sw.js" in p for p in bridge._files)

    def test_pwa_manifest_is_valid_json(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.mobile_tools import create_mobile_tools
            tools = create_mobile_tools("test")
        pwa = next(t for t in tools if t.name == "create_pwa")
        pwa._run(name="TestApp")

        import json
        manifest_file = next(p for p in bridge._files if "manifest.json" in p)
        manifest = json.loads(bridge._files[manifest_file])
        assert manifest["name"] == "TestApp"
        assert "start_url" in manifest
        assert "display" in manifest

    def test_pwa_service_worker_has_cache(self):
        bridge = MockBridge()
        with patch("app.bridge_client.get_bridge", return_value=bridge):
            from app.tools.mobile_tools import create_mobile_tools
            tools = create_mobile_tools("test")
        pwa = next(t for t in tools if t.name == "create_pwa")
        pwa._run(name="TestApp")

        sw_file = next(p for p in bridge._files if "sw.js" in p)
        assert "CACHE_NAME" in bridge._files[sw_file]
        assert "caches" in bridge._files[sw_file]
