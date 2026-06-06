"""Tests for the docker-create broker (review finding S2).

All host-runnable — no Docker. The policy core is pure; the forwarding server
is exercised against an in-process fake upstream, so we verify end-to-end that a
malicious create is BLOCKED (never reaches upstream) and a clean one is
forwarded.
"""
import json
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.docker_broker import server as broker_server
from app.docker_broker.policy import validate_create_body


def _evolver_body() -> dict:
    """The exact locked-down shape evolver_spawn.build_create_payload produces."""
    return {
        "Image": "botarmy-evolver:latest",
        "Env": ["AAI_EVOLVE_JOB={}"],
        "Tty": True,
        "HostConfig": {
            "AutoRemove": False,
            "NetworkMode": "none",
            "Memory": 4 * 1024**3,
            "PidsLimit": 512,
            "SecurityOpt": ["no-new-privileges:true"],
            "OomScoreAdj": 900,
        },
    }


# ── policy core (pure) ───────────────────────────────────────────────────────

def test_policy_allows_real_evolver_payload():
    ok, reason = validate_create_body(_evolver_body())
    assert ok, reason


def test_policy_allows_ollama_fleet_image():
    ok, _ = validate_create_body({"Image": "ollama/ollama:0.5.4", "HostConfig": {}})
    assert ok


@pytest.mark.parametrize("hostconfig", [
    {"Binds": ["/:/host"]},
    {"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]},
    {"Privileged": True},
    {"PidMode": "host"},
    {"PidMode": "container:abc"},
    {"NetworkMode": "host"},
    {"NetworkMode": "container:abc"},
    {"UsernsMode": "host"},
    {"CgroupnsMode": "host"},
    {"UTSMode": "host"},
    {"IpcMode": "host"},
    {"CapAdd": ["SYS_ADMIN"]},
    {"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}]},
    {"DeviceRequests": [{"Driver": "nvidia", "Count": -1}]},
    {"Sysctls": {"kernel.shm_rmid_forced": "1"}},
    {"SecurityOpt": ["seccomp=unconfined"]},
    {"SecurityOpt": ["apparmor:unconfined"]},
])
def test_policy_rejects_host_escapes(hostconfig):
    body = _evolver_body()
    body["HostConfig"].update(hostconfig)
    ok, reason = validate_create_body(body)
    assert not ok, f"escape not blocked: {hostconfig}"


def test_policy_rejects_unlisted_image():
    ok, _ = validate_create_body({"Image": "alpine:latest", "HostConfig": {}})
    assert not ok


def test_policy_image_prefix_boundary_is_not_loose():
    # a look-alike repo must NOT match the allowlist prefix
    ok, _ = validate_create_body({"Image": "botarmy-evolver-evil/x:latest", "HostConfig": {}})
    assert not ok


def test_policy_rejects_missing_image():
    ok, _ = validate_create_body({"HostConfig": {"Privileged": True}})
    assert not ok


# ── forwarding server (fake upstream, no Docker) ─────────────────────────────

class _FakeUpstream(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, bytes]] = []

    def log_message(self, *a):  # silence
        pass

    def _do(self):
        cl = self.headers.get("Content-Length")
        body = self.rfile.read(int(cl)) if cl else b""
        _FakeUpstream.seen.append((self.command, self.path, body))
        payload = json.dumps({"upstream": "ok", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = _do


@pytest.fixture
def broker(monkeypatch):
    _FakeUpstream.seen.clear()
    up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("DOCKER_BROKER_UPSTREAM", f"http://127.0.0.1:{up.server_address[1]}")
    br = ThreadingHTTPServer(("127.0.0.1", 0), broker_server._Handler)
    threading.Thread(target=br.serve_forever, daemon=True).start()
    try:
        yield br.server_address[1]
    finally:
        br.shutdown(); br.server_close()
        up.shutdown(); up.server_close()


def _req(port: int, method: str, path: str, body=None):
    c = HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
    r = c.getresponse(); out = r.read(); c.close()
    return r.status, out


def _create_reached_upstream() -> bool:
    return any(p.split("?")[0].endswith("/containers/create") for _, p, _ in _FakeUpstream.seen)


def test_broker_blocks_malicious_create_before_upstream(broker):
    status, _ = _req(broker, "POST", "/containers/create",
                     {"Image": "botarmy-evolver", "HostConfig": {"Binds": ["/:/host"]}})
    assert status == 403
    assert not _create_reached_upstream(), "malicious create reached the upstream proxy!"


def test_broker_forwards_clean_create(broker):
    status, _ = _req(broker, "POST", "/containers/create", _evolver_body())
    assert status == 200
    assert _create_reached_upstream()


def test_broker_blocks_versioned_create_path(broker):
    status, _ = _req(broker, "POST", "/v1.43/containers/create",
                     {"Image": "botarmy-evolver", "HostConfig": {"Privileged": True}})
    assert status == 403
    assert not _create_reached_upstream()


def test_broker_forwards_non_create_requests(broker):
    # image inspect (GET) and container start (POST, but NOT create) pass through
    s1, _ = _req(broker, "GET", "/images/botarmy-evolver:latest/json")
    s2, _ = _req(broker, "POST", "/containers/abc123/start")
    assert s1 == 200 and s2 == 200
    paths = [p for _, p, _ in _FakeUpstream.seen]
    assert any(p.endswith("/json") for p in paths)
    assert any(p.endswith("/start") for p in paths)
