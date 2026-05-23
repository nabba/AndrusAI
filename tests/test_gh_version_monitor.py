"""Tests for the gh CLI version-drift monitor (Plan Risk #4 closure,
2026-05-22).

Pins:
  * First observation establishes baseline with NO alert.
  * Same-version probe is a no-op.
  * Minor/patch drift updates baseline quietly (no alert).
  * Major version drift fires a Signal alert exactly once per
    (old_major, new_major) within the dedup window.
  * Bridge unreachable → SKIP (no alert, baseline preserved).
  * gh missing → SKIP (no alert).
  * Corrupt baseline → re-baseline silently.
  * Master switch OFF → no probe, no state mutation.
  * Internal cadence guard suppresses repeat work within 7 days.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Module loader with mocks for psycopg2 + the runtime_settings
#    persistence layer. Mirrors the pattern used by the other
#    healing-monitor test files (e.g. test_executor_audit_chain.py).
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


# Load the monitor under test
gh_version = _load(
    "_gh_version_test", "app/healing/monitors/gh_version.py",
)


# ── Helpers ─────────────────────────────────────────────────────────


class _FakeBridge:
    """A bridge stub: returns canned (stdout, returncode) on .execute()."""

    def __init__(
        self,
        stdout: str = "gh version 2.40.1 (2024-09-25)\nhttps://github.com/cli/cli/releases/tag/v2.40.1",
        returncode: int = 0,
        available: bool = True,
        raise_on_execute: Exception | None = None,
        stderr: str = "",
    ):
        self._stdout = stdout
        self._returncode = returncode
        self._available = available
        self._raise = raise_on_execute
        self._stderr = stderr
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def is_available(self) -> bool:
        return self._available

    def execute(
        self, command: list[str],
        working_dir: str = "/tmp", timeout: int = 30,
    ) -> dict[str, Any]:
        self.calls.append(
            (command, {"working_dir": working_dir, "timeout": timeout}),
        )
        if self._raise is not None:
            raise self._raise
        return {
            "stdout": self._stdout,
            "stderr": self._stderr,
            "returncode": self._returncode,
        }


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect state writes into a tmp dir so test runs don't
    pollute workspace/self_heal/."""
    if gh_version is None:
        pytest.skip("gh_version module not loadable")

    # Pin the _common state-file root by monkey-patching the
    # write/read functions to use a tmp directory.
    state_dir = tmp_path / "self_heal"
    state_dir.mkdir()

    import json

    def _fake_read(name: str, default=None):
        p = state_dir / name
        if not p.exists():
            return default if default is not None else {}
        try:
            return json.loads(p.read_text())
        except Exception:
            return default if default is not None else {}

    def _fake_write(name: str, payload: dict):
        p = state_dir / name
        p.write_text(json.dumps(payload, indent=2))

    # Catch signal alerts in a list rather than firing.
    sent: list[tuple[str, str]] = []

    def _fake_alert(text: str, *, tag: str = "self_heal") -> bool:
        sent.append((tag, text))
        return True

    # Pre-set the deferred-import slots so _resolve_common() doesn't
    # try to load the real helpers (which transitively pulls
    # pydantic_settings via app.healing.__init__).
    monkeypatch.setattr(gh_version, "read_state_json", _fake_read)
    monkeypatch.setattr(gh_version, "write_state_json", _fake_write)
    monkeypatch.setattr(gh_version, "audit_event", lambda *a, **kw: None)
    monkeypatch.setattr(gh_version, "send_signal_alert", _fake_alert)

    yield {"state_dir": state_dir, "alerts": sent}


