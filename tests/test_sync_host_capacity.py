"""Tests for ``scripts/sync_host_capacity.py``.

Covers the two surfaces that matter:

  1. Probe — auto-detection works on macOS (sysctl) and Linux
     (/proc/meminfo); returns ``None`` cleanly when both fail.
  2. .env edit — idempotent, preserves untouched lines, replaces in
     place when a key already exists, appends under a managed-block
     header when a key is new, never wipes user data on probe failure.

The script is meant to run on the HOST (not inside Docker) BEFORE the
Python venv exists, so the test imports it via path injection rather
than a normal package import.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Module loader ────────────────────────────────────────────────────────
# The script lives outside the package; loadable via importlib.

@pytest.fixture(scope="module")
def sync_module():
    script = Path(__file__).resolve().parent.parent / "scripts" / "sync_host_capacity.py"
    spec = importlib.util.spec_from_file_location("sync_host_capacity", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_host_capacity"] = mod
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════
# Probe
# ══════════════════════════════════════════════════════════════════════

class TestDetectTotalRamGb:

    def test_macos_via_sysctl(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Darwin")
        # 48 GB in bytes
        fake_bytes = 48 * 1024 ** 3
        monkeypatch.setattr(
            sync_module.subprocess, "check_output",
            lambda *a, **kw: f"{fake_bytes}\n",
        )
        assert sync_module.detect_total_ram_gb() == 48

    def test_macos_64gb(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            sync_module.subprocess, "check_output",
            lambda *a, **kw: f"{64 * 1024 ** 3}\n",
        )
        assert sync_module.detect_total_ram_gb() == 64

    def test_macos_sysctl_failure_returns_none(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Darwin")

        def _raise(*_a, **_kw):
            raise OSError("sysctl gone fishing")

        monkeypatch.setattr(sync_module.subprocess, "check_output", _raise)
        assert sync_module.detect_total_ram_gb() is None

    def test_linux_via_proc_meminfo(self, sync_module, monkeypatch, tmp_path):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Linux")
        # /proc/meminfo reports MemTotal in kB; 32 GB = 33554432 kB
        meminfo = (
            "MemTotal:       33554432 kB\n"
            "MemFree:        12345678 kB\n"
            "Cached:         99999999 kB\n"
        )
        # Patch the open builtin specifically for "/proc/meminfo"
        original_open = open

        def fake_open(path, *a, **kw):
            if str(path) == "/proc/meminfo":
                from io import StringIO
                return StringIO(meminfo)
            return original_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", fake_open)
        assert sync_module.detect_total_ram_gb() == 32

    def test_unsupported_platform_returns_none(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Windows")
        assert sync_module.detect_total_ram_gb() is None


class TestDetectOsBaselineGb:

    def test_macos_returns_10(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Darwin")
        assert sync_module.detect_os_baseline_gb() == 10

    def test_linux_returns_4(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "Linux")
        assert sync_module.detect_os_baseline_gb() == 4

    def test_unknown_returns_8(self, sync_module, monkeypatch):
        monkeypatch.setattr(sync_module.platform, "system", lambda: "FreeBSD")
        assert sync_module.detect_os_baseline_gb() == 8


# ══════════════════════════════════════════════════════════════════════
# .env editing
# ══════════════════════════════════════════════════════════════════════

class TestParseEnv:

    def test_basic_key_value(self, sync_module):
        env = "FOO=bar\nBAZ=42\n"
        d = sync_module.parse_env(env)
        assert d == {"FOO": "bar", "BAZ": "42"}

    def test_strips_inline_comment(self, sync_module):
        env = "HOST_TOTAL_RAM_GB=48   # the comment\n"
        d = sync_module.parse_env(env)
        assert d["HOST_TOTAL_RAM_GB"] == "48"

    def test_strips_quotes(self, sync_module):
        env = "FOO='bar baz'\nQUX=\"spam\"\n"
        d = sync_module.parse_env(env)
        assert d["FOO"] == "bar baz"
        assert d["QUX"] == "spam"

    def test_skips_blank_and_comment_lines(self, sync_module):
        env = "\n# header comment\nFOO=bar\n  \n# another\n"
        d = sync_module.parse_env(env)
        assert d == {"FOO": "bar"}


class TestUpdateEnvText:

    def test_replaces_existing_key_in_place(self, sync_module):
        original = (
            "ANTHROPIC_API_KEY=sk-foo\n"
            "HOST_TOTAL_RAM_GB=16\n"
            "BRAVE_API_KEY=brave-foo\n"
        )
        new_text, changes = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "48"},
        )
        assert "HOST_TOTAL_RAM_GB=48" in new_text
        assert "HOST_TOTAL_RAM_GB=16" not in new_text
        # Other keys preserved exactly
        assert "ANTHROPIC_API_KEY=sk-foo" in new_text
        assert "BRAVE_API_KEY=brave-foo" in new_text
        assert any("HOST_TOTAL_RAM_GB" in c for c in changes)

    def test_preserves_inline_comment(self, sync_module):
        """The user's documentation comments should survive value updates."""
        original = "HOST_TOTAL_RAM_GB=16   # legacy 16GB Mac\n"
        new_text, _ = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "48"},
        )
        assert "HOST_TOTAL_RAM_GB=48" in new_text
        assert "# legacy 16GB Mac" in new_text

    def test_appends_missing_key_under_managed_header(self, sync_module):
        original = "FOO=bar\n"
        new_text, changes = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "48"},
        )
        assert sync_module._MANAGED_BLOCK_HEADER in new_text
        assert "HOST_TOTAL_RAM_GB=48" in new_text
        assert any("added HOST_TOTAL_RAM_GB" in c for c in changes)

    def test_no_change_when_value_already_matches(self, sync_module):
        original = "HOST_TOTAL_RAM_GB=48\n"
        new_text, changes = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "48"},
        )
        # Idempotent — exact text returned, no diff
        assert new_text == original
        assert changes == []

    def test_empty_value_skipped_does_not_wipe_existing(self, sync_module):
        """If the probe failed and returned empty, we MUST NOT wipe the
        user's existing value."""
        original = "HOST_TOTAL_RAM_GB=48\n"
        new_text, changes = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "", "HOST_OS_BASELINE_GB": None},
        )
        assert new_text == original
        assert changes == []

    def test_secrets_in_other_lines_untouched(self, sync_module):
        """Critical safety property — secrets in unrelated lines must
        survive an .env update bit-for-bit."""
        original = (
            "ANTHROPIC_API_KEY=sk-ant-secret-do-not-touch\n"
            "GATEWAY_SECRET=long-token-here\n"
            "HOST_TOTAL_RAM_GB=16\n"
            "MEM0_POSTGRES_PASSWORD=db-password\n"
        )
        new_text, _ = sync_module.update_env_text(
            original, {"HOST_TOTAL_RAM_GB": "48"},
        )
        assert "sk-ant-secret-do-not-touch" in new_text
        assert "long-token-here" in new_text
        assert "db-password" in new_text


