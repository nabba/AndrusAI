"""
mcp/transports.py — Shared transport layer for MCP stdio and SSE connections.

Used by both the MCP server (exposing AndrusAI resources) and the MCP client
(consuming external server tools). One implementation, two consumers.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

_request_counter = 0
_counter_lock = threading.Lock()


def next_request_id() -> int:
    global _request_counter
    with _counter_lock:
        _request_counter += 1
        return _request_counter


def jsonrpc_request(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": next_request_id(), "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def jsonrpc_notification(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


class StdioTransport:
    """Subprocess-based MCP connection. Thread-safe via internal lock."""

    def __init__(self, command: str, args: list[str] = None, env: dict[str, str] = None):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            return
        env = {**os.environ, **self._env}
        self._process = subprocess.Popen(
            [self._command] + self._args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=0,
        )

    def send_receive(self, message: dict) -> dict:
        with self._lock:
            if not self._process or self._process.poll() is not None:
                self.start()
            payload = json.dumps(message) + "\n"
            try:
                self._process.stdin.write(payload.encode())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ConnectionError(f"Stdio write failed: {exc}") from exc
            raw = self._process.stdout.readline()
            if not raw:
                raise ConnectionError("Empty response — server may have crashed")
            return json.loads(raw)

    def send_notification(self, message: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        with self._lock:
            if not self._process or self._process.poll() is not None:
                return
            try:
                payload = json.dumps(message) + "\n"
                self._process.stdin.write(payload.encode())
                self._process.stdin.flush()
            except Exception:
                pass

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None


class SSETransport:
    """HTTP SSE-based MCP connection. Thread-safe via internal lock.

    SSRF-protected: validates URLs before connection.
    """
    def __init__(self, url: str, timeout: float = 30.0):
        self._url = url
        self._timeout = timeout
        self._messages_url: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        from app.tools.web_fetch import _is_safe_url
        safe, reason = _is_safe_url(self._url)
        if not safe:
            raise ValueError(f"SSE URL blocked (SSRF): {reason}")
        import httpx
        try:
            with httpx.stream("GET", self._url, timeout=self._timeout) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        self._messages_url = data.get("messagesUrl", "")
                        break
        except Exception as exc:
            raise ConnectionError(f"SSE connect failed: {exc}") from exc
        if not self._messages_url:
            raise ConnectionError("SSE: no messagesUrl received")

    def send_receive(self, message: dict) -> dict:
        with self._lock:
            if not self._messages_url:
                self.start()
            import httpx
            resp = httpx.post(self._messages_url, json=message, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()

    def send_notification(self, message: dict) -> None:
        with self._lock:
            if not self._messages_url:
                return
            try:
                import httpx
                httpx.post(self._messages_url, json=message, timeout=5)
            except Exception:
                pass

    def stop(self) -> None:
        self._messages_url = None

    @property
    def is_alive(self) -> bool:
        return self._messages_url is not None
