"""Target-selection planner for the verified mutation engine.

Relocated from the retired ``app.avo_operator._phase_planning`` +
``app.evolution._build_evolution_context`` (2026-06-02 evolution
consolidation), so the verified engine owns its planner instead of importing
from the deleted legacy loop. Behaviour-preserving: the only deliberate change
is dropping the ``mutation_strategies`` prompt section (that module described
the legacy whole-file mutator's strategies and is being deleted — the verified
engine implements via iterate-until-green, not strategy-flavoured rewrites).

The SubIA surprise-driven targeting read (``accuracy_tracker``) is preserved —
it is the interoceptive signal that points self-improvement at the domains the
system was most wrong about.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.llm_factory import create_specialist_llm
from app.metrics import compute_metrics, format_metrics
from app.results_ledger import get_recent_results, get_best_score
from app.healing.error_diagnosis import get_recent_errors, get_error_patterns
from app.evo_memory import recall_similar_failures

logger = logging.getLogger(__name__)

_META_DIR = Path("/app/workspace/meta")
PROGRAM_PATH = Path("/app/workspace/program.md")
SKILLS_DIR = Path("/app/workspace/skills")


def _load_meta_prompt(filename: str, fallback: str = "") -> str:
    """Load a meta-parameter prompt file with fallback to a hardcoded default.

    Meta-parameter files live in workspace/meta/. This provides a safe loading
    path that falls back to the hardcoded prompt if the file doesn't exist.
    """
    meta_path = _META_DIR / filename
    try:
        if meta_path.exists():
            content = meta_path.read_text().strip()
            if content:
                return content
    except OSError:
        pass
    return fallback


def _load_program() -> str:
    """Load the user-editable research directions file."""
    try:
        if PROGRAM_PATH.exists():
            return PROGRAM_PATH.read_text()[:4000]
    except OSError:
        pass
    return "No program.md found. Focus on fixing errors and adding useful skills."


def _build_evolution_context() -> str:
    """Build the full context string for the planner."""
    metrics = compute_metrics()
    program = _load_program()
    errors = get_recent_errors(20)
    patterns = get_error_patterns()
    recent_results = get_recent_results(15)

    # Skill inventory
    skill_names = []
    if SKILLS_DIR.exists():
        for f in sorted(SKILLS_DIR.glob("*.md")):
            if f.name != "learning_queue.md":
                skill_names.append(f.stem)

    # Format recent experiments WITH reasons for keep/discard so the planner
    # learns from failures and doesn't repeat them.
    exp_lines = []
    kept_count = 0
    discarded_count = 0
    for r in recent_results[-15:]:
        status = r.get("status", "?")
        delta = r.get("delta", 0)
        hyp = r.get("hypothesis", "")[:60]
        detail = r.get("detail", "")[:80]
        if status == "keep":
            kept_count += 1
        elif status == "discard":
            discarded_count += 1
        exp_lines.append(
            f"  [{status:7s}] Δ={delta:+.4f} | {hyp}"
            + (f"\n           Reason: {detail}" if detail else "")
        )
    if exp_lines:
        experiments_text = (
            f"  Summary: {kept_count} kept, {discarded_count} discarded out of {len(recent_results)} recent\n"
            + "\n".join(exp_lines)
        )
    else:
        experiments_text = "  No experiments yet."

    # Format error patterns — with cooldown for already-addressed errors.
    _addressed_errors = {}
    for r in recent_results:
        hyp = (r.get("hypothesis", "") + " " + r.get("detail", "")).lower()
        for k in patterns:
            if k.lower()[:20] in hyp:
                _addressed_errors[k] = _addressed_errors.get(k, 0) + 1

    pattern_lines = []
    for k, v in list(patterns.items())[:10]:
        times_addressed = _addressed_errors.get(k, 0)
        if times_addressed >= 3:
            pattern_lines.append(f"  {k}: {v}x (ALREADY ADDRESSED {times_addressed}x — skip this)")
        else:
            pattern_lines.append(f"  {k}: {v}x")
    patterns_text = "\n".join(pattern_lines) if pattern_lines else "  No error patterns."

    # Recent undiagnosed errors — exclude types already addressed 3+ times.
    undiagnosed = [e for e in errors if not e.get("diagnosed")]
    fresh_errors = []
    for e in undiagnosed:
        etype = e.get("error_type", "")
        if _addressed_errors.get(etype, 0) < 3:
            fresh_errors.append(e)
    fresh_errors = fresh_errors[:5]
    error_lines = []
    for e in fresh_errors:
        error_lines.append(
            f"  [{e.get('crew', '?')}] {e.get('error_type', '?')}: "
            f"{e.get('error_msg', '?')[:80]}"
        )
    errors_text = "\n".join(error_lines) if error_lines else "  No fresh undiagnosed errors (all known errors addressed)."

    # Recent verified self-modifications (sourced from the canonical CR audit)
    try:
        from app.self_improvement.history import format_modifications, drift_score
        archive_ctx = format_modifications()
        drift = drift_score()
    except Exception:
        archive_ctx = "No self-modification history available."
        drift = 0

    # Tech radar discoveries (if any)
    tech_ctx = ""
    try:
        from app.crews.tech_radar_crew import get_recent_discoveries
        discoveries = get_recent_discoveries(5)
        if discoveries:
            tech_ctx = "\n## Recent Tech Discoveries\n" + "\n".join(f"  - {d[:150]}" for d in discoveries)
    except Exception:
        pass

    # ── SUBIA bridge: surprise-driven evolution targeting ──────────────────
    subia_ctx = ""
    try:
        from app.subia.prediction.accuracy_tracker import get_tracker
        tracker = get_tracker()
        summary = tracker.all_domains_summary()
        weak_domains = []
        for domain, stats in summary.items():
            if tracker.has_sustained_error(domain):
                weak_domains.append(
                    f"  - {domain}: accuracy={stats.get('mean_accuracy', 0):.2f}, "
                    f"sustained errors={stats.get('recent_bad_count', 0)}"
                )
        if weak_domains:
            subia_ctx = (
                "\n## SUBIA Prediction Failures (HIGH PRIORITY)\n"
                "These domains have sustained prediction errors — improving them\n"
                "would reduce future mistakes and increase system reliability.\n"
                + "\n".join(weak_domains[:5])
            )
    except Exception:
        pass

    # ── SUBIA bridge: evolution snapshot archive context ─────────────────
    snapshot_ctx = ""
    try:
        from app.workspace_versioning import list_evolution_tags
        tags = list_evolution_tags(5)
        if tags:
            snapshot_ctx = (
                "\n## Historical Variants (parent selection)\n"
                "You can propose changes starting from the current state or\n"
                "reference a historical high-scoring variant as a starting point.\n"
                + "\n".join(f"  - {t['tag']} ({t.get('date', '?')})" for t in tags)
            )
    except Exception:
        pass

    # ── Knowledge-informed evolution (Phase 3B) ─────────────────────────────
    kb_evolution_ctx = ""
    try:
        # kb_read routes to the gateway when run in the worker process (serving
        # /compute split); identical to episteme.get_store().query() on the
        # gateway. Empty-KB + error cases both yield [] internally.
        from app.memory import kb_read
        epi_hits = kb_read.query(
            "episteme",
            f"improve multi-agent system {errors_text[:100]}",
            n_results=2,
        )
        if epi_hits:
            epi_texts = [h["text"][:300] for h in epi_hits]
            kb_evolution_ctx += (
                "\n## Research Insights (episteme KB)\n"
                + "\n".join(f"  - {t}" for t in epi_texts) + "\n"
            )
    except Exception:
        pass

    try:
        from app.memory import kb_read
        exp_hits = kb_read.query(
            "experiential",
            "evolution improvement experiment outcome",
            n_results=2,
        )
        if exp_hits:
            exp_texts = [h["text"][:300] for h in exp_hits]
            kb_evolution_ctx += (
                "\n## Past Experiences (journal)\n"
                + "\n".join(f"  - {t}" for t in exp_texts) + "\n"
            )
    except Exception:
        pass

    try:
        from app.tensions.vectorstore import get_store as get_ten
        ten_store = get_ten()
        if ten_store._collection.count() > 0:
            ten_hits = ten_store.get_unresolved(n=3)
            if ten_hits:
                ten_texts = [h["text"][:200] for h in ten_hits]
                kb_evolution_ctx += (
                    "\n## Growth Edges (unresolved tensions)\n"
                    + "\n".join(f"  - {t}" for t in ten_texts) + "\n"
                )
    except Exception:
        pass

    return (
        f"## Research Directions (program.md)\n{program}\n\n"
        f"## Current Metrics\n{format_metrics(metrics)}\n\n"
        f"{archive_ctx}\n\n"
        f"## Recent Experiments (keep/discard history)\n{experiments_text}\n\n"
        f"## Error Patterns\n{patterns_text}\n\n"
        f"## Undiagnosed Errors\n{errors_text}\n\n"
        f"## Current Skills ({len(skill_names)})\n"
        f"  {', '.join(skill_names[:20]) if skill_names else 'None'}\n\n"
        f"## Drift from baseline: {drift} mutations\n"
        f"## Best Score Ever: {get_best_score():.4f}"
        f"{tech_ctx}"
        f"{subia_ctx}"
        f"{snapshot_ctx}"
        f"{kb_evolution_ctx}\n\n"
        f"## DIVERSITY REQUIREMENT\n"
        f"Do NOT propose improvements for errors marked 'ALREADY ADDRESSED'.\n"
        f"Explore NEW areas: performance optimization, code quality, new capabilities,\n"
        f"better error handling for DIFFERENT error types, architectural improvements,\n"
        f"test coverage, documentation, or tool enhancements.\n"
        f"Variety is more valuable than depth on a single topic."
    )


def _phase_planning(
    context: str,
    memory_context: str,
    lineage_context: str,
    tried_hashes: set[str],
) -> dict | None:
    """Premium LLM forms a hypothesis and approach.

    Returns dict with keys: hypothesis, approach, change_type, target_files.
    Returns None if planning fails or the hypothesis duplicates a prior one.
    """
    llm = create_specialist_llm(max_tokens=2048, role="architecture")

    _FALLBACK_PLANNING = (
        "You are the PLANNING phase of an autonomous evolution engine.\n"
        "Analyze the system state and propose ONE improvement hypothesis.\n\n"
        "## Your Task\n"
        "1. Identify the HIGHEST-IMPACT improvement opportunity:\n"
        "   - Recurring errors with traceback → CODE fix (HIGHEST priority)\n"
        "   - Performance bottleneck → CODE optimization\n"
        "   - New capability needed → CODE for new tools/features\n"
        "   - Code quality / refactoring → CODE cleanup\n"
        "   - Missing domain knowledge → SKILL file (LAST RESORT only)\n"
        "2. Form a specific, testable hypothesis\n"
        "3. Check evolutionary memory — do NOT repeat past failures\n\n"
        "CRITICAL RULES:\n"
        "- You MUST use change_type='code' at least 80% of the time.\n"
        "- ONLY use change_type='skill' when there is genuinely NO code fix possible.\n"
        "- Skills are documentation, NOT fixes. Code changes are what actually improve the system.\n"
        "- For code changes, specify the EXACT file path (e.g. 'app/tools/web_search.py').\n"
        "- You will receive the current file contents in the next phase.\n\n"
        "DIVERSITY: Do NOT address errors marked 'ALREADY ADDRESSED' in the context.\n"
        "Explore new improvement areas instead: performance, code quality, new features,\n"
        "better test coverage, architectural cleanup, or tool improvements.\n\n"
        "Respond with ONLY this JSON:\n"
        '{"hypothesis": "what to improve and why",\n'
        ' "approach": "specific technical approach",\n'
        ' "change_type": "code",\n'
        ' "target_files": ["app/path/to/file.py"]}\n'
    )
    planning_instructions = _load_meta_prompt("avo_planning_prompt.md", _FALLBACK_PLANNING)

    prompt = f"{planning_instructions}\n\n## System State\n{context}\n\n"

    # Inject coding conventions so the planner produces elegant code, not just
    # code that improves a metric. Loaded from workspace/meta/ — evolvable.
    conventions = _load_meta_prompt("coding_conventions.md", "")
    if conventions:
        prompt += f"## Coding Conventions (you must follow these)\n{conventions}\n\n"

    if memory_context:
        prompt += f"## Evolutionary Memory\n{memory_context}\n\n"
    if lineage_context:
        prompt += f"## Variant Lineage\n{lineage_context}\n\n"

    # Inject relevant successful patterns from the pattern library as positive
    # exemplars (complements evo_memory's negative knowledge).
    try:
        from app.pattern_library import find_relevant_patterns
        patterns = find_relevant_patterns(context[:500], n=3)
        if patterns:
            pattern_lines = [
                f"  - **{p.template_summary[:80]}** "
                f"(observed {p.times_observed}x, avg delta {p.avg_delta:+.4f})"
                for p in patterns
            ]
            prompt += (
                "\n## Successful patterns from past evolution\n"
                "These patterns have produced real improvements before — "
                "consider them as exemplars:\n"
                + "\n".join(pattern_lines)
                + "\n"
            )
    except Exception as exc:
        logger.debug(f"planning: pattern library lookup failed: {exc}")

    # Inject existing capability owners so the planner knows what already exists
    # and can refactor rather than create parallel modules.
    try:
        from app.self_model import get_self_model
        model = get_self_model()
        cap_lines: list[str] = []
        for cap, owners in sorted(model.capability_map.items()):
            if len(owners) < 2:
                continue
            cap_lines.append(
                f"  - **{cap}**: {', '.join(owners[:4])}"
                + (f" (+{len(owners) - 4} more)" if len(owners) > 4 else "")
            )
            if len(cap_lines) >= 6:
                break
        if cap_lines:
            prompt += (
                "\n## Existing capability owners\n"
                "These capabilities are already provided by the listed files. "
                "Prefer refactoring an existing owner over creating a parallel "
                "module that duplicates the capability:\n"
                + "\n".join(cap_lines)
                + "\n"
            )
    except Exception as exc:
        logger.debug(f"planning: capability map injection failed: {exc}")

    try:
        raw = str(llm.call(prompt)).strip()
    except Exception as e:
        logger.warning(f"planning (phase 1) failed: {e}")
        return None

    from app.utils import safe_json_parse
    plan, err = safe_json_parse(raw)
    if plan is None:
        logger.warning(f"planning: unparseable response: {err}")
        return None

    # Dedup check — exact hash
    hypothesis = plan.get("hypothesis", "")
    h = hashlib.sha256(hypothesis.lower().strip().encode()).hexdigest()[:8]
    if h in tried_hashes:
        logger.info("planning: duplicate hypothesis (exact), skipping")
        return None

    # Fuzzy dedup — catch near-duplicate hypotheses that differ by a few words.
    import re as _re
    _norm = _re.sub(r'[^a-z ]+', '', hypothesis.lower())
    _norm = ' '.join(_norm.split())[:40]
    _fuzzy_h = hashlib.sha256(_norm.encode()).hexdigest()[:8]
    if _fuzzy_h in tried_hashes:
        logger.info("planning: duplicate hypothesis (fuzzy), skipping")
        return None
    tried_hashes.add(_fuzzy_h)

    # Check against known failures
    similar_failures = recall_similar_failures(hypothesis, n=3)
    for sf in similar_failures:
        dist = sf.get("distance", 1.0)
        if dist < 0.15:
            logger.info(f"planning: hypothesis too similar to past failure (dist={dist:.3f})")
            return None

    return plan
