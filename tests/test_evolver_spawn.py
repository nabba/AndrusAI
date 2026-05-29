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
