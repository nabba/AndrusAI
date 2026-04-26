"""
knowledge_compactor.py — Skill consolidation and skill→code promotion.

After 38 days, the system has accumulated 49 skill markdown files. Many
overlap heavily — multiple files about error handling, multiple about web
search patterns, multiple about API quotas. Knowledge fragments rather
than compounds.

This module provides two background jobs that the idle_scheduler runs
weekly:

  1. Skill consolidation: cluster skills by embedding similarity, propose
     merges of clusters with overlap above a threshold. Reduces duplication
     and keeps the skill library searchable.

  2. Skill→code promotion: identify skills that describe code patterns
     referenced in 5+ tasks, propose extracting them as reusable utility
     code in app/utils/. Bridges the gap from "documentation" to
     "capability."

Both jobs are advisory — they propose changes via the standard evolution
proposal pipeline (app/proposals.py), they do NOT modify code directly.
This preserves the existing safety guarantees.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

SKILLS_DIR = Path("/app/workspace/skills")
COMPACTOR_LOG_PATH = Path("/app/workspace/knowledge_compactor_log.json")

_SIMILARITY_THRESHOLD = 0.85       # Embeddings cosine sim above this → merge candidate
_MIN_CLUSTER_SIZE = 2              # At least 2 files needed to consolidate
_PROMOTION_REFERENCE_THRESHOLD = 5  # Skill referenced in 5+ tasks → promotion candidate


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SkillCluster:
    """A group of similar skill files."""
    files: tuple[Path, ...]
    avg_similarity: float
    suggested_name: str


@dataclass(frozen=True)
class PromotionCandidate:
    """A skill identified as worth promoting to reusable code."""
    skill_path: Path
    reference_count: int
    target_module_name: str
    code_pattern_hints: tuple[str, ...]


# ── Skill consolidation ──────────────────────────────────────────────────────

def _load_skill(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
        return text if text.strip() else None
    except (OSError, UnicodeDecodeError):
        return None


def _embed(text: str) -> list[float] | None:
    """Embed text using ChromaDB's manager. Returns None on failure."""
    try:
        from app.memory.chromadb_manager import embed
        return embed(text[:2000])
    except Exception:
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_skill_clusters(threshold: float = _SIMILARITY_THRESHOLD) -> list[SkillCluster]:
    """Cluster skill files by embedding similarity.

    Returns clusters of 2+ files where pairwise similarity exceeds threshold.
    Single-file "clusters" are excluded (nothing to merge).
    """
    if not SKILLS_DIR.exists():
        return []

    paths = sorted(p for p in SKILLS_DIR.glob("*.md") if p.name != "learning_queue.md")
    if len(paths) < _MIN_CLUSTER_SIZE:
        return []

    # Embed all skills
    embeddings: dict[Path, list[float]] = {}
    for path in paths:
        text = _load_skill(path)
        if not text:
            continue
        emb = _embed(text)
        if emb:
            embeddings[path] = emb

    # Greedy clustering: assign each unclustered file to the first cluster
    # where its similarity to the centroid (first member) exceeds threshold.
    clusters: list[list[Path]] = []
    cluster_sims: list[list[float]] = []  # parallel list of pairwise sims

    for path, emb in embeddings.items():
        placed = False
        for cluster, sims in zip(clusters, cluster_sims):
            anchor_emb = embeddings.get(cluster[0])
            if not anchor_emb:
                continue
            sim = _cosine_similarity(emb, anchor_emb)
            if sim >= threshold:
                cluster.append(path)
                sims.append(sim)
                placed = True
                break
        if not placed:
            clusters.append([path])
            cluster_sims.append([])

    # Filter to clusters with 2+ files
    result: list[SkillCluster] = []
    for cluster, sims in zip(clusters, cluster_sims):
        if len(cluster) < _MIN_CLUSTER_SIZE:
            continue
        avg_sim = sum(sims) / len(sims) if sims else 0.0
        result.append(SkillCluster(
            files=tuple(cluster),
            avg_similarity=round(avg_sim, 3),
            suggested_name=_suggest_merged_name(cluster),
        ))

    return result


