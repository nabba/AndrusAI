"""Tests for app/mcp/transports.py — StdioTransport, SSETransport, jsonrpc helpers."""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.mcp import transports  # noqa: E402


class TestJsonRpcHelpers:
    def test_next_request_id_monotonic(self):
        a = transports.next_request_id()
        b = transports.next_request_id()
        c = transports.next_request_id()
        assert a < b < c

    def test_jsonrpc_request_with_params(self):
        msg = transports.jsonrpc_request("tools/list", {"foo": "bar"})
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "tools/list"
        assert msg["params"] == {"foo": "bar"}
        assert isinstance(msg["id"], int)

    def test_jsonrpc_request_without_params(self):
        msg = transports.jsonrpc_request("ping")
        assert "params" not in msg
        assert msg["method"] == "ping"

    def test_jsonrpc_notification_has_no_id(self):
        msg = transports.jsonrpc_notification("notifications/initialized")
        assert "id" not in msg
        assert msg["method"] == "notifications/initialized"


class TestStdioTransport:
    def _make_process(self, response: dict = None):
        proc = MagicMock()
        proc.poll.return_value = None  # alive
        proc.stdin = MagicMock()
        proc.stdout.readline.return_value = (
            (json.dumps(response) + "\n").encode() if response else b""
        )
        return proc

    def test_start_spawns_subprocess(self):
        t = transports.StdioTransport("/bin/echo", ["hello"])
        with patch("subprocess.Popen") as popen:
            popen.return_value = self._make_process()
            t.start()
            assert popen.called
            cmd = popen.call_args.args[0]
            assert cmd == ["/bin/echo", "hello"]

    def test_start_idempotent_when_alive(self):
        t = transports.StdioTransport("/bin/echo")
        t._process = self._make_process()
        with patch("subprocess.Popen") as popen:
            t.start()
            popen.assert_not_called()

    def test_send_receive_roundtrip(self):
        t = transports.StdioTransport("/bin/echo")
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        t._process = self._make_process(resp)
        got = t.send_receive({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert got == resp

    def test_send_receive_raises_on_empty_response(self):
        t = transports.StdioTransport("/bin/echo")
        t._process = self._make_process(None)  # returns b""
        with pytest.raises(ConnectionError, match="crashed"):
            t.send_receive({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    def test_send_receive_raises_on_broken_pipe(self):
        t = transports.StdioTransport("/bin/echo")
        proc = self._make_process({"ok": True})
        proc.stdin.write.side_effect = BrokenPipeError("pipe closed")
        t._process = proc
        with pytest.raises(ConnectionError, match="Stdio write failed"):
            t.send_receive({"method": "x"})

    def test_send_notification_no_response_expected(self):
        t = transports.StdioTransport("/bin/echo")
        proc = self._make_process()
        t._process = proc
        t.send_notification({"method": "notif"})
        # Write was called, readline was NOT
        proc.stdin.write.assert_called_once()
        proc.stdout.readline.assert_not_called()

    def test_send_notification_swallows_errors(self):
        t = transports.StdioTransport("/bin/echo")
        proc = self._make_process()
        proc.stdin.write.side_effect = OSError("boom")
        t._process = proc
        # Should not raise
        t.send_notification({"method": "x"})

    def test_stop_terminates_process(self):
        t = transports.StdioTransport("/bin/echo")
        proc = self._make_process()
        t._process = proc
        t.stop()
        proc.terminate.assert_called_once()
        assert t._process is None

    def test_is_alive_when_poll_none(self):
        t = transports.StdioTransport("/bin/echo")
        t._process = self._make_process()
        assert t.is_alive is True

    def test_is_alive_when_exited(self):
        t = transports.StdioTransport("/bin/echo")
        proc = MagicMock()
        proc.poll.return_value = 0
        t._process = proc
        assert t.is_alive is False


class TestSSETransport:
    def test_ssrf_blocks_localhost(self, monkeypatch):
        t = transports.SSETransport("https://localhost:8080/sse")
        # _is_safe_url is loaded lazily inside start()
        with pytest.raises(ValueError, match="SSRF"):
            t.start()

    def test_ssrf_blocks_private_ip(self):
        t = transports.SSETransport("https://192.168.1.1/sse")
        with pytest.raises(ValueError, match="SSRF"):
            t.start()

    def test_is_alive_initially_false(self):
        t = transports.SSETransport("https://example.com/sse")
        assert t.is_alive is False

    def test_stop_clears_messages_url(self):
        t = transports.SSETransport("https://example.com/sse")
        t._messages_url = "https://example.com/msg"
        t.stop()
        assert t._messages_url is None
        assert t.is_alive is False
