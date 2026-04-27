"""Anomaly detection on forged-tool invocation telemetry.

Compares the last hour of invocations against the prior 24h baseline. Two
signals trigger demotion:

  - error_rate spike: error_rate(last_1h) > max(2 * baseline, 0.30)
  - latency drift:   p95_latency(last_1h) > 3 * baseline_p95 (and > 1s)

A tool flagged on either signal is demoted one tier (ACTIVE→CANARY etc).
SHADOW tools are not demoted further on anomalies — they're already pre-
production. Killed/Deprecated/Quarantined are skipped entirely.
"""
from __future__ import annotations

import logging
from typing import Any

from app.control_plane.db import execute
from app.forge.manifest import ToolStatus
from app.forge.registry import list_tools, transition

logger = logging.getLogger(__name__)


_DEMOTE_FROM: dict[ToolStatus, ToolStatus] = {
    ToolStatus.ACTIVE: ToolStatus.CANARY,
    ToolStatus.CANARY: ToolStatus.SHADOW,
}


def _telemetry(tool_id: str) -> dict[str, Any]:
    """Fetch error rate + p95 latency for last 1h and prior 24h."""
    rows = execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN created_at > NOW() - INTERVAL '1 hour'
                               AND error IS NULL THEN 1 ELSE 0 END), 0) AS ok_1h,
            COALESCE(SUM(CASE WHEN created_at > NOW() - INTERVAL '1 hour'
                               AND error IS NOT NULL THEN 1 ELSE 0 END), 0) AS err_1h,
            COALESCE(SUM(CASE WHEN created_at <= NOW() - INTERVAL '1 hour'
                               AND created_at > NOW() - INTERVAL '24 hours'
                               AND error IS NULL THEN 1 ELSE 0 END), 0) AS ok_base,
            COALESCE(SUM(CASE WHEN created_at <= NOW() - INTERVAL '1 hour'
                               AND created_at > NOW() - INTERVAL '24 hours'
                               AND error IS NOT NULL THEN 1 ELSE 0 END), 0) AS err_base,
            COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                     FILTER (WHERE created_at > NOW() - INTERVAL '1 hour'
                                   AND error IS NULL), 0) AS p95_1h,
            COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                     FILTER (WHERE created_at <= NOW() - INTERVAL '1 hour'
                                   AND created_at > NOW() - INTERVAL '24 hours'
                                   AND error IS NULL), 0) AS p95_base
        FROM forge_invocations
        WHERE tool_id = %s
        """,
        (tool_id,), fetch=True,
    )
    if not rows:
        return {}
    r = rows[0]
    total_1h = int(r["ok_1h"]) + int(r["err_1h"])
    total_base = int(r["ok_base"]) + int(r["err_base"])
    err_rate_1h = (int(r["err_1h"]) / total_1h) if total_1h else 0.0
    err_rate_base = (int(r["err_base"]) / total_base) if total_base else 0.0
    return {
        "total_1h": total_1h,
        "total_base": total_base,
        "err_rate_1h": err_rate_1h,
        "err_rate_base": err_rate_base,
        "p95_ms_1h": float(r["p95_1h"] or 0),
        "p95_ms_base": float(r["p95_base"] or 0),
    }


def _classify(t: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (anomalous, reasons)."""
    reasons: list[str] = []
    # Need enough samples to be statistically meaningful.
    if t.get("total_1h", 0) < 5:
        return False, []

    base_err = t["err_rate_base"]
    last_err = t["err_rate_1h"]
    err_threshold = max(0.30, 2.0 * base_err) if base_err > 0 else 0.30
    if last_err > err_threshold:
        reasons.append(
            f"error_rate spike: {last_err:.0%} (last 1h) vs {base_err:.0%} (baseline)"
        )

    base_p95 = t.get("p95_ms_base", 0.0)
    last_p95 = t.get("p95_ms_1h", 0.0)
    if base_p95 > 0 and last_p95 > 1000 and last_p95 > 3.0 * base_p95:
        reasons.append(
            f"p95 latency drift: {last_p95:.0f}ms (last 1h) vs {base_p95:.0f}ms (baseline)"
        )

    return bool(reasons), reasons


def run_anomaly_check() -> dict[str, Any]:
    """Inspect every ACTIVE/CANARY tool, demote on anomaly. Idempotent."""
    targets: list[dict[str, Any]] = []
    for s in ("ACTIVE", "CANARY"):
        targets.extend(list_tools(status=s, limit=500))

    summary: dict[str, Any] = {
        "checked": 0, "anomalous": 0, "demoted": 0, "details": [],
    }
    for row in targets:
        summary["checked"] += 1
        tool_id = row["tool_id"]
        t = _telemetry(tool_id)
        anomalous, reasons = _classify(t)
        if not anomalous:
            continue
        summary["anomalous"] += 1
        current = ToolStatus(row["status"])
        target = _DEMOTE_FROM.get(current)
        if not target:
            continue
        ok = transition(
            tool_id, target, actor="forge.anomaly",
            reason=" ; ".join(reasons),
            audit_data={"telemetry": t, "reasons": reasons},
        )
        if ok:
            summary["demoted"] += 1
            summary["details"].append({
                "tool_id": tool_id, "from": current.value,
                "to": target.value, "reasons": reasons, "telemetry": t,
            })

    logger.info(
        "forge.anomaly: checked=%d anomalous=%d demoted=%d",
        summary["checked"], summary["anomalous"], summary["demoted"],
    )
    return summary
