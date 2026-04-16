"""
hardware_tools.py — Hardware and IoT communication tools via bridge.

Serial (pyserial) and MQTT (paho-mqtt) run on the HOST, not Docker.
The tools generate small Python scripts and execute them via bridge.

Host-side dependencies (not in Docker requirements.txt):
  pip install pyserial paho-mqtt

Usage:
    from app.tools.hardware_tools import create_hardware_tools
    tools = create_hardware_tools("automation")
"""

import logging

logger = logging.getLogger(__name__)


def create_hardware_tools(agent_id: str) -> list:
    """Create hardware/IoT communication tools via bridge.

    Returns empty list if bridge is unavailable.
    """
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _SerialInput(BaseModel):
        port: str = Field(description="Serial port path (e.g. '/dev/tty.usbserial-1234')")
        baudrate: int = Field(default=9600, description="Baud rate")
        data: str = Field(description="Data to send (string)")
        timeout: float = Field(default=5.0, description="Read timeout in seconds")

    class SerialTool(BaseTool):
        name: str = "serial_send"
        description: str = (
            "Send data to a serial port and read the response. "
            "Requires pyserial installed on the host."
        )
        args_schema: Type[BaseModel] = _SerialInput

        def _run(self, port: str, baudrate: int = 9600, data: str = "", timeout: float = 5.0) -> str:
            script = f"""\
import serial
try:
    ser = serial.Serial('{port}', {baudrate}, timeout={timeout})
    ser.write({repr(data.encode())})
    response = ser.read(4096).decode('utf-8', errors='replace')
    ser.close()
    print(response if response else '(no response)')
except Exception as e:
    print(f'Serial error: {{e}}')
"""
            result = bridge.execute(["python3", "-c", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return result.get("stdout", "").strip()

    class _MQTTPublishInput(BaseModel):
        broker: str = Field(description="MQTT broker address (e.g. 'localhost' or '192.168.1.100')")
        topic: str = Field(description="MQTT topic to publish to")
        message: str = Field(description="Message payload")
        port: int = Field(default=1883, description="MQTT broker port")

    class MQTTPublishTool(BaseTool):
        name: str = "mqtt_publish"
        description: str = (
            "Publish a message to an MQTT topic. "
            "Requires paho-mqtt installed on the host."
        )
        args_schema: Type[BaseModel] = _MQTTPublishInput

        def _run(self, broker: str, topic: str, message: str, port: int = 1883) -> str:
            script = f"""\
import paho.mqtt.client as mqtt
try:
    client = mqtt.Client()
    client.connect('{broker}', {port}, 10)
    result = client.publish('{topic}', '{message}')
    result.wait_for_publish(timeout=5)
    client.disconnect()
    print(f'Published to {{"{topic}"}}: {{"{message}"}}')
except Exception as e:
    print(f'MQTT error: {{e}}')
"""
            result = bridge.execute(["python3", "-c", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return result.get("stdout", "").strip()

    class _MQTTSubscribeInput(BaseModel):
        broker: str = Field(description="MQTT broker address")
        topic: str = Field(description="MQTT topic to subscribe to")
        timeout: float = Field(default=10.0, description="Listen timeout in seconds")
        port: int = Field(default=1883, description="MQTT broker port")

    class MQTTSubscribeTool(BaseTool):
        name: str = "mqtt_subscribe"
        description: str = (
            "Subscribe to an MQTT topic and listen for messages (with timeout). "
            "Returns received messages."
        )
        args_schema: Type[BaseModel] = _MQTTSubscribeInput

        def _run(self, broker: str, topic: str, timeout: float = 10.0, port: int = 1883) -> str:
            script = f"""\
import paho.mqtt.client as mqtt
import time
messages = []
def on_message(client, userdata, msg):
    messages.append(f'{{msg.topic}}: {{msg.payload.decode()}}')
try:
    client = mqtt.Client()
    client.on_message = on_message
    client.connect('{broker}', {port}, 10)
    client.subscribe('{topic}')
    client.loop_start()
    time.sleep({timeout})
    client.loop_stop()
    client.disconnect()
    if messages:
        print('\\n'.join(messages))
    else:
        print('No messages received within timeout.')
except Exception as e:
    print(f'MQTT error: {{e}}')
"""
            result = bridge.execute(["python3", "-c", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return result.get("stdout", "").strip()

    class _USBInput(BaseModel):
        pass

    class ListUSBDevicesTool(BaseTool):
        name: str = "list_usb_devices"
        description: str = "List connected USB devices on the macOS host."
        args_schema: Type[BaseModel] = _USBInput

        def _run(self) -> str:
            result = bridge.execute(["system_profiler", "SPUSBDataType", "-detailLevel", "mini"])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            return output[:3000] if output else "No USB devices found."

    class _ListSerialInput(BaseModel):
        pass

    class ListSerialPortsTool(BaseTool):
        name: str = "list_serial_ports"
        description: str = "List available serial ports on the macOS host."
        args_schema: Type[BaseModel] = _ListSerialInput

        def _run(self) -> str:
            result = bridge.execute(["sh", "-c", "ls /dev/tty.* /dev/cu.* 2>/dev/null"])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "").strip()
            return output if output else "No serial ports found."

    return [
        SerialTool(),
        MQTTPublishTool(),
        MQTTSubscribeTool(),
        ListUSBDevicesTool(),
        ListSerialPortsTool(),
    ]
