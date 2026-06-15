"""Forwarder receive-stall watchdog (2026-06-15).

signal-cli's daemon can stay up while its receive path hangs on a stale
server connection (weekend of Mac sleeps). The forwarder used to treat a
hung receive (ReadTimeout / "already being received") as an empty poll and
never recover. These tests pin the fix: classify a stall distinctly, and
on a sustained stall auto-`launchctl kickstart` signal-cli, rate-limited.

The forwarder is a host script under signal/ — loaded by PATH here so the
repo's signal/ package never shadows the stdlib `signal` module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest

requests = pytest.importorskip("requests")  # forwarder imports it at module load

_FWD_PATH = Path(__file__).resolve().parents[1] / "signal" / "forwarder.py"


def _load_forwarder():
    spec = importlib.util.spec_from_file_location("_fwd_under_test", _FWD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fwd():
    return _load_forwarder()


# ── _receive_messages classification ──────────────────────────────────────


def _fake_resp(payload):
    m = mock.Mock()
    m.json.return_value = payload
    m.status_code = 200
    return m


def test_clean_empty_is_empty_list_not_stall(fwd):
    with mock.patch.object(fwd._signal_session, "post", return_value=_fake_resp({"result": []})):
        assert fwd._receive_messages() == []


def test_messages_pass_through(fwd):
    msgs = [{"envelope": {"dataMessage": {"message": "hi"}}}]
    with mock.patch.object(fwd._signal_session, "post", return_value=_fake_resp({"result": msgs})):
        assert fwd._receive_messages() == msgs


def test_read_timeout_is_stall_not_empty(fwd):
    """The core bug: a hung receive (ReadTimeout) must NOT look like empty."""
    with mock.patch.object(fwd._signal_session, "post",
                           side_effect=requests.exceptions.ReadTimeout()):
        assert fwd._receive_messages() is fwd._STALL


def test_already_being_received_is_stall(fwd):
    payload = {"error": {"message": "Receive command cannot be used if messages are already being received."}}
    with mock.patch.object(fwd._signal_session, "post", return_value=_fake_resp(payload)):
        assert fwd._receive_messages() is fwd._STALL


def test_connection_error_is_none(fwd):
    with mock.patch.object(fwd._signal_session, "post",
                           side_effect=requests.exceptions.ConnectionError()):
        assert fwd._receive_messages() is None


def test_other_rpc_error_is_empty_not_stall(fwd):
    """A non-hang JSON-RPC error shouldn't trip the kick path."""
    payload = {"error": {"message": "some other error"}}
    with mock.patch.object(fwd._signal_session, "post", return_value=_fake_resp(payload)):
        assert fwd._receive_messages() == []


# ── _recv_stall_action decision (pure) ────────────────────────────────────


def test_action_start_on_first_stall(fwd):
    action, since = fwd._recv_stall_action(None, 0.0, 1000.0)
    assert action == "start" and since == 1000.0


def test_action_wait_under_threshold(fwd):
    start = 1000.0
    now = start + fwd._RECV_STALL_RESTART_AFTER_S - 1
    action, since = fwd._recv_stall_action(start, 0.0, now)
    assert action == "wait" and since == start


def test_action_kick_when_streak_exceeds_threshold_and_cooldown_elapsed(fwd):
    start = 1000.0
    now = start + fwd._RECV_STALL_RESTART_AFTER_S + 1
    # last kick long ago → cooldown elapsed
    action, _ = fwd._recv_stall_action(start, now - fwd._RECV_STALL_KICK_COOLDOWN_S - 1, now)
    assert action == "kick"


def test_action_cooldown_blocks_repeat_kick(fwd):
    start = 1000.0
    now = start + fwd._RECV_STALL_RESTART_AFTER_S + 1
    # kicked recently → still cooling down
    action, _ = fwd._recv_stall_action(start, now - 1.0, now)
    assert action == "cooldown"


# ── _kick_signal_cli ──────────────────────────────────────────────────────


def test_kick_invokes_launchctl_kickstart(fwd):
    with mock.patch.object(fwd.subprocess, "run") as run, \
         mock.patch.object(fwd._gateway_session, "post"):
        run.return_value = mock.Mock(returncode=0, stderr="")
        ok = fwd._kick_signal_cli()
    assert ok is True
    argv = run.call_args[0][0]
    assert argv[:3] == ["launchctl", "kickstart", "-k"]
    assert argv[3].endswith("/" + fwd._SIGNAL_CLI_LAUNCHD_LABEL)
    assert argv[3].startswith("gui/")


def test_kick_returns_false_on_nonzero_rc(fwd):
    with mock.patch.object(fwd.subprocess, "run",
                           return_value=mock.Mock(returncode=1, stderr="no such service")):
        assert fwd._kick_signal_cli() is False


def test_kick_failure_isolated(fwd):
    with mock.patch.object(fwd.subprocess, "run", side_effect=FileNotFoundError("launchctl")):
        assert fwd._kick_signal_cli() is False  # never raises
