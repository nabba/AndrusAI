"""
app.self_improvement — overhauled self-improvement subsystem.

Replaces the ad-hoc `idle_scheduler._auto_discover_topics` + flat skill-file
pipeline with a structured loop:

    Gap Detector  →  Novelty Gate  →  Learner  →  Integrator  →  Evaluator  →  Consolidator

This package (Phases 0–2 of the overhaul) provides:
    - types: typed records (LearningGap, NoveltyReport, SkillDraft, SkillRecord)
    - store: persistence for gaps in ChromaDB
    - novelty: embedding-based dedup against all KBs (replaces bag-of-words)
    - gap_detector: signal aggregation across multiple sources
                    (retrieval-miss, reflexion-fail, MAP-Elites voids …)

Phases 3–6 (Integrator, Evaluator, Consolidator, observability) follow.

Design invariants (from CLAUDE.md):
    - Novelty thresholds and gap-source weights live in module-level config,
      not in agent-modifiable code paths.
    - The Self-Improver agent reads from this package; it does not modify it.
"""

from app.self_improvement.types import (
    GapSource, GapStatus, NoveltyDecision,
    LearningGap, NoveltyReport, SkillDraft, SkillRecord,
)
from app.self_improvement.store import (
    emit_gap, list_open_gaps, update_gap_status, query_gaps,
)
from app.self_improvement.novelty import novelty_report, NOVELTY_THRESHOLDS
from app.self_improvement.gap_detector import (
    emit_retrieval_miss, emit_reflexion_failure,
    emit_mapelites_voids, get_recent_evidence_block,
)
from app.self_improvement.integrator import (
    integrate, classify_kb, regenerate_disk_mirror,
    load_record, list_records, update_record, KB_CHOICES,
)
from app.self_improvement.evaluator import (
    record_hits, flush_hits, record_task_outcome,
    scan_for_decay, usage_distribution,
)
from app.self_improvement.consolidator import (
    run_consolidation_cycle, migrate_legacy_skills, recover_from_team_shared,
    list_proposals, ConsolidationProposal,
)
from app.self_improvement.metrics import (
    pipeline_funnel, topic_diversity, novelty_histogram, health_summary,
)

__all__ = [
    # types
    "GapSource", "GapStatus", "NoveltyDecision",
    "LearningGap", "NoveltyReport", "SkillDraft", "SkillRecord",
    # store
    "emit_gap", "list_open_gaps", "update_gap_status", "query_gaps",
    # novelty
    "novelty_report", "NOVELTY_THRESHOLDS",
    # gap detector
    "emit_retrieval_miss", "emit_reflexion_failure",
    "emit_mapelites_voids", "get_recent_evidence_block",
    # integrator
    "integrate", "classify_kb", "regenerate_disk_mirror",
    "load_record", "list_records", "update_record", "KB_CHOICES",
    # evaluator
    "record_hits", "flush_hits", "record_task_outcome",
    "scan_for_decay", "usage_distribution",
    # consolidator
    "run_consolidation_cycle", "migrate_legacy_skills", "recover_from_team_shared",
    "list_proposals", "ConsolidationProposal",
    # metrics (Phase 6)
    "pipeline_funnel", "topic_diversity", "novelty_histogram", "health_summary",
]