def _suggest_merged_name(paths: list[Path]) -> str:
    """Propose a filename for the merged skill from cluster members."""
    # Strip hash suffixes and extensions, find common prefix
    stems = [p.stem for p in paths]
    # Remove "__hexhash" suffix if present
    stems_clean = [re.sub(r"__[a-f0-9]+$", "", s) for s in stems]
    # Find longest common prefix
    if not stems_clean:
        return "consolidated_skill.md"
    common = stems_clean[0]
    for s in stems_clean[1:]:
        while not s.startswith(common):
            common = common[:-1]
            if not common:
                break
    common = common.strip("_")
    if len(common) < 5:
        # Fall back to a hash of the merged content
        common = "consolidated_" + hashlib.sha256(
            "".join(stems_clean).encode()
        ).hexdigest()[:8]
    return f"{common}.md"


def consolidate_cluster(cluster: SkillCluster) -> str | None:
    """Propose consolidation of a skill cluster as an evolution proposal.

    Does NOT delete or merge files directly — creates a proposal that the
    AVO planner can act on. This preserves the standard safety pipeline.

    Returns a description of the proposal (or None if creation fails).
    """
    if len(cluster.files) < _MIN_CLUSTER_SIZE:
        return None

    file_summaries = []
    for path in cluster.files:
        text = _load_skill(path)
        if text:
            # First non-empty line as the summary
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    file_summaries.append(f"- **{path.name}**: {line[:100]}")
                    break
            else:
                file_summaries.append(f"- **{path.name}** (header-only)")

    description = (
        f"Consolidate {len(cluster.files)} similar skills "
        f"(avg cosine={cluster.avg_similarity:.2f}) into {cluster.suggested_name}:\n\n"
        + "\n".join(file_summaries)
        + f"\n\nProposed action: merge content, retain unique sections, delete originals."
    )

    try:
        from app.proposals import create_proposal
        pid = create_proposal(
            title=f"Consolidate {len(cluster.files)} similar skills",
            description=description[:2000],
            proposal_type="skill",
            files={},  # Proposal-only — actual merge handled by AVO
        )
        logger.info(f"knowledge_compactor: proposed consolidation {pid} for cluster of {len(cluster.files)}")
        return f"proposal:{pid}"
    except Exception as e:
        logger.debug(f"knowledge_compactor: proposal creation failed: {e}")
        return None


# ── Skill→code promotion ─────────────────────────────────────────────────────

def _count_skill_references(skill_name: str) -> int:
    """Count how often a skill has been referenced in recent tasks/results.

    Looks at variant_archive.json hypotheses and results.tsv detail fields
    for mentions of the skill's name.
    """
    count = 0
    pattern = re.escape(skill_name.replace("_", " ").lower())

    # Scan variant archive
    try:
        from app.variant_archive import get_recent_variants
        for v in get_recent_variants(200):
            text = (v.get("hypothesis", "") + " " + v.get("mutation_summary", "")).lower()
            if re.search(pattern, text):
                count += 1
    except Exception:
        pass

    # Scan results ledger
    try:
        from app.results_ledger import get_recent_results
        for r in get_recent_results(200):
            text = (r.get("hypothesis", "") + " " + r.get("detail", "")).lower()
            if re.search(pattern, text):
                count += 1
    except Exception:
        pass

    return count


def find_promotion_candidates() -> list[PromotionCandidate]:
    """Identify skills that describe code patterns and have been referenced enough.

    Heuristic: a skill is a promotion candidate if:
      1. It contains code blocks (```python ... ```)
      2. It has been referenced in 5+ tasks/experiments
      3. It is at least 200 chars long (substantive)
    """
    if not SKILLS_DIR.exists():
        return []

    candidates: list[PromotionCandidate] = []

    for path in SKILLS_DIR.glob("*.md"):
        if path.name == "learning_queue.md":
            continue
        text = _load_skill(path)
        if not text or len(text) < 200:
            continue

        # Must contain a Python code block
        if "```python" not in text and "```py" not in text:
            continue

        # Reference count
        skill_name = re.sub(r"__[a-f0-9]+$", "", path.stem)
        ref_count = _count_skill_references(skill_name)
        if ref_count < _PROMOTION_REFERENCE_THRESHOLD:
            continue

        # Extract code patterns (first python block, top 500 chars)
        code_match = re.search(r"```py(?:thon)?\n(.*?)\n```", text, re.DOTALL)
        hints: tuple[str, ...] = ()
        if code_match:
            code = code_match.group(1)
            # Pick out function/class names as hints
            hint_names = re.findall(r"^(?:def|class)\s+(\w+)", code, re.M)
            hints = tuple(hint_names[:5])

        candidates.append(PromotionCandidate(
            skill_path=path,
            reference_count=ref_count,
            target_module_name=skill_name + ".py",
            code_pattern_hints=hints,
        ))

    return candidates


