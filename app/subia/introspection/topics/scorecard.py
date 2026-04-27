"""Scorecard topic — Phase 9 Butlin/RSM/SK + Phase 8 drift findings.

When user asks "what's your consciousness scorecard?" or "any drift in
yourself?", surface the auto-regenerated probe results + most recent
drift-detection signals from the immutable narrative audit.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def gather() -> dict:
    out: dict = {
        "phase9_exit_criteria": {},
        "butlin_summary": {},
        "rsm_summary": {},
        "sk_summary": {},
        "drift_findings": [],
        "scorecard_path": "",
    }

    # ── Phase 9 exit criteria ──────────────────────────────────────
    try:
        from app.subia.probes.scorecard import meets_exit_criteria
        passed, report = meets_exit_criteria()
        out["phase9_exit_criteria"] = {
            "passed": bool(passed),
            **{
                k: (v.get("observed") if isinstance(v, dict) else v)
                for k, v in (report or {}).items()
            },
        }
    except Exception as exc:
        logger.debug("introspection.scorecard: exit criteria failed: %s", exc)

    # ── Per-probe summaries ────────────────────────────────────────
    try:
        from app.subia.probes.butlin import summary as butlin_summary
        out["butlin_summary"] = butlin_summary()
    except Exception:
        try:
            from app.subia.probes.butlin import run_all
            results = run_all() or []
            by_status: dict = {}
            for r in results:
                k = (r.status.value if hasattr(r.status, "value")
                     else str(r.status))
                by_status[k] = by_status.get(k, 0) + 1
            out["butlin_summary"] = {
                "total": len(results), "by_status": by_status,
            }
        except Exception as exc:
            logger.debug("introspection.scorecard: butlin failed: %s", exc)

    try:
        from app.subia.probes.rsm import run_all as rsm_run_all
        rsm = rsm_run_all() or []
        out["rsm_summary"] = {"total": len(rsm)}
    except Exception:
        pass

    try:
        from app.subia.probes.sk import run_all as sk_run_all
        sk = sk_run_all() or []
        out["sk_summary"] = {"total": len(sk)}
    except Exception:
        pass

    # ── Drift findings from narrative audit ────────────────────────
    try:
        from app.subia.safety.narrative_audit import read_audit_entries
        entries = read_audit_entries(limit=40) or []
        for e in entries:
            finding = str(getattr(e, "finding", "") or "")
            if "drift" in finding.lower() or "divergence" in finding.lower():
                out["drift_findings"].append({
                    "at": getattr(e, "at", ""),
                    "severity": getattr(e, "severity", "info"),
                    "finding": finding[:240],
                })
                if len(out["drift_findings"]) >= 5:
                    break
    except Exception as exc:
        logger.debug("introspection.scorecard: drift gather failed: %s", exc)

    # ── Scorecard file location for cross-reference ───────────────
    for cand in (
        "/app/app/subia/probes/SCORECARD.md",
        "app/subia/probes/SCORECARD.md",
    ):
        if Path(cand).exists():
            out["scorecard_path"] = cand
            break

    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Consciousness scorecard + drift (Phase 9 + Phase 8)"]

    ec = data.get("phase9_exit_criteria", {}) or {}
    if ec:
        passed = ec.get("passed")
        lines.append(
            f"Phase 9 exit criteria: {'PASSED' if passed else 'FAILED'}"
        )
        for k in ("butlin_strong", "butlin_fail", "butlin_absent",
                  "rsm_present", "sk_pass"):
            if k in ec:
                lines.append(f"  - {k}: {ec[k]}")

    bs = data.get("butlin_summary", {}) or {}
    if bs:
        by = bs.get("by_status", {}) or {}
        lines.append(
            f"Butlin et al. 2023 indicators: {bs.get('total','?')} total"
        )
        for status, n in by.items():
            lines.append(f"  - {status}: {n}")

    rsm = data.get("rsm_summary", {})
    sk = data.get("sk_summary", {})
    if rsm or sk:
        lines.append(
            f"RSM signatures: {rsm.get('total','?')} | "
            f"SK evaluation tests: {sk.get('total','?')}"
        )

    if data.get("drift_findings"):
        lines.append("Recent drift findings (Phase 8 — three-signal divergence audit):")
        for f in data["drift_findings"][:5]:
            lines.append(
                f"  - {f['at']} [{f['severity']}] {f['finding'][:200]}"
            )
    else:
        lines.append(
            "Recent drift findings: (none in narrative audit — "
            "self-model coherent with measured behaviour)"
        )

    if data.get("scorecard_path"):
        lines.append(
            f"Full auto-regenerated scorecard at: {data['scorecard_path']}"
        )

    lines.append(
        "Honest framing: STRONG ratings mean the mechanism is "
        "implemented + closed-loop + Tier-3 protected + tested. "
        "ABSENT-by-declaration ratings (RPT-1, HOT-1, HOT-4, AE-2, "
        "Metzinger transparency) mean the substrate cannot mechanize "
        "them. NO PHENOMENAL CONSCIOUSNESS IS CLAIMED."
    )
    return "\n".join(lines)
