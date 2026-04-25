#!/usr/bin/env python3
"""
sync_host_capacity.py — probe the HOST's true hardware capacity and
write it into .env so the LLM registry scanner can size local-model
proposals correctly.

Why this exists
---------------
``app/llm_registry_scanner.probe_host_capacity()`` runs INSIDE the
Docker container. On Docker-Desktop-for-Mac that means /proc/meminfo
and Docker /info both report the VM's slice (e.g. 23.4 GB), not the
macOS host's true RAM (e.g. 48 GB) — and Ollama runs on the macOS
host, so the VM's slice is the wrong number to budget against.

The probe inside the container will respect ``HOST_TOTAL_RAM_GB`` if
set; this script is the canonical way to set it. Run once at install
time. Re-run any time the hardware changes (RAM upgrade, different
machine via the same repo).

Usage
-----
    # Detect and write — idempotent (only writes if changed)
    python3 scripts/sync_host_capacity.py

    # Show what would change without writing
    python3 scripts/sync_host_capacity.py --dry-run

    # Use a non-default .env path
    python3 scripts/sync_host_capacity.py --env-file /path/to/.env

Exit codes
----------
    0  success (or already in sync)
    1  probe failed (couldn't detect RAM)
    2  write failed (permissions, etc.)

This script intentionally has zero third-party dependencies — it runs
during install, before pip install -r requirements.txt has executed.
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("sync_host_capacity")

# Variables this script manages in .env. Each maps to a probe function
# that returns a string value (or None if the probe couldn't determine
# it). Order is preserved when appending new entries.
_MANAGED_KEYS: tuple[str, ...] = (
    "HOST_TOTAL_RAM_GB",       # true host RAM
    "HOST_OS_BASELINE_GB",     # OS overhead estimate (platform-derived)
)


# ── Probes ────────────────────────────────────────────────────────────────

def detect_total_ram_gb() -> int | None:
    """Probe true host total RAM in GB (rounded down).

    Returns None if every fallback fails — the caller should keep
    whatever value is already in .env rather than overwrite with
    nonsense.
    """
    sysname = platform.system()

    # macOS — sysctl hw.memsize is the only authoritative source.
    if sysname == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                timeout=2, text=True,
            ).strip()
            bytes_total = int(out)
            return int(bytes_total / (1024 ** 3))
        except Exception as exc:
            logger.warning(f"sysctl probe failed: {exc}")
            return None

    # Linux bare-metal — /proc/meminfo MemTotal is reliable when not
    # running inside a memory-limited cgroup (which is the case here
    # since this script is meant to run on the HOST, not in a
    # container).
    if sysname == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return int(kb / 1024 / 1024)
        except Exception as exc:
            logger.warning(f"/proc/meminfo probe failed: {exc}")
            return None

    # Unknown platform — bail.
    logger.warning(f"unsupported platform: {sysname}")
    return None


def detect_os_baseline_gb() -> int:
    """Estimate OS + system service memory overhead by platform.

    Numbers come from observed ``Activity Monitor`` / ``free -h`` on
    idle systems. Conservative — better to leave a model proposal
    slightly small than overcommit and hit the SIGKILL pattern.
    """
    sysname = platform.system()
    if sysname == "Darwin":
        # macOS kernel + WindowServer + system frameworks idle ~8-12 GB
        # on Apple Silicon. Pick 10 as a round middle.
        return 10
    if sysname == "Linux":
        # Bare-metal Linux idle is typically 1-3 GB. Use 4 to leave
        # headroom for systemd journals and unattended upgrades.
        return 4
    return 8


# ── .env file editing ─────────────────────────────────────────────────────
#
# Idempotent edit:
#   * If "KEY=value" already exists with the right value → no-op.
#   * If "KEY=" exists with a different value → replace in place.
#   * If "KEY=" doesn't exist → append at the bottom under a managed-block
#     comment so subsequent runs can find it.

_MANAGED_BLOCK_HEADER = "# ─── Auto-detected host capacity (sync_host_capacity.py) ───"


def parse_env(text: str) -> dict[str, str]:
    """Parse .env into key→value, preserving last-wins semantics."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, raw = s.partition("=")
        key = key.strip()
        # Strip inline comments (anything after a `#` that's not in quotes)
        val = raw.split("#", 1)[0].strip()
        # Strip surrounding quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def update_env_text(text: str, updates: dict[str, str]) -> tuple[str, list[str]]:
    """Apply ``updates`` to .env content. Returns (new_text, change_log).

    Empty/None values in ``updates`` are skipped (probe failures
    shouldn't wipe existing user settings).
    """
    if not text.endswith("\n") and text:
        text += "\n"
    lines = text.splitlines(keepends=True)
    keys_to_set = {k: v for k, v in updates.items() if v not in ("", None)}
    if not keys_to_set:
        return text, []

    changes: list[str] = []
    new_lines: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.lstrip()
        # Detect existing assignment
        m = re.match(r"([A-Z][A-Z0-9_]*)\s*=", stripped)
        if m and m.group(1) in keys_to_set:
            key = m.group(1)
            new_val = keys_to_set[key]
            # Reconstruct with original leading whitespace + comment if any
            comment_idx = line.find("#", line.find("=") + 1)
            comment = line[comment_idx:].rstrip("\n") if comment_idx > -1 else ""
            indent = line[: len(line) - len(line.lstrip())]
            new_line = f"{indent}{key}={new_val}"
            if comment:
                # Keep the comment column-aligned roughly the same way
                pad = max(1, 40 - len(new_line))
                new_line += " " * pad + comment
            new_line += "\n"
            # Only record as a change if it differs
            if new_line != line:
                changes.append(f"updated {key}: {_short(line.strip())}  →  {key}={new_val}")
            new_lines.append(new_line)
            seen.add(key)
        else:
            new_lines.append(line)

    # Append any keys that weren't in the file under a managed block
    missing = [k for k in keys_to_set if k not in seen]
    if missing:
        if not new_lines or not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append("\n")
        new_lines.append(f"{_MANAGED_BLOCK_HEADER}\n")
        for key in missing:
            val = keys_to_set[key]
            new_lines.append(f"{key}={val}\n")
            changes.append(f"added {key}={val}")

    return "".join(new_lines), changes