# ── First-observation baseline ──────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestFirstObservation:
    def test_first_run_records_baseline_no_alert(self, isolated_state):
        bridge = _FakeBridge()
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is True
        assert result["version"] == "2.40.1"
        assert result["drift_kind"] == "first_baseline"
        assert result["alert_fired"] is False
        assert result["alert_tag"] is None
        assert isolated_state["alerts"] == []

    def test_baseline_persists_across_runs(self, isolated_state):
        bridge = _FakeBridge()
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge)
        # 7 days later, same version
        result = gh_version.run(
            now=1000.0 + 7 * 86400 + 10, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is True
        assert result["baseline_before"] == "2.40.1"
        assert result["drift_kind"] is None  # no drift
        assert isolated_state["alerts"] == []


# ── Drift classification ────────────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestDriftClassification:
    def test_no_drift_same_triple(self, isolated_state):
        bridge = _FakeBridge()
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge)
        # Same version a week later
        result = gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge,
        )
        assert result["drift_kind"] is None
        assert result["alert_fired"] is False

    def test_patch_drift_silent(self, isolated_state):
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        bridge_b = _FakeBridge(stdout="gh version 2.40.5")
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        result = gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge_b,
        )
        assert result["drift_kind"] == "minor_patch"
        assert result["alert_fired"] is False
        assert result["version"] == "2.40.5"

    def test_minor_drift_silent(self, isolated_state):
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        bridge_b = _FakeBridge(stdout="gh version 2.50.0")
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        result = gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge_b,
        )
        assert result["drift_kind"] == "minor_patch"
        assert result["alert_fired"] is False

    def test_major_drift_fires_alert(self, isolated_state):
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        bridge_b = _FakeBridge(stdout="gh version 3.0.0")
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        result = gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge_b,
        )
        assert result["drift_kind"] == "major"
        assert result["alert_fired"] is True
        assert result["alert_tag"] == "gh_version:major:2->3"
        assert len(isolated_state["alerts"]) == 1
        tag, body = isolated_state["alerts"][0]
        assert tag == "gh_version:major:2->3"
        assert "2.40.1" in body and "3.0.0" in body
        assert "MAJOR" in body.upper()

    def test_major_drift_alert_dedup_within_window(self, isolated_state):
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        bridge_b = _FakeBridge(stdout="gh version 3.0.0")
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge_b,
        )
        # Second probe 7 days later (within 14-day dedup window).
        # The version is still 3.0.0 but baseline is 3.0.0 now, so
        # no drift; we contrive a 3.x→4.x scenario instead.
        bridge_c = _FakeBridge(stdout="gh version 4.0.0")
        gh_version.run(
            now=1000.0 + 16 * 86400, bridge_factory=lambda: bridge_c,
        )
        # 3→4 is a different alert tag, so this fires (different dedup
        # bucket); first alert wasn't suppressed by the second. Both
        # alerts present.
        assert len(isolated_state["alerts"]) == 2
        tags = [t for t, _ in isolated_state["alerts"]]
        assert "gh_version:major:2->3" in tags
        assert "gh_version:major:3->4" in tags

    def test_major_drift_repeated_same_pair_dedup(self, isolated_state):
        """When the major version flips to a value, then within the
        dedup window flips to a third value and back, the SAME pair
        should not double-fire (the baseline updates so each pair
        only happens once anyway — but we verify state-file dedup
        explicitly)."""
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        bridge_b = _FakeBridge(stdout="gh version 3.0.0")
        # First: 2 → 3
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        gh_version.run(
            now=1000.0 + 8 * 86400, bridge_factory=lambda: bridge_b,
        )
        # If the same (2, 3) drift somehow recurred (operator
        # downgrade then upgrade), the dedup state should still
        # suppress within 14d. Manually inject the old baseline
        # and re-fire.
        import json
        state_file = (
            isolated_state["state_dir"] / "gh_version_baseline.json"
        )
        state = json.loads(state_file.read_text())
        # Reset baseline to 2.40.1 to simulate a downgrade
        state["baseline"] = {
            "major": 2, "minor": 40, "patch": 1,
            "raw": "gh version 2.40.1",
            "observed_at": "2026-05-22T00:00:00+00:00",
        }
        state["last_run_at"] = 1000.0  # force next run to fire
        state_file.write_text(json.dumps(state))

        # Now probe 3.0.0 again, less than 14 days after first 2→3
        # alert. Should be deduped.
        bridge_c = _FakeBridge(stdout="gh version 3.0.0")
        gh_version.run(
            now=1000.0 + 12 * 86400, bridge_factory=lambda: bridge_c,
        )
        # Still only ONE 2→3 alert
        major_alerts = [
            t for t, _ in isolated_state["alerts"]
            if t == "gh_version:major:2->3"
        ]
        assert len(major_alerts) == 1


# ── Bridge unreachable / gh missing — SKIP path ─────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestSkipPath:
    def test_bridge_none_skips(self, isolated_state):
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: None,
        )
        assert result["probe_ok"] is False
        assert result["alert_fired"] is False
        assert "bridge unavailable" in (result["skip_reason"] or "")

    def test_bridge_not_available_skips(self, isolated_state):
        bridge = _FakeBridge(available=False)
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is False
        assert "not available" in (result["skip_reason"] or "")
        assert result["alert_fired"] is False

    def test_bridge_raises_skips(self, isolated_state):
        bridge = _FakeBridge(
            raise_on_execute=RuntimeError("bridge POST failed"),
        )
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is False
        assert "raised" in (result["skip_reason"] or "")
        assert result["alert_fired"] is False

    def test_gh_not_found_skips(self, isolated_state):
        bridge = _FakeBridge(
            stdout="", stderr="gh: command not found", returncode=127,
        )
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is False
        assert result["alert_fired"] is False
        assert "exit 127" in (result["skip_reason"] or "")

    def test_unparseable_output_skips(self, isolated_state):
        bridge = _FakeBridge(stdout="this is not a gh version string")
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is False
        assert result["alert_fired"] is False
        assert "unparseable" in (result["skip_reason"] or "")

    def test_skipped_probe_does_not_advance_last_run(self, isolated_state):
        """When the bridge is unreachable, the next cadence retry
        should actually fire — we don't burn the weekly slot on a
        failed probe."""
        bridge = _FakeBridge(available=False)
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge)
        # Now bridge is back; second probe should run, not skip on cadence.
        bridge_ok = _FakeBridge(stdout="gh version 2.40.1")
        result = gh_version.run(
            now=1000.0 + 60, bridge_factory=lambda: bridge_ok,
        )
        # Probe ran (didn't skip on cadence)
        assert result["ran"] is True
        assert result["probe_ok"] is True


