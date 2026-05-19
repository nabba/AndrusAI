"""Annual elegance reflection (Phase 3 of the elegance plan).

Sibling to :mod:`app.identity.annual_reflection`. Where that essay
reflects on values + drift via an LLM call, *this* essay reflects on
the codebase's elegance trajectory using objective metrics only —
no LLM, no phenomenal-language risk.

Source of truth
---------------

The composer reads three artefacts the Phase 1 + Phase 2 loops already
maintain:

  * ``workspace/code_quality/elegance_history.json`` — per-file
    QualityScore samples, weekly cadence.
  * ``workspace/code_quality/architectural_baseline.json`` — current
    SCC + capability-owner + reverse-degree snapshot.
  * ``workspace/proposal_bridge/refactor_proposer/*.json`` — refactor
    proposals filed in the year and their resolution status.

Plus :mod:`app.identity.continuity_ledger` for the rolled-up event
counts (``architectural_debt_drift`` kind covers Phase 1 + 2 events).

Output
------

A deterministic markdown essay at
``wiki/self/elegance_reflections/<year>.md`` with a yaml frontmatter
header and six sections:

  1. ## Composite trajectory — annual avg + min + max
  2. ## Architectural shape — cycle count delta, parallel-cap delta
  3. ## Drift events — counts by source
  4. ## Refactor proposals — counts by status
  5. ## Net file-count change — added / shed
  6. ## Net-zero growth verdict — did the codebase get more elegant?

Cadence
-------

Runs daily via the identity scheduler; cadence-checks via ``_is_due``
against the target file's mtime (350-day default). The first year of
operation produces a baseline essay; subsequent years compare.

Master switch
-------------

``runtime_settings.elegance_reflection_enabled`` (default ON), env
fallback ``ELEGANCE_REFLECTION_ENABLED``.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_REFLECTIONS_DIR = Path("/app/wiki/self/elegance_reflections")
_DEFAULT_MIN_INTERVAL_DAYS = 350


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_elegance_reflection_enabled
        return get_elegance_reflection_enabled()
    except Exception:
        return os.getenv("ELEGANCE_REFLECTION_ENABLED", "true").lower() in (
            "true", "1", "yes", "on",
        )


def _workspace_root() -> Path:
    env = os.environ.get("WORKSPACE_ROOT")
    if env:
        return Path(env)
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


@dataclass(frozen=True)
class EleganceReflectionResult:
    status: str  # "wrote" | "skipped_disabled" | "skipped_recent" | "error"
    year: int = 0
    written_to: str = ""
    failure_reason: str = ""


# ── data gathering ─────────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _composite_trajectory(year: int) -> dict[str, float | int]:
    """Annual stats over the elegance_history samples landing in ``year``."""
    history = _read_json(_workspace_root() / "code_quality" / "elegance_history.json")
    if not history:
        return {"n_samples": 0}
    annual_composites: list[float] = []
    for samples in history.values():
        for sample in samples:
            ts = sample.get("ts", "")
            if isinstance(ts, str) and ts.startswith(f"{year}-"):
                comp = sample.get("composite")
                if isinstance(comp, (int, float)):
                    annual_composites.append(float(comp))
    if not annual_composites:
        return {"n_samples": 0}
    return {
        "n_samples": len(annual_composites),
        "avg": round(statistics.fmean(annual_composites), 3),
        "min": round(min(annual_composites), 3),
        "max": round(max(annual_composites), 3),
        "median": round(statistics.median(annual_composites), 3),
    }


def _architectural_shape() -> dict:
    """Read the current architectural_drift baseline shape."""
    baseline = _read_json(_workspace_root() / "code_quality" / "architectural_baseline.json")
    if not baseline:
        return {"baseline_present": False}
    cycles = baseline.get("cycles") or []
    actionable_cycles = [c for c in cycles if isinstance(c, list) and 2 <= len(c) <= 20]
    systemic = [c for c in cycles if isinstance(c, list) and len(c) > 20]
    owners = baseline.get("capability_owners") or {}
    parallel = {cap: o for cap, o in owners.items() if isinstance(o, list) and len(o) >= 3}
    rev = baseline.get("reverse_degree") or {}
    return {
        "baseline_present": True,
        "n_actionable_cycles": len(actionable_cycles),
        "n_systemic_sccs": len(systemic),
        "largest_systemic_size": max((len(c) for c in systemic), default=0),
        "n_parallel_capabilities": len(parallel),
        "top_centrality": sorted(rev.items(), key=lambda kv: -int(kv[1]))[:5],
    }


def _refactor_proposals_year(year: int) -> dict[str, int]:
    """Count refactor_proposer proposals by status within ``year``."""
    root = _workspace_root() / "proposal_bridge" / "refactor_proposer"
    if not root.exists():
        return {"total": 0}
    counts: dict[str, int] = {"total": 0}
    for meta in root.glob("*.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        staged = data.get("staged_at", "")
        if not (isinstance(staged, str) and staged.startswith(f"{year}-")):
            continue
        status = str(data.get("status", "unknown"))
        counts["total"] += 1
        counts[status] = counts.get(status, 0) + 1
    return counts


def _drift_event_counts(year: int) -> dict[str, int]:
    """Count continuity-ledger ``architectural_debt_drift`` events in ``year``."""
    try:
        from app.identity.continuity_ledger import list_events
    except Exception:
        return {"total": 0}
    counts: dict[str, int] = {"total": 0}
    try:
        events = list_events(
            since_iso=f"{year}-01-01T00:00:00+00:00",
            kinds={"architectural_debt_drift"},
        )
        for ev in events:
            if not ev.ts.startswith(f"{year}-"):
                continue
            counts["total"] += 1
            counts[ev.actor] = counts.get(ev.actor, 0) + 1
    except Exception:
        logger.debug("elegance_reflection: ledger read failed", exc_info=True)
    return counts


def _net_file_change(year: int) -> dict[str, int]:
    """Rough net-file-change for the year from the system_inventory snapshot.

    Snapshot captures only one moment in time; we use it as the
    *current* baseline and estimate year-over-year delta from the
    annual-reflection-style heuristic: file mtime within the year =
    "touched this year". Files mtime older than year start = "stable
    this year".
    """
    inv = _read_json(_workspace_root() / "system_inventory" / "snapshot.json")
    if not inv:
        return {"snapshot_present": False}
    modules = inv.get("modules") or []
    year_start_iso = f"{year}-01-01T00:00:00+00:00"
    # The snapshot doesn't store per-file mtimes — only paths + symbols.
    # For year-over-year deltas we rely on continuity-ledger drift events.
    # The snapshot still gives the CURRENT shape for context.
    return {
        "snapshot_present": True,
        "current_n_modules": len(modules),
        "current_n_packages": int(inv.get("n_packages", 0)),
        "current_total_loc": int(inv.get("total_loc", 0)),
    }


# ── essay rendering ────────────────────────────────────────────────────


def _verdict(
    composite: dict, shape: dict, proposals: dict, drift: dict,
) -> str:
    """Heuristic net-zero verdict the operator can read in one glance.

    Three signals must all hold for ``shedding`` verdict:
      * avg composite ≥ 0.90 (codebase is healthy)
      * proposals.applied ≥ proposals.rejected (operator IS acting)
      * shape.n_actionable_cycles ≤ 5 (small-cycle backlog is bounded)

    ``stable`` when avg composite ≥ 0.85 AND drift.total > 0 (loop fired).
    ``growing`` otherwise — the loop hasn't gained traction yet.
    """
    avg = float(composite.get("avg", 0.0) or 0.0)
    applied = int(proposals.get("applied", 0))
    rejected = int(proposals.get("rejected", 0))
    cycles = int(shape.get("n_actionable_cycles", 0))
    drift_total = int(drift.get("total", 0))
    if avg >= 0.90 and applied >= rejected and cycles <= 5:
        return "shedding"
    if avg >= 0.85 and drift_total > 0:
        return "stable"
    return "growing"


def _render_essay(
    year: int,
    composite: dict,
    shape: dict,
    proposals: dict,
    drift: dict,
    net: dict,
) -> str:
    verdict = _verdict(composite, shape, proposals, drift)
    composed_at = datetime.now(timezone.utc).isoformat()
    n_samples = int(composite.get("n_samples", 0))
    n_drift = int(drift.get("total", 0))
    n_props = int(proposals.get("total", 0))

    lines: list[str] = [
        f"---",
        f"year: {year}",
        f"composed_at: {composed_at}",
        f"verdict: {verdict}",
        f"composite_samples: {n_samples}",
        f"drift_events: {n_drift}",
        f"refactor_proposals: {n_props}",
        f"---",
        "",
        f"# Code elegance reflection — {year}",
        "",
        f"**Trajectory verdict:** `{verdict}`. ",
        f"This essay is composed deterministically from the Phase 1 + 2 ",
        f"artefacts; no LLM is involved. Read it as a one-page operator ",
        f"dashboard for whether the codebase is shedding, stable, or ",
        f"growing in complexity over the year.",
        "",
        "## Composite trajectory",
        "",
    ]
    if n_samples == 0:
        lines.append("_No composite samples persisted in this year._")
    else:
        lines.append(f"- Samples in window: {n_samples}")
        lines.append(f"- Mean composite: **{composite['avg']:.3f}**")
        lines.append(f"- Median composite: {composite['median']:.3f}")
        lines.append(f"- Range: {composite['min']:.3f} – {composite['max']:.3f}")

    lines.extend(["", "## Architectural shape (current snapshot)", ""])
    if not shape.get("baseline_present"):
        lines.append("_No architectural_baseline.json persisted yet._")
    else:
        lines.append(f"- Actionable cycles (≤20 members): **{shape['n_actionable_cycles']}**")
        lines.append(f"- Systemic SCCs (>20 members): {shape['n_systemic_sccs']}")
        lines.append(f"- Largest systemic SCC: {shape['largest_systemic_size']} files")
        lines.append(f"- Parallel-capability clusters (≥3 owners): {shape['n_parallel_capabilities']}")
        if shape.get("top_centrality"):
            lines.append("- Top centrality (importers):")
            for path, n in shape["top_centrality"]:
                lines.append(f"  - `{path}`: {n}")

    lines.extend(["", "## Drift events recorded", ""])
    lines.append(f"- Total `architectural_debt_drift` events in the year: **{drift.get('total', 0)}**")
    other = {k: v for k, v in drift.items() if k != "total"}
    if other:
        for actor, n in sorted(other.items(), key=lambda kv: -int(kv[1])):
            lines.append(f"  - by `{actor}`: {n}")

    lines.extend(["", "## Refactor proposals filed", ""])
    if n_props == 0:
        lines.append("_No refactor proposals filed this year (Phase 2 producer disabled or signal-empty)._")
    else:
        lines.append(f"- Total filed: **{n_props}**")
        for status in ("staged", "cr_filed", "applied", "rejected", "expired"):
            v = int(proposals.get(status, 0))
            if v:
                lines.append(f"  - {status}: {v}")

    lines.extend(["", "## Codebase shape (current snapshot)", ""])
    if not net.get("snapshot_present"):
        lines.append("_No system_inventory snapshot persisted yet._")
    else:
        lines.append(f"- Modules: {net['current_n_modules']:,}")
        lines.append(f"- Packages: {net['current_n_packages']:,}")
        lines.append(f"- Total non-blank LOC: {net['current_total_loc']:,}")

    lines.extend([
        "",
        "## Net-zero growth verdict",
        "",
        f"**`{verdict}`** — defined as:",
        "",
        "- `shedding`: avg composite ≥ 0.90, applied ≥ rejected, ≤5 actionable cycles",
        "- `stable`: avg composite ≥ 0.85 with measurable drift activity",
        "- `growing`: neither — refactor loop hasn't gained traction yet",
        "",
        "_The elegance plan's stated goal is to reach `shedding` and stay ",
        "there — the codebase doing more while having FEWER files than ",
        "today, achieved through consolidation rather than just addition._",
        "",
    ])
    return "\n".join(lines)


# ── orchestration ──────────────────────────────────────────────────────


def _is_due(reflections_dir: Path, year: int, min_interval_days: int) -> bool:
    target = reflections_dir / f"{year}.md"
    if not target.exists():
        return True
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return True
    age_days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400.0
    return age_days >= min_interval_days


def _emit_landmark(year: int, verdict: str, n_props: int, n_drift: int) -> None:
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="code_consolidation",
            actor="elegance_reflection",
            summary=f"annual elegance reflection {year} verdict={verdict}",
            detail={
                "year": year,
                "verdict": verdict,
                "proposals_filed": n_props,
                "drift_events": n_drift,
            },
        )
    except Exception:
        logger.debug("elegance_reflection: landmark emit failed", exc_info=True)


def run_one_pass(
    *,
    year: int | None = None,
    reflections_dir: Path | str | None = None,
    min_interval_days: int = _DEFAULT_MIN_INTERVAL_DAYS,
    now: datetime | None = None,
) -> EleganceReflectionResult:
    """Compose and write one annual elegance reflection. Never raises."""
    if not _enabled():
        return EleganceReflectionResult(status="skipped_disabled")

    out_dir = Path(reflections_dir) if reflections_dir else _DEFAULT_REFLECTIONS_DIR
    cur = now or datetime.now(timezone.utc)
    target_year = year if year is not None else cur.year

    if not _is_due(out_dir, target_year, min_interval_days):
        return EleganceReflectionResult(
            status="skipped_recent",
            year=target_year,
            written_to=str(out_dir / f"{target_year}.md"),
        )

    try:
        composite = _composite_trajectory(target_year)
        shape = _architectural_shape()
        proposals = _refactor_proposals_year(target_year)
        drift = _drift_event_counts(target_year)
        net = _net_file_change(target_year)
        body = _render_essay(target_year, composite, shape, proposals, drift, net)

        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{target_year}.md"
        target.write_text(body, encoding="utf-8")
        _emit_landmark(
            target_year,
            _verdict(composite, shape, proposals, drift),
            int(proposals.get("total", 0)),
            int(drift.get("total", 0)),
        )
        return EleganceReflectionResult(
            status="wrote",
            year=target_year,
            written_to=str(target),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("elegance_reflection: pass failed", exc_info=True)
        return EleganceReflectionResult(
            status="error",
            year=target_year,
            failure_reason=str(exc),
        )
