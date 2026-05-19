"""Quarterly code-consolidation digest (Phase 3 of the elegance plan).

Sibling to :mod:`app.self_improvement.meta_agent.consolidation` — that
module consolidates *recipes* on a weekly/quarterly cadence; this module
consolidates *code* on a strictly quarterly cadence. Both ask the same
shape of question: "what should we shed before adding more?"

What gets digested
------------------

The pass reads three deterministic sources:

  * The current ``system_inventory`` snapshot — module shape (path,
    LOC, public_symbols, has_tests).
  * The current ``architectural_baseline`` — capability owners +
    reverse-degree graph.
  * The current ``elegance_history`` — per-file QualityScore history.

…and produces a tight markdown digest at
``wiki/self/code_consolidation/<year>_q<n>.md`` enumerating:

  1. **Shed candidates** — modules with ALL of:
       - `loc < 200`
       - `reverse_degree ≤ 1` (zero or one importer)
       - `has_tests = False`
       - matches no `_HUB_PATTERNS` exclusion (foundational paths)
     These are *suggestions* the operator can review. The digest never
     proposes deletion; it surfaces candidates for the operator to
     triage by hand or feed to ``refactor_proposer`` for a CR.

  2. **Parallel-capability clusters** — capabilities with ≥3 owners,
     i.e. the same ones ``refactor_proposer`` proposes against. Listed
     here for situational awareness even when the proposer is OFF.

  3. **Stable cycles** — small SCCs from the architectural_drift
     baseline. They've persisted across the quarter; nobody has
     refactored them yet.

Why a digest, not a CR
----------------------

The quarterly digest is *informational*, like the annual essay. CRs
for code changes flow through ``refactor_proposer`` (Phase 2) — the
digest helps the operator decide which of those CRs to prioritise.

Output and emission
-------------------

* Markdown at ``wiki/self/code_consolidation/<year>_q<n>.md``.
* Continuity-ledger event ``code_consolidation`` so the annual essay's
  ``summarise_drift`` Counter sees the quarter.

Cadence
-------

Internal cadence-gate of 85 days against the target file's mtime.
Runs as a LIGHT idle job (one tick costs ~50 ms when not due).

Master switch
-------------

``runtime_settings.code_consolidation_enabled`` (default ON), env
fallback ``CODE_CONSOLIDATION_ENABLED``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_DIGESTS_DIR = Path("/app/wiki/self/code_consolidation")
_DEFAULT_MIN_INTERVAL_DAYS = 85
_LOC_THRESHOLD = 200
_MAX_DEPENDENTS_FOR_SHED = 1
_MAX_SHED_CANDIDATES = 20
_MAX_CYCLES_LISTED = 10
_MAX_PARALLELS_LISTED = 10

# Modules under these path prefixes are foundational hubs — even if
# they look "shedable" by the heuristic, they almost certainly aren't.
_HUB_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^app/__init__\.py$"),
    re.compile(r"^app/config\.py$"),
    re.compile(r"^app/paths\.py$"),
    re.compile(r"^app/main\.py$"),
    re.compile(r"^app/runtime_settings\.py$"),
    # ``__init__.py`` files are usually package shells. If they have
    # no importers it's normally because Python finds them implicitly.
    re.compile(r"^app/[^/]+/__init__\.py$"),
)


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_code_consolidation_enabled
        return get_code_consolidation_enabled()
    except Exception:
        return os.getenv("CODE_CONSOLIDATION_ENABLED", "true").lower() in (
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
class ConsolidationResult:
    status: str  # "wrote" | "skipped_disabled" | "skipped_recent" | "error"
    year: int = 0
    quarter: int = 0
    written_to: str = ""
    n_shed_candidates: int = 0
    n_parallel_clusters: int = 0
    n_cycles: int = 0
    failure_reason: str = ""


# ── helpers ────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _quarter_for(now: datetime) -> tuple[int, int]:
    """Return (year, quarter) for ``now`` — Q1 is Jan-Mar."""
    return now.year, (now.month - 1) // 3 + 1


def _is_excluded_path(path: str) -> bool:
    return any(pat.match(path) for pat in _HUB_PATTERNS)


# ── detectors ──────────────────────────────────────────────────────────


def _detect_shed_candidates(
    inventory: dict | None, baseline: dict | None,
) -> list[dict[str, Any]]:
    """Modules that look like they could be removed.

    Heuristic — every criterion must hold:
      * LOC < 200 (small enough that nothing important is "hidden" inside)
      * reverse_degree ≤ 1 (zero or one importer)
      * has_tests is False (no test file with the module's stem)
      * path doesn't match a hub-exclusion pattern
    """
    if not inventory or not baseline:
        return []
    rev = baseline.get("reverse_degree") or {}
    modules = inventory.get("modules") or []
    out: list[dict[str, Any]] = []
    for m in modules:
        path = str(m.get("path", ""))
        if _is_excluded_path(path):
            continue
        if m.get("kind") == "package":  # __init__.py — keep
            continue
        if int(m.get("loc", 0)) >= _LOC_THRESHOLD:
            continue
        if int(rev.get(path, 0)) > _MAX_DEPENDENTS_FOR_SHED:
            continue
        if m.get("has_tests"):
            # Tested modules are interesting too, but a strong shed
            # signal really wants "no tests + no importers" — the
            # tested variant is a softer "review this" rather than
            # "consider deletion".
            continue
        out.append({
            "path": path,
            "loc": int(m.get("loc", 0)),
            "dependents": int(rev.get(path, 0)),
            "public_symbols": list(m.get("public_symbols") or [])[:3],
            "summary": str(m.get("summary", ""))[:120],
        })
    # Stable order: smallest LOC first — easiest to evaluate.
    out.sort(key=lambda c: (c["loc"], c["path"]))
    return out[:_MAX_SHED_CANDIDATES]


def _detect_parallel_clusters(baseline: dict | None) -> list[dict[str, Any]]:
    if not baseline:
        return []
    owners = baseline.get("capability_owners") or {}
    out: list[dict[str, Any]] = []
    for cap, files in owners.items():
        if not isinstance(files, list) or len(files) < 3:
            continue
        out.append({"capability": cap, "owners": sorted(files)})
    out.sort(key=lambda d: (-len(d["owners"]), d["capability"]))
    return out[:_MAX_PARALLELS_LISTED]


def _detect_stable_cycles(baseline: dict | None) -> list[list[str]]:
    if not baseline:
        return []
    cycles = baseline.get("cycles") or []
    actionable = [c for c in cycles if isinstance(c, list) and 2 <= len(c) <= 20]
    actionable.sort(key=lambda c: (len(c), c[0] if c else ""))
    return actionable[:_MAX_CYCLES_LISTED]


# ── rendering ──────────────────────────────────────────────────────────


def _render_digest(
    year: int,
    quarter: int,
    shed: list[dict[str, Any]],
    parallels: list[dict[str, Any]],
    cycles: list[list[str]],
    inventory: dict | None,
) -> str:
    composed_at = datetime.now(timezone.utc).isoformat()
    n_modules = int((inventory or {}).get("n_modules", 0))
    n_packages = int((inventory or {}).get("n_packages", 0))
    total_loc = int((inventory or {}).get("total_loc", 0))

    lines: list[str] = [
        "---",
        f"year: {year}",
        f"quarter: {quarter}",
        f"composed_at: {composed_at}",
        f"current_n_modules: {n_modules}",
        f"current_total_loc: {total_loc}",
        f"shed_candidates: {len(shed)}",
        f"parallel_clusters: {len(parallels)}",
        f"actionable_cycles: {len(cycles)}",
        "---",
        "",
        f"# Code-consolidation digest — {year} Q{quarter}",
        "",
        "Quarterly snapshot composed deterministically from the system "
        "inventory + architectural baseline. The digest is **informational** —"
        " it never proposes a code change. CRs for actionable items flow "
        "through `refactor_proposer` (Phase 2 elegance loop). Use this "
        "digest to decide which of those CRs to prioritise, or to file "
        "shed candidates by hand when the proposer is OFF.",
        "",
        "## Current codebase shape",
        "",
        f"- Modules: **{n_modules:,}** (packages: {n_packages:,})",
        f"- Total non-blank LOC: **{total_loc:,}**",
        "",
        "## Shed candidates",
        "",
        f"Heuristic: ``LOC < {_LOC_THRESHOLD}`` AND ``≤ {_MAX_DEPENDENTS_FOR_SHED} importer(s)`` "
        f"AND no sibling test file AND not in the foundational-hub allowlist.",
        "",
    ]
    if not shed:
        lines.append("_No candidates this quarter — the codebase is staying lean._")
    else:
        for c in shed:
            symbols = ", ".join(c["public_symbols"]) or "_(no public symbols)_"
            lines.append(
                f"- `{c['path']}` — {c['loc']} LOC, "
                f"{c['dependents']} importer(s), exports: {symbols}"
            )
            if c["summary"]:
                lines.append(f"    > {c['summary']}")

    lines.extend(["", "## Parallel-capability clusters", ""])
    if not parallels:
        lines.append("_No clusters this quarter (≥3 owners per capability)._")
    else:
        for p in parallels:
            owners = "\n".join(f"  - `{o}`" for o in p["owners"])
            lines.append(f"- **`{p['capability']}`** — {len(p['owners'])} owners:")
            lines.append(owners)

    lines.extend(["", "## Persisting small cycles", ""])
    if not cycles:
        lines.append("_No actionable cycles in baseline._")
    else:
        for c in cycles:
            lines.append(f"- ({len(c)}) " + " → ".join(f"`{m}`" for m in c))

    lines.extend([
        "",
        "## Actions the operator can take",
        "",
        "1. For each shed candidate: confirm it's truly unused (search "
        "ChromaDB usage, recent operator activity), then delete or file "
        "a refactor CR manually.",
        "2. For each parallel cluster: decide whether to consolidate, "
        "rename to disambiguate, or document as an intentional meta-tag.",
        "3. For each cycle: pick the smallest one to break first — the "
        "smaller the SCC, the more focused the refactor.",
        "",
    ])
    return "\n".join(lines)


# ── orchestration ──────────────────────────────────────────────────────


def _is_due(
    digests_dir: Path, year: int, quarter: int, min_interval_days: int,
) -> bool:
    target = digests_dir / f"{year}_q{quarter}.md"
    if not target.exists():
        return True
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return True
    age_days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400.0
    return age_days >= min_interval_days


def _emit_landmark(
    year: int, quarter: int,
    n_shed: int, n_parallels: int, n_cycles: int,
) -> None:
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="code_consolidation",
            actor="code_consolidation_quarterly",
            summary=f"quarterly digest {year}Q{quarter}",
            detail={
                "year": year,
                "quarter": quarter,
                "n_shed_candidates": n_shed,
                "n_parallel_clusters": n_parallels,
                "n_actionable_cycles": n_cycles,
            },
        )
    except Exception:
        logger.debug("code_consolidation: landmark emit failed", exc_info=True)


def run_one_pass(
    *,
    now: datetime | None = None,
    digests_dir: Path | str | None = None,
    min_interval_days: int = _DEFAULT_MIN_INTERVAL_DAYS,
) -> ConsolidationResult:
    """Compose and write one quarterly digest. Never raises."""
    if not _enabled():
        return ConsolidationResult(status="skipped_disabled")

    cur = now or datetime.now(timezone.utc)
    year, quarter = _quarter_for(cur)
    out_dir = Path(digests_dir) if digests_dir else _DEFAULT_DIGESTS_DIR

    if not _is_due(out_dir, year, quarter, min_interval_days):
        return ConsolidationResult(
            status="skipped_recent",
            year=year,
            quarter=quarter,
            written_to=str(out_dir / f"{year}_q{quarter}.md"),
        )

    try:
        inv = _read_json(_workspace_root() / "system_inventory" / "snapshot.json")
        baseline = _read_json(_workspace_root() / "code_quality" / "architectural_baseline.json")
        shed = _detect_shed_candidates(inv, baseline)
        parallels = _detect_parallel_clusters(baseline)
        cycles = _detect_stable_cycles(baseline)
        body = _render_digest(year, quarter, shed, parallels, cycles, inv)

        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{year}_q{quarter}.md"
        target.write_text(body, encoding="utf-8")
        _emit_landmark(year, quarter, len(shed), len(parallels), len(cycles))
        return ConsolidationResult(
            status="wrote",
            year=year,
            quarter=quarter,
            written_to=str(target),
            n_shed_candidates=len(shed),
            n_parallel_clusters=len(parallels),
            n_cycles=len(cycles),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("code_consolidation: pass failed", exc_info=True)
        return ConsolidationResult(
            status="error",
            year=year,
            quarter=quarter,
            failure_reason=str(exc),
        )