# ══════════════════════════════════════════════════════════════════════
# End-to-end via run()
# ══════════════════════════════════════════════════════════════════════

class TestRun:

    def test_creates_env_when_missing(self, sync_module, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        assert not env_file.exists()
        monkeypatch.setattr(sync_module, "detect_total_ram_gb", lambda: 48)
        monkeypatch.setattr(sync_module, "detect_os_baseline_gb", lambda: 10)
        rc = sync_module.run(env_file=env_file, dry_run=False)
        assert rc == 0
        text = env_file.read_text()
        assert "HOST_TOTAL_RAM_GB=48" in text
        assert "HOST_OS_BASELINE_GB=10" in text

    def test_idempotent_no_writes_when_in_sync(
        self, sync_module, tmp_path, monkeypatch,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("HOST_TOTAL_RAM_GB=48\nHOST_OS_BASELINE_GB=10\n")
        mtime_before = env_file.stat().st_mtime_ns

        monkeypatch.setattr(sync_module, "detect_total_ram_gb", lambda: 48)
        monkeypatch.setattr(sync_module, "detect_os_baseline_gb", lambda: 10)
        rc = sync_module.run(env_file=env_file, dry_run=False)
        assert rc == 0
        # Same mtime — no write happened
        assert env_file.stat().st_mtime_ns == mtime_before

    def test_dry_run_does_not_write(self, sync_module, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("HOST_TOTAL_RAM_GB=16\n")  # stale
        monkeypatch.setattr(sync_module, "detect_total_ram_gb", lambda: 48)
        monkeypatch.setattr(sync_module, "detect_os_baseline_gb", lambda: 10)
        rc = sync_module.run(env_file=env_file, dry_run=True)
        assert rc == 0
        # File unchanged despite probe disagreeing
        assert "HOST_TOTAL_RAM_GB=16" in env_file.read_text()
        assert "HOST_TOTAL_RAM_GB=48" not in env_file.read_text()

    def test_probe_failure_returns_1_and_does_not_modify(
        self, sync_module, tmp_path, monkeypatch,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=secret\nHOST_TOTAL_RAM_GB=48\n")
        before = env_file.read_text()
        monkeypatch.setattr(sync_module, "detect_total_ram_gb", lambda: None)
        rc = sync_module.run(env_file=env_file, dry_run=False)
        assert rc == 1
        assert env_file.read_text() == before  # untouched

    def test_updates_stale_value_in_existing_file(
        self, sync_module, tmp_path, monkeypatch,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-secret\n"
            "HOST_TOTAL_RAM_GB=16\n"           # stale (RAM was upgraded)
            "BRAVE_API_KEY=brave-key\n"
        )
        monkeypatch.setattr(sync_module, "detect_total_ram_gb", lambda: 64)
        monkeypatch.setattr(sync_module, "detect_os_baseline_gb", lambda: 10)
        rc = sync_module.run(env_file=env_file, dry_run=False)
        assert rc == 0
        text = env_file.read_text()
        assert "HOST_TOTAL_RAM_GB=64" in text
        assert "HOST_TOTAL_RAM_GB=16" not in text
        # Other lines preserved bit-for-bit
        assert "ANTHROPIC_API_KEY=sk-secret" in text
        assert "BRAVE_API_KEY=brave-key" in text