def propose_skill_to_code(candidate: PromotionCandidate) -> str | None:
    """Create an evolution proposal to extract a skill as reusable code.

    The proposal targets app/utils/{skill_name}.py and includes the
    extracted code pattern as a starting point. The AVO planner refines
    it during the implementation phase.

    Returns the proposal ID, or None on failure.
    """
    text = _load_skill(candidate.skill_path)
    if not text:
        return None

    code_block = ""
    code_match = re.search(r"```py(?:thon)?\n(.*?)\n```", text, re.DOTALL)
    if code_match:
        code_block = code_match.group(1).strip()

    description = (
        f"Promote skill '{candidate.skill_path.name}' to reusable code.\n\n"
        f"This skill has been referenced in {candidate.reference_count} tasks/experiments.\n"
        f"Target module: app/utils/{candidate.target_module_name}\n"
        f"Code pattern hints: {', '.join(candidate.code_pattern_hints) or 'none'}\n\n"
        f"Source skill content (truncated):\n{text[:1000]}\n\n"
        f"Extract the reusable code pattern into a proper module with tests."
    )

    try:
        from app.proposals import create_proposal
        pid = create_proposal(
            title=f"Promote skill to code: {candidate.skill_path.stem}",
            description=description[:2000],
            proposal_type="code",
            files={
                f"app/utils/{candidate.target_module_name}": (
                    f'"""\nExtracted from skill {candidate.skill_path.name}.\n"""\n\n'
                    + code_block
                ),
            },
        )
        logger.info(
            f"knowledge_compactor: proposed promotion {pid} for {candidate.skill_path.name}"
        )
        return pid
    except Exception as e:
        logger.debug(f"knowledge_compactor: promotion proposal failed: {e}")
        return None


# ── Background job entry point ───────────────────────────────────────────────

def run_consolidation_cycle() -> dict:
    """Background job: find clusters and promotion candidates, propose actions.

    Designed for idle_scheduler as a HEAVY job (typically 10-30s for ~50 skills).
    Returns a summary dict for logging.
    """
    summary: dict = {"timestamp": time.time()}

    try:
        clusters = find_skill_clusters()
        consolidations = 0
        for cluster in clusters[:3]:  # Cap at 3 per cycle to avoid proposal spam
            if consolidate_cluster(cluster):
                consolidations += 1
        summary["clusters_found"] = len(clusters)
        summary["consolidations_proposed"] = consolidations
    except Exception as e:
        logger.debug(f"knowledge_compactor: consolidation phase failed: {e}")
        summary["clusters_found"] = 0
        summary["consolidations_proposed"] = 0

    try:
        candidates = find_promotion_candidates()
        promotions = 0
        for candidate in candidates[:2]:  # Cap at 2 per cycle
            if propose_skill_to_code(candidate):
                promotions += 1
        summary["promotion_candidates"] = len(candidates)
        summary["promotions_proposed"] = promotions
    except Exception as e:
        logger.debug(f"knowledge_compactor: promotion phase failed: {e}")
        summary["promotion_candidates"] = 0
        summary["promotions_proposed"] = 0

    _persist_log(summary)
    return summary


def _persist_log(summary: dict) -> None:
    try:
        existing: list[dict] = []
        if COMPACTOR_LOG_PATH.exists():
            existing = json.loads(COMPACTOR_LOG_PATH.read_text())
        existing.append(summary)
        existing = existing[-50:]
        COMPACTOR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMPACTOR_LOG_PATH.write_text(json.dumps(existing, indent=2, default=str))
    except OSError:
        pass


def get_compactor_stats() -> dict:
    """Aggregate stats for the dashboard."""
    if not COMPACTOR_LOG_PATH.exists():
        return {"runs": 0}
    try:
        log = json.loads(COMPACTOR_LOG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"runs": 0}

    total_clusters = sum(e.get("clusters_found", 0) for e in log)
    total_consolidations = sum(e.get("consolidations_proposed", 0) for e in log)
    total_promotions = sum(e.get("promotions_proposed", 0) for e in log)

    return {
        "runs": len(log),
        "lifetime_clusters_found": total_clusters,
        "lifetime_consolidations_proposed": total_consolidations,
        "lifetime_promotions_proposed": total_promotions,
        "last_run": log[-1] if log else None,
    }
