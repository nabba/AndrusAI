"""Docker-daemon guard (2026-06-21) — the watchdog must recognise a DOWN
Docker daemon and relaunch Docker Desktop instead of mislabelling it an
event-loop wedge and flooding doomed `docker compose restart` alerts.

The incident: an overnight host reboot left Docker Desktop NOT auto-started,
so the whole container stack stayed down ~15h. `container_running()` returned
None on the failed inspect (not False), so the loop fell through to the
HEALTH_ESCALATE branch every ~17 min — "event loop wedged" + a restart that
can't work with the daemon down + "restart FAILED — manual intervention
needed". The fix adds `docker_daemon_reachable()` to distinguish a down daemon,
and `maybe_relaunch_docker()` to (re)start Docker Desktop (throttled), letting
the stack's restart:unless-stopped bring the gateway back on its own.

Same load-via-importlib + requests-stub harness as
``tests/test_gateway_watchdog_cooldown.py`` (the watchdog is a host script
under ``scripts/`` and isn't normally on the import path).
"""
from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path


def _load_watchdog(monkeypatch, tmp_path):
    """Load scripts/gateway_watchdog.py with a stubbed requests + a
    tmp_path-rooted STATE_PATH so tests are hermetic. Env set BEFORE this
    call is read fresh — the module is re-exec'd on every load."""
    if "requests" not in sys.modules:
        fake = types.ModuleType("requests")

        class _Session:
            def __init__(self):
                self.headers: dict = {}

            def get(self, *a, **kw):
                raise RuntimeError("network unavailable in test")

            def post(self, *a, **kw):
                raise RuntimeError("network unavailable in test")

        class _RequestException(Exception):
            pass

        exceptions = types.ModuleType("requests.exceptions")
        exceptions.RequestException = _RequestException
        fake.Session = _Session
        fake.exceptions = exceptions
        sys.modules["requests"] = fake
        sys.modules["requests.exceptions"] = exceptions

    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    path = Path(__file__).parent.parent / "scripts" / "gateway_watchdog.py"
    spec = importlib.util.spec_from_file_location("_test_watchdog_docker", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_watchdog_docker"] = mod
    spec.loader.exec_module(mod)
    return mod


class _CP:
    """Minimal CompletedProcess stand-in."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── docker_daemon_reachable ────────────────────────────────────────────


def test_daemon_reachable_true_on_server_version(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _CP(returncode=0, stdout="29.5.3\n"))
    assert mod.docker_daemon_reachable() is True


def test_daemon_reachable_false_on_connect_error(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    err = ("Cannot connect to the Docker daemon at "
           "unix:///Users/x/.docker/run/docker.sock. Is the docker daemon running?")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _CP(returncode=1, stdout="", stderr=err))
    assert mod.docker_daemon_reachable() is False


def test_daemon_reachable_false_matches_daemon_running_phrase(monkeypatch, tmp_path):
    """Defensive: match either canonical phrase, not one exact string."""
    mod = _load_watchdog(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _CP(returncode=1, stderr="error: Is the docker daemon running?"),
    )
    assert mod.docker_daemon_reachable() is False


def test_daemon_reachable_none_on_other_error(monkeypatch, tmp_path):
    """A non-connect failure (e.g. a permission error) is 'unknown', NOT a
    confident 'daemon down' — must not trigger a relaunch on its own."""
    mod = _load_watchdog(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _CP(returncode=1, stderr="permission denied while trying to connect"),
    )
    assert mod.docker_daemon_reachable() is None


def test_daemon_reachable_none_when_binary_missing(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)

    def _missing(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(mod.subprocess, "run", _missing)
    assert mod.docker_daemon_reachable() is None


def test_daemon_reachable_never_raises(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert mod.docker_daemon_reachable() is None


# ── relaunch_docker_desktop ────────────────────────────────────────────


def test_relaunch_issues_configured_command(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    captured = {}

    def _run(argv, *a, **k):
        captured["argv"] = argv
        return _CP(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", _run)
    assert mod.relaunch_docker_desktop() is True
    # Default macOS launch command, parsed argv-not-shell.
    assert captured["argv"] == ["open", "-ga", "Docker"]


def test_relaunch_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_DESKTOP_OPEN_CMD", "systemctl --user start docker-desktop")
    mod = _load_watchdog(monkeypatch, tmp_path)
    captured = {}

    def _run(argv, *a, **k):
        captured["argv"] = argv
        return _CP(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", _run)
    assert mod.relaunch_docker_desktop() is True
    assert captured["argv"] == ["systemctl", "--user", "start", "docker-desktop"]


def test_relaunch_false_on_nonzero(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _CP(returncode=1, stderr="no such app"))
    assert mod.relaunch_docker_desktop() is False


def test_relaunch_false_when_command_missing(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)

    def _missing(*a, **k):
        raise FileNotFoundError("open")

    monkeypatch.setattr(mod.subprocess, "run", _missing)
    assert mod.relaunch_docker_desktop() is False


def test_relaunch_never_raises(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert mod.relaunch_docker_desktop() is False


# ── maybe_relaunch_docker (throttle + gating) ──────────────────────────


def test_maybe_relaunch_skips_inside_cooldown(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mod, "relaunch_docker_desktop", lambda: calls.append(1) or True)
    # last action just now → inside the relaunch cooldown → no-op.
    last = time.time()
    out = mod.maybe_relaunch_docker(last)
    assert out == last
    assert calls == [], "must not relaunch again inside the cooldown window"


def test_maybe_relaunch_acts_past_cooldown(monkeypatch, tmp_path):
    mod = _load_watchdog(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mod, "relaunch_docker_desktop", lambda: calls.append(1) or True)
    last = time.time() - (mod.DOCKER_RELAUNCH_COOLDOWN + 30)
    out = mod.maybe_relaunch_docker(last)
    assert calls == [1], "should relaunch once the cooldown has elapsed"
    assert out == pytest_approx_now()


def test_maybe_relaunch_disabled_does_not_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_AUTOSTART_ENABLED", "0")
    mod = _load_watchdog(monkeypatch, tmp_path)
    assert mod.DOCKER_AUTOSTART_ENABLED is False
    calls = []
    monkeypatch.setattr(mod, "relaunch_docker_desktop", lambda: calls.append(1) or True)
    last = time.time() - (mod.DOCKER_RELAUNCH_COOLDOWN + 30)
    out = mod.maybe_relaunch_docker(last)
    # Past cooldown but disabled → does NOT launch, but DOES advance the
    # timestamp so the (alert-only) path is itself throttled.
    assert calls == []
    assert out != last


# ── config defaults + overrides ────────────────────────────────────────


def test_autostart_default_on(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_AUTOSTART_ENABLED", raising=False)
    mod = _load_watchdog(monkeypatch, tmp_path)
    assert mod.DOCKER_AUTOSTART_ENABLED is True


def test_open_cmd_default_is_macos(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_DESKTOP_OPEN_CMD", raising=False)
    mod = _load_watchdog(monkeypatch, tmp_path)
    assert mod.DOCKER_DESKTOP_OPEN_CMD == "open -ga Docker"


def test_relaunch_cooldown_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_RELAUNCH_COOLDOWN_SECONDS", raising=False)
    mod = _load_watchdog(monkeypatch, tmp_path)
    assert mod.DOCKER_RELAUNCH_COOLDOWN == 300.0
    monkeypatch.setenv("DOCKER_RELAUNCH_COOLDOWN_SECONDS", "60")
    mod2 = _load_watchdog(monkeypatch, tmp_path)
    assert mod2.DOCKER_RELAUNCH_COOLDOWN == 60.0


# Tiny local approx helper so we don't depend on pytest.approx import style.
def pytest_approx_now():
    import pytest
    return pytest.approx(time.time(), abs=2.0)
