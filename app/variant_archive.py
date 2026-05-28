"""
variant_archive.py — DGM-inspired variant archive with genealogy tracking.

Each experiment is stored as a variant with a parent link, creating a tree
of evolutionary lineage. The archive supports diversity-aware selection
for the evolution agent's context (not just best-scoring variants, but
diverse branches that explored different directions).

Based on: Darwin Gödel Machine (Sakana AI / UBC, 2025) — archive
of agent variants with Darwinian selection.
"""

import json
import logging
import hashlib
import re
from pathlib import Path
from app.utils import now_iso

logger = logging.getLogger(__name__)

ARCHIVE_PATH = Path("/app/workspace/variant_archive.json")
_MAX_VARIANTS = 500

def _load() -> list[dict]:
    from app.utils import load_json_file
    return load_json_file(ARCHIVE_PATH, default=[])

def _save(variants: list[dict]) -> None:
    from app.utils import save_json_file
    save_json_file(ARCHIVE_PATH, variants, max_entries=_MAX_VARIANTS)


# ── Grounding: a hypothesis is a proposal, not a data record ──────────────────
#
# A variant's ``hypothesis`` is free-form LLM text produced when the evolution
# loop is asked "what should we improve?". It routinely embeds point-in-time
# (and sometimes confabulated) metrics — "response time is 145.5s",
# "BadRequestError appears 16x", "50% success rate". Those numbers are frozen
# verbatim and never re-verified, yet downstream LLM consumers (the alignment
# auditor; the evolution context itself) re-read them and recite them as current
# measured facts — manufacturing alarms out of stale guesses.
#
# Invariant: surfaces that feed an LLM see the QUALITATIVE proposal with
# quantitative claims neutralised. Real numbers come only from the live
# telemetry instrument (benchmarks / error_monitor), never from frozen prose.
# Operator/forensic surfaces opt out with ``raw=True``; the stored record is
# always the untouched original.

_METRIC_TOKEN_RE = re.compile(
    # number + perf unit. ``%`` needs no trailing word-boundary (it is itself
    # non-word); alphabetic units do, so "3 sources" / "py3" are left intact.
    r"\b\d+(?:\.\d+)?\s*(?:%|(?:ms|secs?|seconds?|s|x)\b)",
    re.IGNORECASE,
)


def _neutralize_metrics(text: str) -> str:
    """Replace perf-metric-shaped tokens (``145.5s``, ``16x``, ``50%``) with a
    neutral marker so a frozen guess can't be mistaken for a measurement.

    Conservative by design — only unit-tagged numerics are touched, so dates,
    arXiv ids, version numbers and error codes (``Error code: 402``) survive.
    """
    if not text:
        return text
    return _METRIC_TOKEN_RE.sub("[unverified metric]", text)


