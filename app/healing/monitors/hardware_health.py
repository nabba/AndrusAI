"""hardware_health — Surfaces host-disk degradation via SMART telemetry.

Gap #11 (2026-05-24): ``substrate_radar`` covers OS / container EOL.
``host_substrate_health`` covers free space + memory headroom.
``bit_rot_scan`` covers data-integrity hashes. None covers **physical
disk degradation** — the reallocated-sectors curve, the pending-sectors
counter, the slowly-creeping drive temperature.

This monitor reads ``workspace/healing/host_smart.jsonl`` (written
out-of-band by ``scripts/host_smart_collector.py`` running as a host
LaunchAgent — same architectural split as Q15's browse collector and
Q17.1's warm-spare manifest). It computes deltas vs a baseline and
alerts when degradation indicators trip.

Why a host-side companion script
================================

The gateway runs inside Docker on macOS. The container's view of disk
health is one virtualized layer removed from the physical drive —
smartctl on the container sees nothing useful. So the actual SMART
read lives on the host; the gateway monitor reads the JSONL the host
script produces. Same two-process split as browse + warm-spare.

What gets alerted
=================

  * **Reallocated sectors** delta > 5 in a 7-day window — actual bad
    blocks remapped. Even one is worth noting; >5 is meaningful.
  * **Pending sectors** ≥ 1 in the latest reading — sectors the drive
    suspects but hasn't confirmed. Pending → reallocated is the
    expected lifecycle.
  * **Uncorrectable errors** any positive value — hard read/write
    errors the drive couldn't recover.
  * **Temperature** ≥ 60 °C sustained across two consecutive readings.
  * **Tool unavailable** for >14 days — the LaunchAgent is dead, the
    host has rebooted into recovery mode, etc.

What this monitor doesn't do
============================

  * No SSD wear-level alerts. Those need vendor-specific decoding (NVMe
    spec doesn't standardize wear).
  * No drive-replacement actions. Operator gets the alert; replacement
    is an out-of-band physical act.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


NAME = "hardware_health"
CADENCE_SECONDS = 24 * 3600
MASTER_SWITCH_KEY = "hardware_health_monitor_enabled"

_INTERNAL_CADENCE_S = 24 * 3600
_STATE_FILE_NAME = "hardware_health_state.json"
_DEDUP_WINDOW_S = 14 * 86400

_TELEMETRY_FILE_NAME = "host_smart.jsonl"
_STALE_HOST_DATA_S = 14 * 86400

_REALLOCATED_DELTA_WARN = 5
_TEMP_WARN_CELSIUS = 60.0
# NVMe SSD wear: manufacturer-derived % of design endurance consumed.
# 80% is the typical pre-warning band; 100% means rated lifetime
# reached (still works but warranty endurance is exhausted).
_WEAR_PCT_WARN = 80
_WEAR_PCT_CRITICAL = 100
# NVMe available spare: drops below the vendor-set threshold means
# the spare pool is running out and the drive is one bad block from
# transitioning to read-only.
_SPARE_PCT_FALLBACK_THRESHOLD = 10  # used when the drive doesn't report a vendor threshold


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_hardware_health_monitor_enabled
        return get_hardware_health_monitor_enabled()
    except Exception:
        return os.getenv(
            "HARDWARE_HEALTH_MONITOR_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _telemetry_path() -> Path:
    return _workspace() / "healing" / _TELEMETRY_FILE_NAME


def _state_path() -> Path:
    return _workspace() / "healing" / _STATE_FILE_NAME


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"last_run_at": 0.0, "baselines": {}, "last_alert_at": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": 0.0, "baselines": {}, "last_alert_at": {}}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8",
        )
    except Exception:
        logger.debug("hardware_health: state write failed", exc_info=True)


# ── Data shapes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiskReading:
    """One disk's SMART snapshot. Fields are normalized across vendors
    by the host-side collector. ATA + NVMe fields coexist in one
    schema; rotational drives populate the first set, SSDs populate
    the wear/spare set, with shared temperature/power-on-hours/
    uncorrectable carrying the cross-vendor signal."""
    device: str               # e.g. "disk0", "nvme0n1"
    ts: float                 # epoch seconds
    reallocated_sectors: Optional[int]
    pending_sectors: Optional[int]
    uncorrectable_errors: Optional[int]
    temperature_celsius: Optional[float]
    power_on_hours: Optional[int]
    model: Optional[str]
    error: Optional[str]      # populated when SMART read failed
    # NVMe-specific (None on ATA drives):
    wear_pct: Optional[int] = None          # 0..100 design-endurance consumed
    spare_pct: Optional[int] = None         # 0..100 spare pool remaining
    spare_pct_threshold: Optional[int] = None  # vendor-supplied alert threshold
    unsafe_shutdowns: Optional[int] = None  # cumulative count


def _parse_reading(row: dict[str, Any]) -> Optional[DiskReading]:
    """Convert a raw JSONL row into a DiskReading. Missing optional
    fields are coerced to None rather than crashing the read."""
    device = row.get("device")
    ts = row.get("ts")
    if not isinstance(device, str) or not isinstance(ts, (int, float)):
        return None

    def _opt_int(v: Any) -> Optional[int]:
        return int(v) if isinstance(v, (int, float)) else None

    def _opt_float(v: Any) -> Optional[float]:
        return float(v) if isinstance(v, (int, float)) else None

    def _opt_str(v: Any) -> Optional[str]:
        return str(v) if isinstance(v, str) and v else None

    return DiskReading(
        device=device,
        ts=float(ts),
        reallocated_sectors=_opt_int(row.get("reallocated_sectors")),
        pending_sectors=_opt_int(row.get("pending_sectors")),
        uncorrectable_errors=_opt_int(row.get("uncorrectable_errors")),
        temperature_celsius=_opt_float(row.get("temperature_celsius")),
        power_on_hours=_opt_int(row.get("power_on_hours")),
        model=_opt_str(row.get("model")),
        error=_opt_str(row.get("error")),
        wear_pct=_opt_int(row.get("wear_pct")),
        spare_pct=_opt_int(row.get("spare_pct")),
        spare_pct_threshold=_opt_int(row.get("spare_pct_threshold")),
        unsafe_shutdowns=_opt_int(row.get("unsafe_shutdowns")),
    )


def _read_latest_per_device() -> dict[str, DiskReading]:
    """Walk the JSONL; return the most-recent reading per device."""
    p = _telemetry_path()
    if not p.exists():
        return {}
    latest: dict[str, DiskReading] = {}
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                reading = _parse_reading(row)
                if reading is None:
                    continue
                prior = latest.get(reading.device)
                if prior is None or reading.ts > prior.ts:
                    latest[reading.device] = reading
    except OSError:
        return latest
    return latest


def _read_two_most_recent_per_device() -> dict[str, list[DiskReading]]:
    """For temperature-sustained alerts we need the two most-recent
    readings. Returns ``{device: [newer, older]}`` (older may be absent
    on first run)."""
    p = _telemetry_path()
    if not p.exists():
        return {}
    by_device: dict[str, list[DiskReading]] = {}
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                r = _parse_reading(row)
                if r is None:
                    continue
                lst = by_device.setdefault(r.device, [])
                lst.append(r)
                lst.sort(key=lambda x: -x.ts)
                if len(lst) > 2:
                    del lst[2:]
    except OSError:
        return by_device
    return by_device


# ── Alert evaluation ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alert:
    device: str
    kind: str       # reallocated_jump | pending_present | uncorrectable | temp_sustained | stale_telemetry
    severity: str   # warning | critical
    detail: str


def evaluate(
    latest_per_device: dict[str, DiskReading],
    two_per_device: dict[str, list[DiskReading]],
    baselines: dict[str, dict[str, Any]],
    *,
    now: float,
) -> list[Alert]:
    """Pure-function alert evaluator. Returns a list of alerts; the
    run path persists state + fires Signal alerts deduped externally.
    """
    alerts: list[Alert] = []

    # Stale-telemetry check: if every reading is older than 14 days,
    # surface that the host collector probably died.
    if latest_per_device:
        newest_ts = max(r.ts for r in latest_per_device.values())
        if now - newest_ts > _STALE_HOST_DATA_S:
            age_days = (now - newest_ts) / 86400
            alerts.append(Alert(
                device="*",
                kind="stale_telemetry",
                severity="warning",
                detail=(
                    f"Last host SMART telemetry is {age_days:.1f} days old "
                    f"(threshold: {_STALE_HOST_DATA_S / 86400:.0f}d). The "
                    "LaunchAgent at scripts/host_smart_collector.plist may "
                    "have died or the host may have rebooted into a state "
                    "where the agent isn't running. Verify with `launchctl "
                    "list | grep andrusai`."
                ),
            ))

    for device, reading in latest_per_device.items():
        if reading.error:
            # Tool-level failure recorded by the host script — surface
            # once. Dedup is handled at the Signal layer (alert key
            # includes device + kind).
            alerts.append(Alert(
                device=device,
                kind="tool_error",
                severity="warning",
                detail=(
                    f"Host SMART collector reported an error reading {device}: "
                    f"{reading.error}"
                ),
            ))
            continue

        baseline = baselines.get(device) or {}

        # Reallocated-sector growth.
        if isinstance(reading.reallocated_sectors, int):
            baseline_realloc = baseline.get("reallocated_sectors")
            if isinstance(baseline_realloc, int):
                delta = reading.reallocated_sectors - baseline_realloc
                if delta >= _REALLOCATED_DELTA_WARN:
                    alerts.append(Alert(
                        device=device,
                        kind="reallocated_jump",
                        severity="critical",
                        detail=(
                            f"Reallocated sectors on {device} grew from "
                            f"{baseline_realloc} → {reading.reallocated_sectors} "
                            f"({delta:+d}). Drive is remapping bad blocks; "
                            f"consider scheduling a replacement."
                        ),
                    ))

        # Pending sectors — even one is worth noting.
        if isinstance(reading.pending_sectors, int) and reading.pending_sectors > 0:
            alerts.append(Alert(
                device=device,
                kind="pending_present",
                severity="warning",
                detail=(
                    f"{reading.pending_sectors} pending sector(s) on {device}. "
                    "These are sectors the drive suspects but hasn't yet "
                    "confirmed bad. Pending → reallocated is the expected "
                    "lifecycle; if the count keeps climbing, plan for "
                    "replacement."
                ),
            ))

        # Uncorrectable errors.
        if isinstance(reading.uncorrectable_errors, int) and reading.uncorrectable_errors > 0:
            alerts.append(Alert(
                device=device,
                kind="uncorrectable",
                severity="critical",
                detail=(
                    f"{reading.uncorrectable_errors} uncorrectable error(s) "
                    f"on {device}. The drive could not recover one or more "
                    "reads/writes. Data may be lost. Investigate immediately."
                ),
            ))

        # NVMe SSD wear — vendor-derived percentage of design endurance
        # consumed. Crosses 80% → warning, 100% → critical (rated
        # lifetime exhausted; drive still works but warranty
        # endurance is gone, transition to read-only mode is the
        # next failure step).
        if isinstance(reading.wear_pct, int):
            if reading.wear_pct >= _WEAR_PCT_CRITICAL:
                alerts.append(Alert(
                    device=device,
                    kind="wear_exhausted",
                    severity="critical",
                    detail=(
                        f"SSD wear on {device} has reached {reading.wear_pct}% of "
                        f"rated design endurance. The drive may transition to "
                        f"read-only mode at any time. Plan for replacement."
                    ),
                ))
            elif reading.wear_pct >= _WEAR_PCT_WARN:
                alerts.append(Alert(
                    device=device,
                    kind="wear_high",
                    severity="warning",
                    detail=(
                        f"SSD wear on {device} is {reading.wear_pct}% of design "
                        f"endurance ({_WEAR_PCT_WARN}% warning threshold). "
                        f"Begin planning for replacement; the drive will "
                        f"continue functioning until ~100%."
                    ),
                ))

        # NVMe spare pool — when remaining spare drops below the
        # vendor-supplied threshold, the drive is one remap away
        # from read-only. If the drive doesn't supply a threshold,
        # use a conservative 10% fallback.
        if isinstance(reading.spare_pct, int):
            threshold = (
                reading.spare_pct_threshold
                if isinstance(reading.spare_pct_threshold, int)
                else _SPARE_PCT_FALLBACK_THRESHOLD
            )
            if reading.spare_pct <= threshold:
                alerts.append(Alert(
                    device=device,
                    kind="spare_low",
                    severity="critical",
                    detail=(
                        f"SSD spare pool on {device} is {reading.spare_pct}% "
                        f"(vendor threshold: {threshold}%). One more bad-block "
                        f"event may transition the drive to read-only. "
                        f"Replace soon."
                    ),
                ))

        # Sustained temperature — need two consecutive readings ≥ 60 °C.
        readings_for_device = two_per_device.get(device, [])
        if len(readings_for_device) >= 2:
            temps = [r.temperature_celsius for r in readings_for_device[:2]]
            if all(isinstance(t, (int, float)) and t >= _TEMP_WARN_CELSIUS for t in temps):
                alerts.append(Alert(
                    device=device,
                    kind="temp_sustained",
                    severity="warning",
                    detail=(
                        f"Drive temperature on {device} sustained ≥{_TEMP_WARN_CELSIUS}°C "
                        f"across the two most-recent readings ({temps[1]:.1f}°C → "
                        f"{temps[0]:.1f}°C). Heat accelerates wear; check airflow."
                    ),
                ))

    return alerts


def _update_baselines(
    state: dict[str, Any],
    latest_per_device: dict[str, DiskReading],
) -> None:
    """Refresh the baseline snapshot of cumulative counters.

    The baseline is the **most-recent good reading** for each counter
    type. Once an alert fires for a reallocated-jump, we move the
    baseline forward so the *next* alert needs a *further* jump rather
    than refiring on the same delta.
    """
    baselines = state.setdefault("baselines", {})
    if not isinstance(baselines, dict):
        baselines = {}
        state["baselines"] = baselines
    for device, reading in latest_per_device.items():
        bl = baselines.setdefault(device, {})
        if not isinstance(bl, dict):
            bl = {}
            baselines[device] = bl
        if isinstance(reading.reallocated_sectors, int):
            bl["reallocated_sectors"] = reading.reallocated_sectors
        if isinstance(reading.power_on_hours, int):
            bl["power_on_hours"] = reading.power_on_hours
        if reading.model:
            bl["model"] = reading.model


def _emit_signal_alerts(state: dict[str, Any], alerts: list[Alert], *, now: float) -> int:
    """One Signal notify per (device, kind), deduped to 14d."""
    last_alerts = state.setdefault("last_alert_at", {})
    if not isinstance(last_alerts, dict):
        last_alerts = {}
        state["last_alert_at"] = last_alerts
    n_sent = 0
    for alert in alerts:
        key = f"{alert.device}:{alert.kind}"
        last = float(last_alerts.get(key, 0))
        if now - last < _DEDUP_WINDOW_S:
            continue
        last_alerts[key] = now
        try:
            from app.notify import notify
            notify(
                title=f"💾 Hardware health: {alert.kind} on {alert.device}",
                body=alert.detail,
                url="/cp/monitor",
                topic=f"hardware_health:{key}",
                critical=(alert.severity == "critical"),
                arbitrate=True,
            )
            n_sent += 1
        except Exception:
            logger.debug("hardware_health: notify failed", exc_info=True)
    return n_sent


def run(*, now: Optional[float] = None) -> dict[str, Any]:
    """One probe pass. Daily internal cadence. Returns a summary."""
    if not _enabled():
        return {"ran": False, "skipped": True}

    cur = float(now) if now is not None else time.time()
    state = _read_state()
    last = float(state.get("last_run_at", 0))
    if last > 0 and cur - last < _INTERNAL_CADENCE_S:
        return {"ran": False}

    state["last_run_at"] = cur

    if not _telemetry_path().exists():
        _write_state(state)
        return {
            "ran": True,
            "skipped": True,
            "reason": "no_host_telemetry",
            "hint": (
                "Install the host-side collector via "
                "scripts/install_host_smart_collector.sh to populate "
                "workspace/healing/host_smart.jsonl."
            ),
        }

    latest = _read_latest_per_device()
    two_per = _read_two_most_recent_per_device()
    baselines = state.get("baselines") or {}
    alerts = evaluate(latest, two_per, baselines, now=cur)

    n_sent = _emit_signal_alerts(state, alerts, now=cur)
    _update_baselines(state, latest)
    _write_state(state)

    return {
        "ran": True,
        "iso": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
        "n_devices": len(latest),
        "n_alerts": len(alerts),
        "n_signals_sent": n_sent,
        "alerts": [asdict(a) for a in alerts],
    }


__all__ = ["run", "evaluate", "DiskReading", "Alert"]