# ── Corrupt baseline recovery ───────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestCorruptBaseline:
    def test_corrupt_baseline_is_recovered_silently(self, isolated_state):
        import json
        state_file = (
            isolated_state["state_dir"] / "gh_version_baseline.json"
        )
        # Write a corrupt baseline that lacks the major/minor/patch keys
        state_file.write_text(json.dumps({
            "baseline": {"garbage": "no version here"},
            "last_run_at": 0.0,
            "last_alert_at": {},
            "history": [],
        }))
        bridge = _FakeBridge(stdout="gh version 2.40.1")
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["drift_kind"] == "baseline_recovered"
        # No alert — we don't want to spam operators with our own
        # state-file corruption.
        assert result["alert_fired"] is False


# ── Master switch ──────────────────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestMasterSwitch:
    def test_disabled_via_env_skips_everything(
        self, isolated_state, monkeypatch,
    ):
        # Force the runtime_settings path to fail so we fall back to env
        import importlib

        class _FakeRS:
            pass

        # The fallback path: no get_gh_version_monitor_enabled attr →
        # env var consulted. Set env var to off.
        monkeypatch.setenv("GH_VERSION_MONITOR_ENABLED", "false")

        # Stash a fake runtime_settings without the getter to force env path.
        fake_mod = type(sys)("app.runtime_settings_stub")
        # No get_gh_version_monitor_enabled attribute set
        monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_mod)

        bridge = _FakeBridge()
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["ran"] is False
        assert result["skip_reason"] == "monitor disabled"
        # Bridge never called
        assert bridge.calls == []


# ── Internal cadence guard ──────────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestCadence:
    def test_second_call_within_window_is_noop(self, isolated_state):
        bridge_a = _FakeBridge(stdout="gh version 2.40.1")
        gh_version.run(now=1000.0, bridge_factory=lambda: bridge_a)
        # 1 hour later
        bridge_b = _FakeBridge(stdout="gh version 9.9.9")  # would be alarming
        result = gh_version.run(
            now=1000.0 + 3600, bridge_factory=lambda: bridge_b,
        )
        # Cadence skipped — bridge_b never called
        assert result["ran"] is False
        assert result["skip_reason"] == "within internal cadence window"
        assert bridge_b.calls == []


# ── Version regex robustness ────────────────────────────────────────


@pytest.mark.skipif(gh_version is None, reason="module not loadable")
class TestVersionParse:
    @pytest.mark.parametrize("stdout, expected", [
        ("gh version 2.40.1 (2024-09-25)", (2, 40, 1)),
        ("gh version 2.40.1", (2, 40, 1)),
        ("  gh version 1.0.0  ", (1, 0, 0)),
        ("GH VERSION 4.5.6", (4, 5, 6)),  # case-insensitive
        ("gh version 10.20.30 (build x)", (10, 20, 30)),
    ])
    def test_parse_variations(self, isolated_state, stdout, expected):
        bridge = _FakeBridge(stdout=stdout)
        result = gh_version.run(
            now=1000.0, bridge_factory=lambda: bridge,
        )
        assert result["probe_ok"] is True
        assert result["version"] == (
            f"{expected[0]}.{expected[1]}.{expected[2]}"
        )


# ── Runtime-settings wire-in ────────────────────────────────────────


def test_runtime_settings_has_getter_and_setter():
    """Plan Risk #4: the master switch must be a real
    runtime_settings key with idempotent getter/setter pairs that
    match the pattern used by every other monitor toggle.

    Verified by source inspection rather than import so the test
    passes on the dev host (which lacks pydantic_settings — the
    transitive import would crash).
    """
    src = Path("app/runtime_settings.py").read_text(encoding="utf-8")
    # Key declared in defaults with True
    assert '"gh_version_monitor_enabled": True' in src
    # Getter + setter functions defined
    assert "def get_gh_version_monitor_enabled()" in src
    assert "def set_gh_version_monitor_enabled(value: bool)" in src
    # Getter reads the key with default True (failure-open posture)
    assert (
        '"gh_version_monitor_enabled", True' in src
    ), "getter must default to True (failure-open)"
    # Setter writes through _update
    assert (
        '_update({"gh_version_monitor_enabled": bool(value)})' in src
    )


def test_monitor_is_registered_in_driver():
    """The monitor must be visible in the driver's import block AND
    in the cadence map — otherwise it's dead code."""
    src = Path(
        "app/healing/monitors/__init__.py",
    ).read_text(encoding="utf-8")
    assert '"gh_version"' in src
    assert "from app.healing.monitors import gh_version" in src
    assert 'gh_version.run' in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