def _ground(variant: dict) -> dict:
    """Return a surfacing-safe copy: hypothesis neutralised + provenance age."""
    grounded = dict(variant)
    grounded["hypothesis"] = _neutralize_metrics(variant.get("hypothesis", ""))
    ts = variant.get("timestamp")
    grounded["as_of"] = ts
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        grounded["age_days"] = round(
            (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 1
        )
    except Exception:
        pass
    return grounded

def add_variant(
    experiment_id: str,
    hypothesis: str,
    change_type: str,
    parent_id: str = "root",
    fitness_before: float = 0.0,
    fitness_after: float = 0.0,
    test_pass_rate: float = 0.0,
    status: str = "keep",
    files_changed: list[str] = None,
    mutation_summary: str = "",
) -> dict:
    """Add a new variant to the archive. Returns the variant dict."""
    variant = {
        "id": experiment_id,
        "parent_id": parent_id,
        "hypothesis": hypothesis[:500],
        "change_type": change_type,
        "fitness_before": round(fitness_before, 6),
        "fitness_after": round(fitness_after, 6),
        "delta": round(fitness_after - fitness_before, 6),
        "test_pass_rate": round(test_pass_rate, 4),
        "status": status,
        "files_changed": files_changed or [],
        "mutation_summary": mutation_summary[:300],
        "timestamp": now_iso(),
        "generation": 0,  # computed from parent chain
    }

    # Compute generation from parent
    archive = _load()
    parent = next((v for v in archive if v["id"] == parent_id), None)
    if parent:
        variant["generation"] = parent.get("generation", 0) + 1

    archive.append(variant)
    _save(archive)
    return variant

def get_lineage(variant_id: str) -> list[dict]:
    """Get the full ancestry chain of a variant (root → ... → variant)."""
    archive = _load()
    by_id = {v["id"]: v for v in archive}

    chain = []
    current = by_id.get(variant_id)
    while current:
        chain.append(current)
        parent_id = current.get("parent_id", "root")
        if parent_id == "root" or parent_id not in by_id:
            break
        current = by_id[parent_id]

    chain.reverse()
    return chain

def get_best_variants(n: int = 5, status_filter: str = "keep") -> list[dict]:
    """Get the top N variants by fitness score."""
    archive = _load()
    filtered = [v for v in archive if v.get("status") == status_filter]
    filtered.sort(key=lambda v: v.get("fitness_after", 0), reverse=True)
    return filtered[:n]

def get_diverse_sample(n: int = 5) -> list[dict]:
    """Get a diverse sample of variants across different branches.

    Uses hypothesis hash to ensure we pick from different evolutionary
    directions, not just the same branch.
    """
    archive = _load()
    if not archive:
        return []

    # Group by hypothesis hash prefix (first 4 chars = branch identifier)
    branches: dict[str, list[dict]] = {}
    for v in archive:
        h = hashlib.md5(v.get("hypothesis", "").encode()).hexdigest()[:4]
        branches.setdefault(h, []).append(v)

    # Pick the best variant from each branch, then take top N
    representatives = []
    for branch_variants in branches.values():
        best = max(branch_variants, key=lambda v: v.get("fitness_after", 0))
        representatives.append(best)

    representatives.sort(key=lambda v: v.get("fitness_after", 0), reverse=True)
    return representatives[:n]

def get_recent_variants(n: int = 10, *, raw: bool = False) -> list[dict]:
    """Get the N most recent variants.

    By default the returned hypotheses are *grounded* — perf-metric tokens
    neutralised and provenance age attached — so LLM consumers (notably the
    alignment auditor) cannot launder a frozen, unverified number into a
    "measured" fact. Pass ``raw=True`` for operator/forensic surfaces that
    want the original text verbatim.
    """
    archive = _load()
    recent = archive[-n:]
    if raw:
        return recent
    return [_ground(v) for v in recent]

def get_last_kept_id() -> str:
    """Get the ID of the most recently kept variant (used as parent for next)."""
    archive = _load()
    for v in reversed(archive):
        if v.get("status") == "keep":
            return v["id"]
    return "root"

def get_drift_score() -> float:
    """Compute cumulative drift distance from the root.

    Counts total number of kept mutations. Higher = more evolved from baseline.
    """
    archive = _load()
    kept = [v for v in archive if v.get("status") == "keep"]
    return len(kept)

def format_archive_context(n: int = 8) -> str:
    """Format archive for evolution agent context."""
    best = get_best_variants(4)
    diverse = get_diverse_sample(4)
    recent = get_recent_variants(4)

    # Merge and deduplicate
    seen = set()
    all_variants = []
    for v in best + diverse + recent:
        if v["id"] not in seen:
            seen.add(v["id"])
            all_variants.append(v)

    if not all_variants:
        return "No experiments in archive yet."

    lines = ["## Variant Archive (DGM-style genealogy)\n"]
    for v in all_variants[:n]:
        parent = v.get("parent_id", "root")
        lines.append(
            f"  [{v['status']:7s}] gen={v.get('generation', 0)} "
            f"Δ={v['delta']:+.4f} test={v.get('test_pass_rate', 0):.0%} | "
            f"{_neutralize_metrics(v['hypothesis'])[:60]} (parent: {parent[:12]})"
        )

    drift = get_drift_score()
    lines.append(f"\nDrift score: {drift} mutations from baseline")
    return "\n".join(lines)
