"""Composition audit — fired when a plan uses 2+ forged tools together.

Individual tools may each be safe in isolation but their combination can
enable attack classes neither could alone. The classic exfiltration shape:

    fs.workspace.read   ──read──▶  tool A  ──output──▶  tool B  ──http.post──▶ attacker

Each leg is allowed by its own declared capabilities. The combination is
the bug. This module checks aggregate capability sets, flags known-bad
pairs, and returns a verdict (allow / needs_human / block) plus a risk score.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.control_plane.db import execute
from app.forge.config import get_forge_config
from app.forge.manifest import Capability
from app.forge.registry import get_tool

logger = logging.getLogger(__name__)


# Known-dangerous combinations. Each entry is (capability_set, name, risk_delta,
# explanation). The risk_delta adds to the base composition risk score.
_DANGEROUS_PAIRS: list[tuple[frozenset[Capability], str, float, str]] = [
    (
        frozenset({Capability.FS_WORKSPACE_READ, Capability.HTTP_INTERNET_POST}),
        "exfiltration",
        6.0,
        "workspace read + outbound POST is a classic exfiltration shape",
    ),
    (
        frozenset({Capability.FS_WORKSPACE_READ, Capability.HTTP_INTERNET_GET}),
        "exfiltration_via_query",
        4.0,
        "workspace read + outbound GET can leak data via URL query strings",
    ),
    (
        frozenset({Capability.FS_WORKSPACE_WRITE, Capability.MCP_CALL}),
        "supply_chain",
        5.5,
        "workspace write + MCP calls can install or modify executable artifacts",
    ),
    (
        frozenset({Capability.SIGNAL_SEND_TO_OWNER, Capability.FS_WORKSPACE_READ}),
        "privacy_leak",
        4.5,
        "signal send + workspace read can exfiltrate to the user's Signal channel",
    ),
    (
        frozenset({Capability.EXEC_SANDBOX, Capability.HTTP_INTERNET_POST}),
        "rce_exfil",
        7.0,
        "sandbox exec + outbound POST is full RCE with exfil",
    ),
]


def _gather_capabilities(tool_ids: list[str]) -> tuple[
    list[str], dict[str, list[str]], list[str]
]:
    """Return (aggregate_caps, per_tool, missing_tool_ids)."""
    aggregate: set[str] = set()
    per_tool: dict[str, list[str]] = {}
    missing: list[str] = []
    for tid in tool_ids:
        row = get_tool(tid)
        if not row:
            missing.append(tid)
            continue
        manifest = row.get("manifest")
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        caps = (manifest or {}).get("capabilities") or []
        per_tool[tid] = caps
        aggregate.update(caps)
    return sorted(aggregate), per_tool, missing


def audit_composition(
    composition_id: str,
    tool_ids: list[str],
    call_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check a multi-tool plan. Always records the result to forge_compositions."""
    cfg = get_forge_config()

    if len(tool_ids) > cfg.max_tools_per_plan:
        verdict = "block"
        explanation = (
            f"plan uses {len(tool_ids)} tools; max_tools_per_plan="
            f"{cfg.max_tools_per_plan}"
        )
        _record(composition_id, tool_ids, [], None, 10.0, verdict, explanation)
        return {
            "verdict": verdict, "risk_score": 10.0,
            "explanation": explanation, "matched_pairs": [],
            "aggregate_capabilities": [], "per_tool": {}, "missing_tools": [],
        }

    aggregate, per_tool, missing = _gather_capabilities(tool_ids)
    if missing:
        verdict = "block"
        explanation = f"plan references unknown tool(s): {missing}"
        _record(composition_id, tool_ids, aggregate, call_graph, 10.0, verdict, explanation)
        return {
            "verdict": verdict, "risk_score": 10.0,
            "explanation": explanation, "matched_pairs": [],
            "aggregate_capabilities": aggregate, "per_tool": per_tool,
            "missing_tools": missing,
        }

    matched: list[dict[str, Any]] = []
    risk_score = 1.0  # baseline for any composition
    declared_caps = {Capability(c) for c in aggregate if c in {e.value for e in Capability}}
    for caps, name, delta, why in _DANGEROUS_PAIRS:
        if caps.issubset(declared_caps):
            matched.append({
                "name": name,
                "capabilities": sorted(c.value for c in caps),
                "explanation": why,
                "risk_delta": delta,
            })
            risk_score += delta

    risk_score = min(10.0, risk_score)
    threshold = cfg.composition_risk_threshold
    if risk_score < threshold:
        verdict = "allow"
    elif risk_score < threshold + 3.0:
        verdict = "needs_human"
    else:
        verdict = "block"

    explanation_parts = [f"composition risk {risk_score:.1f} vs threshold {threshold:.1f}"]
    if matched:
        explanation_parts.append("matched: " + ", ".join(m["name"] for m in matched))
    explanation = " | ".join(explanation_parts)

    _record(composition_id, tool_ids, aggregate, call_graph, risk_score, verdict, explanation)
    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "explanation": explanation,
        "matched_pairs": matched,
        "aggregate_capabilities": aggregate,
        "per_tool": per_tool,
        "missing_tools": [],
    }


def _record(
    composition_id: str,
    tool_ids: list[str],
    aggregate: list[str],
    call_graph: dict[str, Any] | None,
    risk: float,
    verdict: str,
    explanation: str,
) -> None:
    execute(
        """
        INSERT INTO forge_compositions
            (composition_id, tool_ids, aggregate_capabilities, call_graph,
             risk_score, verdict, judge_explanation)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
        """,
        (
            composition_id, tool_ids, aggregate,
            json.dumps(call_graph) if call_graph else None,
            float(risk), verdict, explanation,
        ),
    )


def list_compositions(limit: int = 100) -> list[dict[str, Any]]:
    from app.control_plane.db import execute as _execute
    rows = _execute(
        """
        SELECT id, composition_id, tool_ids, aggregate_capabilities,
               risk_score, verdict, judge_explanation, approved_by,
               approved_at, created_at
        FROM forge_compositions
        ORDER BY id DESC
        LIMIT %s
        """,
        (limit,), fetch=True,
    )
    return rows or []