def _short(s: str, n: int = 40) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ── Top-level orchestration ───────────────────────────────────────────────

def run(env_file: Path, dry_run: bool = False) -> int:
    """Detect host capacity and update env_file. Returns exit code."""
    logger.info(f"probing host capacity ({platform.system()})...")

    ram_gb = detect_total_ram_gb()
    if ram_gb is None:
        logger.error("could not detect host RAM; aborting (existing .env unchanged)")
        return 1
    os_gb = detect_os_baseline_gb()

    logger.info(f"detected: HOST_TOTAL_RAM_GB={ram_gb}  HOST_OS_BASELINE_GB={os_gb}")

    if not env_file.exists():
        logger.info(f"{env_file} does not exist — creating")
        existing_text = ""
    else:
        existing_text = env_file.read_text()

    existing = parse_env(existing_text)
    updates = {
        "HOST_TOTAL_RAM_GB": str(ram_gb),
        "HOST_OS_BASELINE_GB": str(os_gb),
    }

    # Drop updates that match what's already there — preserves file
    # mtime when nothing actually changes (cleaner for git status).
    same = {k: v for k, v in updates.items() if existing.get(k) == v}
    new = {k: v for k, v in updates.items() if existing.get(k) != v}
    for k, v in same.items():
        logger.info(f"already in sync: {k}={v}")
    if not new:
        logger.info("no changes needed — .env is up to date")
        return 0

    new_text, changes = update_env_text(existing_text, new)
    if not changes:
        logger.info("no changes computed — .env is up to date")
        return 0

    if dry_run:
        logger.info("DRY RUN — would have made these changes:")
        for c in changes:
            logger.info(f"  • {c}")
        return 0

    try:
        env_file.write_text(new_text)
    except OSError as exc:
        logger.error(f"could not write {env_file}: {exc}")
        return 2

    logger.info(f"updated {env_file}:")
    for c in changes:
        logger.info(f"  • {c}")
    logger.info("done — restart the gateway (docker compose up -d gateway) to apply")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sync_host_capacity.py",
        description="Probe host hardware capacity and update .env so the "
                    "LLM registry scanner can size local-model proposals "
                    "correctly. Idempotent — safe to run repeatedly.",
    )
    default_env = Path(__file__).resolve().parent.parent / ".env"
    p.add_argument("--env-file", type=Path, default=default_env,
                   help=f"path to .env (default: {default_env})")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would change without writing")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="suppress info logging (warnings + errors only)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )
    return run(env_file=args.env_file, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
