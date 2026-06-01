"""Tests for the gateway-side evolver spawn client
(app/self_improvement/evolver_spawn.py). The Docker calls are exercised through
an injected fake transport, so the create→start→wait→logs→extract→remove flow
is validated without a Docker daemon.
"""
from __future__ import annotations

import json

import pytest

try:
    from app.self_improvement import evolver_spawn as es
    from app.self_improvement.evolver_job import _RESULT_BEGIN, _RESULT_END
except Exception as exc:  # pragma: no cover
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


def test_docker_base_from_tcp(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-proxy:2375")
    assert es._docker_base() == "http://docker-proxy:2375"


def test_docker_base_default(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    assert es._docker_base() == "http://docker-proxy:2375"


def test_build_create_payload_includes_job_and_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    job = {"target_file": "app/x.py", "approach": "do y"}
    payload = es.build_create_payload("img:1", job)

    assert payload["Image"] == "img:1"
    assert payload["Tty"] is True
    assert payload["HostConfig"]["AutoRemove"] is False
    assert payload["HostConfig"]["NetworkMode"] == "bridge"
    env = payload["Env"]
    assert any(e.startswith("AAI_EVOLVE_JOB=") for e in env)
    # the job round-trips through the env var
    job_env = next(e for e in env if e.startswith("AAI_EVOLVE_JOB="))
    assert json.loads(job_env[len("AAI_EVOLVE_JOB=") :]) == job
    assert "ANTHROPIC_API_KEY=sk-test" in env
    assert not any(e.startswith("OPENAI_API_KEY=") for e in env)


def test_build_create_payload_forwards_required_settings_keys(monkeypatch):
    # Regression for the 2026-05-29 bug: the app's Settings() requires these
    # non-LLM keys, so without forwarding them the container ValidationErrors
    # at import before the job runs.
    monkeypatch.setenv("BRAVE_API_KEY", "brave-x")
    monkeypatch.setenv("SIGNAL_BOT_NUMBER", "+100")
    monkeypatch.setenv("SIGNAL_OWNER_NUMBER", "+200")
    monkeypatch.setenv("GATEWAY_SECRET", "gw-secret")
    payload = es.build_create_payload("img:1", {"target_file": "app/x.py"})
    env = payload["Env"]
    assert "BRAVE_API_KEY=brave-x" in env
    assert "SIGNAL_BOT_NUMBER=+100" in env
    assert "SIGNAL_OWNER_NUMBER=+200" in env
    assert "GATEWAY_SECRET=gw-secret" in env


def test_build_create_payload_makes_evolver_the_oom_victim():
    # The throwaway evolver must be the OOM kill preference so host memory
    # pressure never takes down the production gateway (2026-05-29 incident).
    payload = es.build_create_payload("img:1", {"target_file": "app/x.py"})
    assert payload["HostConfig"]["OomScoreAdj"] == 900


def test_build_create_payload_network_mode_default_and_override():
    # Default keeps the evolver on bridge (internet for in-container LLM calls),
    # so the verified-mutation path is byte-for-byte unchanged.
    assert es.build_create_payload("img:1", {})["HostConfig"]["NetworkMode"] == "bridge"
    # The research experiment runner isolates the container: a self-contained
    # measurement script gets no network at all — the kernel enforces what the
    # design prompt only requested.
    isolated = es.build_create_payload("img:1", {}, network_mode="none")
    assert isolated["HostConfig"]["NetworkMode"] == "none"


def test_run_container_job_threads_network_mode_into_create():
    seen: dict = {}

    def fake_tx(method, path, body=None, timeout=None):
        if path == "/containers/create":
            seen["network_mode"] = body["HostConfig"]["NetworkMode"]
            return 201, json.dumps({"Id": "cid"}).encode()
        if path.endswith("/start"):
            return 204, b""
        if path.endswith("/wait"):
            return 200, b"{}"
        if "/logs" in path:
            return 200, _result_logs({"ok": True, "result": {}})
        if method == "DELETE":
            return 200, b""
        return 404, b""

    es.run_container_job({"x": 1}, image="img:1", network_mode="none", transport=fake_tx)
    assert seen["network_mode"] == "none"


def _result_logs(payload: dict) -> bytes:
    return (
        "INFO noise\n"
        + _RESULT_BEGIN
        + json.dumps(payload)
        + _RESULT_END
        + "\n"
    ).encode("utf-8")


def test_run_evolver_job_happy_path():
    calls = []

    def fake_tx(method, path, body=None, timeout=None):
        calls.append((method, path))
        if path == "/containers/create":
            return 201, json.dumps({"Id": "abc123"}).encode()
        if path.endswith("/start"):
            return 204, b""
        if path.endswith("/wait"):
            return 200, json.dumps({"StatusCode": 0}).encode()
        if "/logs" in path:
            return 200, _result_logs({"ok": True, "result": {"verdict": {"verdict": "IMPROVED"}}})
        if method == "DELETE":
            return 200, b""
        return 404, b""

    res = es.run_evolver_job(
        {"target_file": "app/x.py", "approach": "y"}, transport=fake_tx
    )
    assert res["ok"] is True
    assert res["result"]["verdict"]["verdict"] == "IMPROVED"
    # The container was removed (cleanup ran).
    assert any(m == "DELETE" for m, _ in calls)


def test_run_evolver_job_create_failure():
    def fake_tx(method, path, body=None, timeout=None):
        return 500, b"boom"

    res = es.run_evolver_job({"target_file": "app/x.py"}, transport=fake_tx)
    assert res["ok"] is False
    assert "create failed" in res["error"]


def test_run_evolver_job_removes_container_even_on_log_error():
    deleted = []

    def fake_tx(method, path, body=None, timeout=None):
        if path == "/containers/create":
            return 201, json.dumps({"Id": "cid"}).encode()
        if path.endswith("/start"):
            return 204, b""
        if path.endswith("/wait"):
            return 200, b"{}"
        if "/logs" in path:
            return 200, b"no sentinel here"  # extract_result will raise → handled
        if method == "DELETE":
            deleted.append(path)
            return 200, b""
        return 404, b""

    res = es.run_evolver_job({"target_file": "app/x.py"}, transport=fake_tx)
    assert res["ok"] is False
    assert deleted, "container must be cleaned up even when result parsing fails"


def test_image_exists(monkeypatch):
    assert es.image_exists("img:1", transport=lambda *a, **k: (200, b"{}")) is True
    assert es.image_exists("img:1", transport=lambda *a, **k: (404, b"")) is False


def test_http_transport_end_to_end_against_loopback_proxy(monkeypatch):
    """Exercise the REAL ``_http_transport`` (urllib) against a faithful
    in-process docker-proxy stub.

    Every other test injects a fake ``transport=``, so the actual wire
    construction (URL from ``DOCKER_HOST``, JSON body, method/headers, response
    parsing), the full create→start→wait→logs→delete sequence, AND end-to-end
    env-key forwarding are otherwise unpinned. This closes that gap and is the
    regression fence for the 2026-05-29 §76 bug (required Settings env keys
    silently absent from the container) at the wire, not just the payload.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured: dict = {}

    class _Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # keep the test run quiet
            pass

        def _send(self, code: int, body: bytes = b""):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            if self.path == "/containers/create":
                captured["create_body"] = json.loads(raw or b"{}")
                self._send(201, json.dumps({"Id": "cid"}).encode())
            elif self.path.endswith("/start"):
                self._send(204)
            elif self.path.endswith("/wait"):
                self._send(200, b"{}")
            else:
                self._send(404)

        def do_GET(self):
            if "/logs" in self.path:
                payload = {
                    "ok": True,
                    "result": {"ok": True, "returncode": 0, "stdout": "m=1", "stderr": "", "timed_out": False},
                }
                logs = _RESULT_BEGIN + json.dumps(payload) + _RESULT_END + "\n"
                self._send(200, logs.encode())
            else:
                self._send(404)

        def do_DELETE(self):
            self._send(200, b"{}")

    srv = HTTPServer(("127.0.0.1", 0), _Proxy)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")
        monkeypatch.setenv("GATEWAY_SECRET", "gw-xyz")  # a REQUIRED settings key
        # No transport= → the real urllib _http_transport runs against the stub.
        res = es.run_container_job({"x": 1}, image="img:1", timeout_s=5)
    finally:
        srv.shutdown()
        thread.join(timeout=2)

    # Sentinel round-tripped through the real GET /logs read + extract_result.
    assert res == {
        "ok": True,
        "result": {"ok": True, "returncode": 0, "stdout": "m=1", "stderr": "", "timed_out": False},
    }
    # §76 regression fence: the required Settings key reached the create wire.
    assert "GATEWAY_SECRET=gw-xyz" in captured["create_body"]["Env"]
