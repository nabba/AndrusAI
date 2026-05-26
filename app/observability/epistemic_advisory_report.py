"""Advisory-mode observation report for the epistemic gate.

The epistemic gate (``app.epistemic.orchestrator_hook.gate_output``)
ships behind two switches: ``EPISTEMIC_ENABLED`` (master) and
``EPISTEMIC_BLOCKING_MODE`` (enforce vs observe). The intended promotion
discipline mirrors Goodhart's:

  1. Stage A: plant the structural producer (Postgres ledger fills)
  2. Stage B: ``EPISTEMIC_ENABLED=true``, blocking off → observe only
  3. Soak ≥30 days, run THIS report
  4. If the would-have-blocked rate looks sane → promote to enforcing
     via React ``/cp/settings`` Epistemic card

This module is the lens for step 3. It walks the verdict telemetry
JSONL (``workspace/epistemic/gate_verdicts.jsonl``, written by
``app/epistemic/verdict_telemetry.py``) and surfaces:

  * total evaluations in window
  * counts by action: ship / revise / block
  * **would-have-blocked rate** — what % of replies the gate would have
    interfered with if blocking mode were on
  * per-zone distribution (chat / autonomous / financial)
  * top 5 user_visible_reasons (the "why" of blocks/revises)
  * top 10 bias matches across all CalibrationVerdict rows
  * effective-mode label for the current configuration

Operator workflow:

  1. Run after ≥30 days in Stage B:
     ``python -m app.observability.epistemic_advisory_report``
     ``aai advisory epistemic --window-days 30``  (CLI alias)
  2. If would-have-blocked rate is non-zero AND the reasons look like
     real epistemic faults → flip to enforcing.
  3. If would-have-blocked rate is non-zero but reasons look like noise
     (e.g. the producer is mislabelling claims) → tune producer /
     detector thresholds BEFORE flipping.
  4. If would-have-blocked rate is zero AND ledger is meaningfully sized
     (>10k claims) → either flip as cheap insurance OR leave in advisory.

Invocation:
  * ``python -m app.observability.epistemic_advisory_report``
    — prints a human-readable report to stdout.
  * ``app.observability.epistemic_advisory_report.report(window_days=30)``
    — programmatic dict for the React dashboard.
  * ``--json`` flag emits machine-readable JSON.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from typing import Any

from app.epistemic.verdict_telemetry import read_rows_since

logger = logging.getLogger(__name__)


def _current_mode() -> dict[str, Any]:
    """Resolve the effective configuration (env + runtime overrides)."""
    try:
        from app.epistemic import is_enabled
        from app.epistemic.orchestrator_hook import is_blocking_mode_enabled
        enabled = bool(is_enabled())
        blocking = bool(is_blocking_mode_enabled())
    except Exception:
        return {"phase": "unknown", "enabled": False, "blocking": False}
    if not enabled:
        phase = "disabled"
    elif blocking:
        phase = "enforcing"
    else:
        phase = "advisory"
    return {"phase": phase, "enabled": enabled, "blocking": blocking}


def _verification_extension_state() -> dict[str, Any]:
    """Stage D switches — surfaced so the report explains thresholds."""
    try:
        from app.runtime_settings import (
            get_verification_extension_enabled,
            get_verification_threshold,
        )
        return {
            "verification_extension_enabled":
                bool(get_verification_extension_enabled()),
            "thresholds": {
                z: get_verification_threshold(z)
                for z in ("chat", "autonomous", "financial")
            },
        }
    except Exception:
        return {
            "verification_extension_enabled": False,
            "thresholds": {},
        }


def _producer_state() -> dict[str, Any]:
    """Stage A producer status + ledger row count (best-effort)."""
    try:
        from app.runtime_settings import (
            get_epistemic_retrieval_producer_enabled,
        )
        enabled = bool(get_epistemic_retrieval_producer_enabled())
    except Exception:
        enabled = False
    row_count: int | None = None
    try:
        from app.db_pool import execute
        rows = execute(
            "SELECT COUNT(*) FROM control_plane.epistemic_claims "
            "WHERE created_at > NOW() - INTERVAL '30 days'",
            fetch=True,
        )
        if rows:
            row_count = int(rows[0][0])
    except Exception:
        pass
    return {
        "epistemic_retrieval_producer_enabled": enabled,
        "claims_last_30d": row_count,
    }


def report(window_days: int = 30) -> dict[str, Any]:
    """Aggregate verdict telemetry over a trailing window.

    Returns a JSON-serialisable dict suitable for both the CLI renderer
    and a React fetch. Never raises — empty windows return zeros."""
    since_ts = time.time() - max(1, int(window_days)) * 86400.0
    rows = read_rows_since(since_ts)

    actions = Counter(r.get("action") for r in rows)
    zones = Counter(r.get("zone") or "unknown" for r in rows)

    # Would-have-blocked = the count of revise+block actions REGARDLESS of
    # whether blocking_mode was on. Advisory mode emits the action label
    # the gate would have taken — that's what we want to characterise.
    would_have_blocked = actions.get("block", 0) + actions.get("revise", 0)
    total = len(rows)
    rate = (would_have_blocked / total) if total else 0.0

    # Top user_visible_reasons across revise+block.
    reasons = Counter()
    for r in rows:
        if r.get("action") in ("revise", "block"):
            why = (r.get("user_visible_reason") or "").strip()
            if why:
                reasons[why] += 1

    # Top biases across all CalibrationVerdict rows.
    biases = Counter()
    for r in rows:
        verdict = r.get("verdict") or {}
        for bias in verdict.get("biases_detected") or []:
            bid = bias.get("bias_id") if isinstance(bias, dict) else None
            if bid:
                biases[bid] += 1

    # Ledger size distribution — tells the operator whether the producer
    # is feeding the gate well or starving it.
    ledger_sizes = [r.get("ledger_size") for r in rows
                    if isinstance(r.get("ledger_size"), int)]
    ledger_pct = {}
    if ledger_sizes:
        ledger_sizes.sort()
        n = len(ledger_sizes)
        ledger_pct = {
            "min": ledger_sizes[0],
            "p50": ledger_sizes[n // 2],
            "p95": ledger_sizes[min(n - 1, int(n * 0.95))],
            "max": ledger_sizes[-1],
        }

    return {
        "window_days": window_days,
        "total_verdicts": total,
        "by_action": dict(actions),
        "by_zone": dict(zones),
        "would_have_blocked": would_have_blocked,
        "would_have_blocked_rate": rate,
        "top_reasons": reasons.most_common(5),
        "top_biases": biases.most_common(10),
        "ledger_size_pct": ledger_pct,
        "mode": _current_mode(),
        "extension": _verification_extension_state(),
        "producer": _producer_state(),
        "as_of": time.time(),
    }


def _render(data: dict[str, Any]) -> str:
    """Human-readable formatting. Stable shape for terminal + Signal."""
    lines = []
    mode = data["mode"]
    lines.append(
        f"Epistemic gate — {mode['phase'].upper()} (enabled={mode['enabled']}, "
        f"blocking={mode['blocking']})"
    )
    lines.append("─" * 64)
    lines.append(
        f"window: {data['window_days']} days · "
        f"total verdicts: {data['total_verdicts']} · "
        f"would-have-blocked: {data['would_have_blocked']} "
        f"({data['would_have_blocked_rate']:.1%})"
    )
    lines.append("")
    lines.append("By action:")
    for action, n in sorted(data["by_action"].items(),
                            key=lambda x: -x[1]):
        lines.append(f"  {action or '(none)':10s} {n:>6d}")
    lines.append("")
    lines.append("By zone:")
    for zone, n in sorted(data["by_zone"].items(),
                          key=lambda x: -x[1]):
        lines.append(f"  {zone:10s} {n:>6d}")
    lines.append("")
    if data["top_reasons"]:
        lines.append("Top reasons (revise+block):")
        for reason, n in data["top_reasons"]:
            lines.append(f"  {n:>4d}× {reason[:80]}")
        lines.append("")
    if data["top_biases"]:
        lines.append("Top biases detected:")
        for bias_id, n in data["top_biases"]:
            lines.append(f"  {n:>4d}× {bias_id}")
        lines.append("")
    if data["ledger_size_pct"]:
        p = data["ledger_size_pct"]
        lines.append(
            f"Ledger size per task: min={p['min']} p50={p['p50']} "
            f"p95={p['p95']} max={p['max']}"
        )
        lines.append("")
    producer = data["producer"]
    lines.append(
        f"Producer (Stage A): "
        f"enabled={producer['epistemic_retrieval_producer_enabled']} · "
        f"claims_last_30d={producer['claims_last_30d']}"
    )
    ext = data["extension"]
    lines.append(
        f"Verification extension (Stage D): enabled="
        f"{ext['verification_extension_enabled']} · thresholds={ext['thresholds']}"
    )
    lines.append("")
    lines.append("Operator decision (Stage B → C):")
    rate = data["would_have_blocked_rate"]
    total = data["total_verdicts"]
    if total < 100:
        lines.append("  ↳ Sample too small. Continue soaking; revisit at ≥100 verdicts.")
    elif rate == 0.0:
        lines.append("  ↳ No revise/block proposals — gate is silent. Either")
        lines.append("    flip to enforcing as cheap insurance OR investigate")
        lines.append("    whether the producer is generating actionable claims.")
    elif rate > 0.10:
        lines.append("  ↳ Would-have-blocked rate is high (>10%). Inspect Top reasons —")
        lines.append("    if they look like real epistemic faults, promote to enforcing.")
        lines.append("    If they look like producer noise, tune before promoting.")
    else:
        lines.append("  ↳ Would-have-blocked rate is in the bite-but-not-spammy range.")
        lines.append("    Inspect top reasons; if defensible, promote to enforcing.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Epistemic gate advisory report (Stage B observability).",
    )
    ap.add_argument("--window-days", type=int, default=30,
                    help="Days of verdict telemetry to aggregate (default 30).")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of text.")
    args = ap.parse_args()
    data = report(window_days=args.window_days)
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(_render(data) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
