#!/usr/bin/env python3
"""host_smart_collector — Host-side SMART telemetry writer.

Gap #11 (2026-05-24): the gateway runs in Docker; SMART data is below
the virtualization boundary. This script runs on the host (via the
LaunchAgent at scripts/host_smart_collector.plist), reads SMART for
every physical disk via ``smartctl``, normalizes the output, and
appends one JSON line per device to the bind-mounted workspace path
``$WORKSPACE_ROOT/healing/host_smart.jsonl`` (default
``~/BotArmy/crewai-team/workspace``).

The container-side ``app.healing.monitors.hardware_health`` reads
that file and surfaces degradation alerts to Signal.

Why a separate Python script (not a Bash one-liner)
====================================================

Bash + smartctl + jq works for one disk one platform. The moment we
need cross-disk iteration + degraded-attribute heuristics + multiple
SMART output formats (macOS APFS NVMe / Linux SATA / NVMe-CLI), a
Python adapter keeps the code reviewable.

Stdlib only — runs on a fresh macOS / Linux host without pip install.
``smartctl`` is the only external dependency; if missing, the script
writes a single ``error: tool_unavailable`` row so the gateway monitor
can surface that as an actionable alert.

Run modes
=========

  * Default (no args): one pass over every disk; appends to JSONL;
    exits 0. This is what the LaunchAgent invokes (daily at 04:00).
  * ``--dry-run``: prints the rows that *would* be written; no file IO.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DEFAULT = Path.home() / "BotArmy" / "crewai-team" / "workspace"


def _workspace_root() -> Path:
    raw = os.environ.get("ANDRUSAI_WORKSPACE_ROOT", "").strip()
    if raw:
        return Path(raw)
    return WORKSPACE_DEFAULT


def _telemetry_path() -> Path:
    return _workspace_root() / "healing" / "host_smart.jsonl"


def _list_disks() -> list[str]:
    """Enumerate disk device paths.

    Strategy:
      * On macOS: ``diskutil list`` then map physical disks to
        ``/dev/disk*``. We try ``/dev/disk0`` first (typical boot
        drive) and skip /dev/disk1+ unless they look like additional
        physical devices.
      * On Linux: ``/sys/block`` listing for ``sd*`` / ``nvme*n*``
        names.
      * Else: empty list — the run path writes a single tool-error
        record.
    """
    devices: list[str] = []
    plat = sys.platform
    if plat == "darwin":
        # Use the non-plist `diskutil list` whose header lines are
        # literally `/dev/diskN (...):`. The plist variant references
        # disks by short identifier ("disk0") rather than the device
        # path, so the regex would silently fall through to the
        # disk0 fallback even on multi-disk hosts.
        try:
            out = subprocess.run(
                ["diskutil", "list"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            for m in re.finditer(r"^/dev/(disk\d+)\s*\(internal[^)]*physical\)", out, re.MULTILINE):
                path = f"/dev/{m.group(1)}"
                if path not in devices:
                    devices.append(path)
            if not devices:
                # Fallback: any /dev/disk\d+ header line (covers
                # external/usb physical disks too).
                for m in re.finditer(r"^/dev/(disk\d+)\s*\(", out, re.MULTILINE):
                    path = f"/dev/{m.group(1)}"
                    if path not in devices:
                        devices.append(path)
        except Exception:
            devices.append("/dev/disk0")  # last-resort fallback
    elif plat.startswith("linux"):
        sysblock = Path("/sys/block")
        if sysblock.exists():
            for entry in sysblock.iterdir():
                name = entry.name
                if name.startswith("sd") and re.fullmatch(r"sd[a-z]+", name):
                    devices.append(f"/dev/{name}")
                elif name.startswith("nvme") and re.fullmatch(r"nvme\d+n\d+", name):
                    devices.append(f"/dev/{name}")
    return devices


def _smartctl_attributes(device: str) -> dict:
    """Call ``smartctl -j -A <device>`` and parse the JSON. Returns
    raw smartctl json or ``{"error": "..."}`` on failure."""
    if not shutil.which("smartctl"):
        return {"error": "smartctl_not_installed"}
    try:
        proc = subprocess.run(
            ["smartctl", "-j", "-A", device],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return {"error": f"smartctl_invocation_failed: {exc}"}
    # smartctl returns non-zero in many normal cases (USB bridge, etc.)
    # but still writes valid JSON. Parse the stdout regardless.
    out = proc.stdout.strip()
    if not out:
        return {"error": f"smartctl_empty_output (rc={proc.returncode})"}
    try:
        return json.loads(out)
    except Exception as exc:
        return {"error": f"smartctl_json_parse: {exc}"}


def _normalize(device: str, smart: dict) -> dict:
    """Convert smartctl JSON into the schema the gateway monitor reads.

    Maps standard SMART attribute IDs by name; falls back to nvme_smart_health
    fields for NVMe devices. Missing fields stay absent (None on read).
    """
    now = time.time()
    row: dict = {
        "device": device.replace("/dev/", ""),
        "ts": now,
    }
    if "error" in smart:
        row["error"] = smart["error"]
        return row

    # SATA — attributes table.
    attrs = (smart.get("ata_smart_attributes") or {}).get("table") or []
    by_id = {a.get("id"): a for a in attrs if isinstance(a, dict)}
    # SMART attribute IDs:
    #   5   = Reallocated_Sector_Ct
    #   197 = Current_Pending_Sector
    #   198 = Offline_Uncorrectable
    #   194 = Temperature_Celsius
    #   9   = Power_On_Hours
    def _ata_raw(id_: int):
        a = by_id.get(id_) or {}
        raw = (a.get("raw") or {}).get("value")
        return raw if isinstance(raw, (int, float)) else None

    row["reallocated_sectors"] = _ata_raw(5)
    row["pending_sectors"] = _ata_raw(197)
    row["uncorrectable_errors"] = _ata_raw(198)
    temp = _ata_raw(194)
    if isinstance(temp, (int, float)):
        row["temperature_celsius"] = float(temp)
    row["power_on_hours"] = _ata_raw(9)

    # NVMe — nvme_smart_health_information_log block. SSDs don't have
    # ATA-style reallocated/pending counters; the meaningful health
    # surface is wear + spare + media error count. We map onto the
    # existing schema where the semantic equivalence is clear and
    # add wear_pct + spare_pct + unsafe_shutdowns as NVMe-specific
    # fields the monitor reads alongside.
    nvme = smart.get("nvme_smart_health_information_log") or {}
    if isinstance(nvme, dict) and nvme:
        cw = nvme.get("critical_warning")
        if isinstance(cw, int) and cw > 0:
            row["uncorrectable_errors"] = (
                (row.get("uncorrectable_errors") or 0) + cw
            )
        # NVMe `media_errors` is the SSD equivalent of ATA's
        # uncorrectable count — count of unrecoverable read/write
        # operations. A non-zero value is the same signal as ATA
        # uncorrectable_errors > 0.
        media_errors = nvme.get("media_errors")
        if isinstance(media_errors, int):
            row["uncorrectable_errors"] = (
                (row.get("uncorrectable_errors") or 0) + media_errors
            )
        temp_c = nvme.get("temperature")
        if isinstance(temp_c, (int, float)):
            row["temperature_celsius"] = float(temp_c)
        poh = nvme.get("power_on_hours")
        if isinstance(poh, (int, float)):
            row["power_on_hours"] = int(poh)
        # SSD wear: percentage_used is the manufacturer-derived "% of
        # design endurance consumed" — 100% means the drive has reached
        # its rated lifetime (still works, but write-endurance warranty
        # has expired). >80% is the typical warning band.
        pct_used = nvme.get("percentage_used")
        if isinstance(pct_used, int):
            row["wear_pct"] = pct_used
        # Available spare: 100 = full, drops as bad blocks are remapped
        # from the spare pool. `available_spare_threshold` is the
        # vendor-supplied below-which-alert value. We record both so
        # the monitor can decide.
        spare = nvme.get("available_spare")
        if isinstance(spare, int):
            row["spare_pct"] = spare
        spare_threshold = nvme.get("available_spare_threshold")
        if isinstance(spare_threshold, int):
            row["spare_pct_threshold"] = spare_threshold
        # Unsafe shutdowns: drive lost power before a clean
        # flush. Rising count = host/PSU issue; recorded for the
        # operator but does not itself trigger an alert in the
        # gateway monitor (Macs have hard kernel panics frequently
        # enough that this baselines high).
        unsafe = nvme.get("unsafe_shutdowns")
        if isinstance(unsafe, int):
            row["unsafe_shutdowns"] = unsafe

    model = smart.get("model_name") or smart.get("model_family")
    if isinstance(model, str):
        row["model"] = model
    return row


def _append(row: dict) -> None:
    path = _telemetry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Host SMART telemetry collector")
    parser.add_argument("--dry-run", action="store_true", help="Print rows only.")
    args = parser.parse_args()

    devices = _list_disks()
    if not devices:
        row = {
            "device": "*",
            "ts": time.time(),
            "error": "no_disks_enumerated",
        }
        if args.dry_run:
            print(json.dumps(row, indent=2))
        else:
            _append(row)
        return 0

    n = 0
    for device in devices:
        smart = _smartctl_attributes(device)
        row = _normalize(device, smart)
        if args.dry_run:
            print(json.dumps(row, indent=2))
        else:
            _append(row)
        n += 1
    if args.dry_run:
        print(f"\n# dry-run: {n} row(s) would be appended to {_telemetry_path()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
