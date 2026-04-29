"""
avo_operator.py — AVO (Agentic Variation Operator) multi-phase pipeline.

Implements the 5-phase AVO loop from NVIDIA's arXiv:2603.24517:
  Phase 1: PLANNING — Premium LLM forms hypothesis from context + memory
  Phase 2: IMPLEMENTATION — Fast LLM generates code/skill changes
  Phase 3: LOCAL TESTING — No LLM; AST + safety checks catch bad mutations
  Phase 2↔3 REPAIR LOOP — Bounded to 3 attempts
  Phase 4: SELF-CRITIQUE — Mid LLM evaluates quality before submission
  Phase 5: SUBMISSION — Construct MutationSpec for experiment_runner

Uses direct llm.call() (not CrewAI Agent/Crew) for tight control over the
multi-phase pipeline. This pattern is established in idle_scheduler.py:308.
"""

from __future__ import annotations

import ast
import json
import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.experiment_runner import MutationSpec, generate_experiment_id
from app.llm_factory import create_specialist_llm
from app.evo_memory import format_memory_context, recall_similar_failures

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 3
_META_DIR = Path("/app/workspace/meta")


def _load_meta_prompt(filename: str, fallback: str = "") -> str:
    """Load a meta-parameter prompt file with fallback to hardcoded default.

    Meta-parameter files live in workspace/meta/ and can be evolved by
    the meta-evolution engine. This function provides a safe loading path
    that falls back to the hardcoded prompt if the file doesn't exist.
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


@dataclass
class AVOResult:
    """Result of an AVO variation pipeline run."""
    mutation: MutationSpec | None = None
    phases_completed: int = 0
    repair_attempts: int = 0
    critique_notes: str = ""
    abandoned_reason: str = ""

# ── Phase 1: Planning ────────────────────────────────────────────────────────

def _phase_planning(
    context: str,
    memory_context: str,
    lineage_context: str,
    tried_hashes: set[str],
) -> dict | None:
    """Premium LLM forms a hypothesis and approach.

    Returns dict with keys: hypothesis, approach, change_type, target_files.
    Returns None if planning fails.
    """
    # Planning needs strong reasoning — use architecture role (routes to mid-tier)
    # This is the most important phase: bad plans waste all subsequent phases
    llm = create_specialist_llm(max_tokens=2048, role="architecture")

    # Load planning instructions from workspace/meta/ (evolvable via meta-evolution)
    # Falls back to hardcoded prompt if the meta file doesn't exist
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

    # Fix A: Inject coding conventions so the planner produces elegant code,
    # not just code that improves a metric. Loaded from workspace/meta/ —
    # evolvable but starts with the rules from CLAUDE.md.
    conventions = _load_meta_prompt("coding_conventions.md", "")
    if conventions:
        prompt += f"## Coding Conventions (you must follow these)\n{conventions}\n\n"

    if memory_context:
        prompt += f"## Evolutionary Memory\n{memory_context}\n\n"
    if lineage_context:
        prompt += f"## Variant Lineage\n{lineage_context}\n\n"

    # General improvements pass: inject mutation strategy guidance
    # Samples one of six strategies (defensive, refactoring, capability,
    # architectural, removal, optimization) per cycle to break out of the
    # observed defensive-pattern monoculture.
    try:
        from app.mutation_strategies import select_strategy, build_strategy_prompt_section
        strategy = select_strategy()
        prompt += build_strategy_prompt_section(strategy)
        logger.info(f"AVO Phase 1: sampled strategy {strategy.name.value}")
    except Exception as exc:
        logger.debug(f"AVO Phase 1: strategy injection failed: {exc}")

    # Inject relevant successful patterns from the pattern library as
    # positive exemplars (complements evo_memory's negative knowledge).
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
        logger.debug(f"AVO Phase 1: pattern library lookup failed: {exc}")

    # Inject existing capability owners so the planner knows what already
    # exists and can choose to refactor rather than create parallel modules.
    # This closes the gap exposed by exp_202604290007_1172, which proposed
    # app/orch/commander.py despite app/agents/commander/ already existing.
    try:
        from app.self_model import get_self_model
        model = get_self_model()
        cap_lines: list[str] = []
        # Only show non-trivial capabilities — at most 6, with up to 4 owners each
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
        logger.debug(f"AVO Phase 1: capability map injection failed: {exc}")

    try:
        raw = str(llm.call(prompt)).strip()
    except Exception as e:
        logger.warning(f"AVO Phase 1 (planning) failed: {e}")
        return None

    from app.utils import safe_json_parse
    plan, err = safe_json_parse(raw)
    if plan is None:
        logger.warning(f"AVO Phase 1: unparseable response: {err}")
        return None

    # Dedup check — exact hash
    hypothesis = plan.get("hypothesis", "")
    h = hashlib.sha256(hypothesis.lower().strip().encode()).hexdigest()[:8]
    if h in tried_hashes:
        logger.info(f"AVO Phase 1: duplicate hypothesis (exact), skipping")
        return None

    # Fuzzy dedup — catch near-duplicate hypotheses that differ by a few words
    # Normalize: lowercase, strip numbers, collapse whitespace, take first 40 chars
    import re as _re
    _norm = _re.sub(r'[^a-z ]+', '', hypothesis.lower())
    _norm = ' '.join(_norm.split())[:40]
    _fuzzy_h = hashlib.sha256(_norm.encode()).hexdigest()[:8]
    if _fuzzy_h in tried_hashes:
        logger.info(f"AVO Phase 1: duplicate hypothesis (fuzzy), skipping")
        return None
    tried_hashes.add(_fuzzy_h)  # Prevent future fuzzy dupes within session

    # Check against known failures
    similar_failures = recall_similar_failures(hypothesis, n=3)
    for sf in similar_failures:
        dist = sf.get("distance", 1.0)
        if dist < 0.15:  # Very similar to a past failure
            logger.info(f"AVO Phase 1: hypothesis too similar to past failure (dist={dist:.3f})")
            return None

    return plan

# ── File reading for code context ────────────────────────────────────────────

def _read_target_files(target_files: list[str]) -> dict[str, str]:
    """Read current contents of target files for code mutation context.

    Returns {file_path: content} for files that exist.
    Resolves paths relative to /app/ (the container root).
    Skips protected files and non-existent paths.
    """
    from app.auto_deployer import PROTECTED_FILES

    result = {}
    for fpath in target_files[:5]:  # Cap at 5 files
        # Normalize path
        if not fpath.startswith("app/"):
            fpath = f"app/{fpath}" if not fpath.startswith("/") else fpath
        full = Path(f"/app/{fpath}") if not fpath.startswith("/") else Path(fpath)

        # Skip protected files
        if fpath in PROTECTED_FILES:
            logger.debug(f"AVO: skipping protected file {fpath}")
            continue

        # Read if exists
        try:
            if full.exists() and full.is_file() and full.suffix == ".py":
                content = full.read_text(errors="replace")
                if len(content) > 0:
                    result[fpath] = content[:8000]  # 8K chars covers most files fully
                    logger.debug(f"AVO: read {fpath} ({len(content)} chars)")
        except Exception:
            pass

    return result

# ── Phase 2: Implementation ──────────────────────────────────────────────────

def _phase_implementation(plan: dict, repair_errors: list | None = None) -> dict | None:
    """Fast LLM generates file contents based on the plan.

    Returns dict {file_path: content} or None on failure.
    """
    change_type_hint = plan.get("change_type", "skill")
    if change_type_hint == "code":
        # Code changes need reliable JSON output with complete file contents.
        # DeepSeek (budget tier) is more reliable at structured output than local models.
        llm = create_specialist_llm(max_tokens=8192, role="coding")
    else:
        # Skill files are simpler — local model is fine and free
        llm = create_specialist_llm(max_tokens=8192, role="coding", force_tier="local")

    change_type = plan.get("change_type", "skill")
    hypothesis = plan.get("hypothesis", "")
    approach = plan.get("approach", "")
    target_files = plan.get("target_files", [])

    if change_type == "skill":
        prompt = (
            "You are generating a SKILL FILE for an autonomous AI agent team.\n"
            "Skill files teach domain knowledge that helps agents perform better.\n\n"
            f"## Hypothesis\n{hypothesis}\n\n"
            f"## Approach\n{approach}\n\n"
            "## Requirements\n"
            "- Write practical, actionable knowledge (NOT vague advice)\n"
            "- Include specific examples, patterns, and techniques\n"
            "- Minimum 200 characters of useful content\n"
            "- Format as Markdown with clear headings\n"
        )
        if target_files:
            fname = target_files[0] if target_files[0].startswith("skills/") else f"skills/{target_files[0]}"
        else:
            # Generate filename from hypothesis
            slug = hypothesis[:40].lower().replace(" ", "_")
            slug = "".join(c for c in slug if c.isalnum() or c == "_")
            fname = f"skills/{slug}.md"
        prompt += f"\nFile path: {fname}\n"
    else:
        # Read current contents of target files so the LLM modifies real code
        existing_code = _read_target_files(target_files)

        prompt = (
            "You are generating a CODE CHANGE for an autonomous AI agent team.\n"
            "You MUST return the COMPLETE file contents (not a diff or partial).\n\n"
            f"## Hypothesis\n{hypothesis}\n\n"
            f"## Approach\n{approach}\n\n"
            f"## Target files\n{', '.join(target_files)}\n\n"
        )

        if existing_code:
            prompt += "## Current file contents (MODIFY these, don't write from scratch)\n"
            for fpath, content in existing_code.items():
                truncated = content  # Already capped at 8K in _read_target_files
                prompt += f"\n### {fpath}\n```python\n{truncated}\n```\n"
            prompt += "\n"

        prompt += (
            "## Requirements\n"
            "- Return the COMPLETE modified file (not just changed lines)\n"
            "- Preserve all existing functionality — only add/fix what the hypothesis requires\n"
            "- Write clean, working Python code\n"
            "- Do NOT use dangerous imports (subprocess, os, sys, etc.)\n"
            "- Do NOT modify protected files\n"
            "- Keep changes minimal and focused\n"
        )

    if repair_errors:
        prompt += (
            "\n## REPAIR — Previous attempt had these errors:\n"
            + "\n".join(f"  - {e}" for e in repair_errors[:5])
            + "\n\nFix these errors in your output.\n"
        )

    prompt += (
        "\n\nRespond with ONLY this JSON (no markdown fences):\n"
        '{"files": {"path/to/file.ext": "file content here"}}\n'
    )

    try:
        raw = str(llm.call(prompt)).strip()
    except Exception as e:
        logger.warning(f"AVO Phase 2 (implementation) failed: {e}")
        return None

    from app.utils import safe_json_parse
    result, err = safe_json_parse(raw)
    if result is None:
        logger.warning(f"AVO Phase 2: unparseable response: {err}")
        return None

    files = result.get("files")
    if not isinstance(files, dict) or not files:
        logger.warning(f"AVO Phase 2: no files in response")
        return None

    return files

# ── Phase 3: Local Testing ───────────────────────────────────────────────────

def _phase_local_testing(files: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate mutation locally without LLM. Returns (ok, errors)."""
    from app.auto_deployer import (
        _check_dangerous_imports,
        validate_proposal_paths,
        PROTECTED_FILES,
    )

    errors = []

    # Check for protected file violations
    path_violations = validate_proposal_paths(files)
    if path_violations:
        errors.extend(path_violations)

    for fpath, content in files.items():
        # Python files: AST parse + safety scan
        if fpath.endswith(".py"):
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                errors.append(f"Syntax error in {fpath}: {e}")
                continue

            import_violations = _check_dangerous_imports(tree)
            if import_violations:
                errors.extend(f"{fpath}: {v}" for v in import_violations)

        # Skill files: minimum size
        elif fpath.endswith(".md"):
            if len(content.strip()) < 50:
                errors.append(f"Skill file too short ({len(content)} chars): {fpath}")

        # All files: non-empty
        if not content.strip():
            errors.append(f"Empty file: {fpath}")

    # Fix E: Architectural review — cycle detection is a hard reject;
    # capability overlaps and centrality spikes are surfaced via metadata
    # for the Phase 4 critique LLM but don't fail Phase 3.
    try:
        from app.architectural_review import review_mutation
        report = review_mutation(files)
        if report.has_hard_rejects:
            errors.append(f"Architectural review: {report.summary()}")
    except Exception as exc:
        logger.debug(f"architectural_review unavailable: {exc}")

    return (len(errors) == 0, errors)

# ── Phase 4: Self-Critique ───────────────────────────────────────────────────

def _phase_self_critique(
    plan: dict,
    files: dict[str, str],
    memory_context: str,
) -> tuple[bool, str]:
    """External LLM evaluates the mutation quality (DGM: different model than generator).

    Phase 2 uses local codestral:22b for generation.
    Phase 4 uses DeepSeek (budget tier) for critique — ensures DGM separation.

    Returns (approved: bool, notes: str).
    """
    from app.llm_factory import create_cheap_vetting_llm
    llm = create_cheap_vetting_llm()

    hypothesis = plan.get("hypothesis", "")
    change_type = plan.get("change_type", "skill")

    file_summary = []
    for fpath, content in files.items():
        file_summary.append(f"### {fpath} ({len(content)} chars)\n{content[:500]}")

    # Load critique instructions from workspace/meta/ (evolvable via meta-evolution)
    _FALLBACK_CRITIQUE = (
        "You are the SELF-CRITIQUE phase of an evolution engine.\n"
        "Review this proposed mutation and decide: APPROVE or REJECT.\n\n"
        "## Evaluation Criteria\n"
        "1. Does the implementation match the hypothesis?\n"
        "2. Is the scope reasonable (not too many changes)?\n"
        "3. For skills: is the content practical and actionable (not generic)?\n"
        "4. For code: is it clean, safe, and focused?\n"
        "5. Is this NOT a repeat of a known failed pattern?\n\n"
        'Respond with ONLY: {"approve": true/false, "concerns": ["..."]}\n'
    )
    critique_instructions = _load_meta_prompt("avo_critique_prompt.md", _FALLBACK_CRITIQUE)

    prompt = (
        f"{critique_instructions}\n\n"
        f"## Hypothesis\n{hypothesis}\n\n"
        f"## Change Type: {change_type}\n\n"
        f"## Files\n" + "\n\n".join(file_summary) + "\n\n"
    )

    # Fix A: Critique LLM also sees the coding conventions, so it can reject
    # mutations that violate them even if functionally correct.
    conventions = _load_meta_prompt("coding_conventions.md", "")
    if conventions:
        prompt += f"## Coding Conventions (mutations must comply)\n{conventions}\n\n"

    # Fix E: Surface architectural review findings to the critique LLM.
    # Hard rejects (cycles) are caught in Phase 3; soft warnings here
    # become inputs to the critique's structured rubric.
    try:
        from app.architectural_review import review_mutation
        report = review_mutation(files)
        if report.has_soft_warnings:
            prompt += (
                "## Architectural Review Findings (consider these in your critique)\n"
                f"{report.summary()}\n\n"
            )
    except Exception:
        pass

    if memory_context:
        prompt += f"## Evolutionary Memory\n{memory_context}\n\n"

    try:
        raw = str(llm.call(prompt)).strip()
    except Exception as e:
        logger.warning(f"AVO Phase 4 (self-critique) failed: {e}")
        return True, "Critique unavailable — proceeding"

    from app.utils import safe_json_parse
    result, err = safe_json_parse(raw)
    if result is None:
        return True, f"Critique unparseable: {err}"

    approved = result.get("approve", True)
    concerns = result.get("concerns", [])

    # Fix C: Honor the structured rubric — hard rejects always fail, and a
    # rubric score below 7/10 also fails (matches the prompt's threshold).
    hard_rejects = result.get("hard_rejects_triggered", [])
    if hard_rejects:
        approved = False
        concerns = list(concerns) + [f"Hard reject: {hr}" for hr in hard_rejects[:5]]

    rubric_score = result.get("rubric_score")
    if isinstance(rubric_score, (int, float)) and rubric_score < 7:
        approved = False
        concerns = list(concerns) + [f"Rubric score {rubric_score}/10 below threshold of 7"]

    smells = result.get("smells_detected", [])
    if len(smells) >= 2:
        approved = False
        concerns = list(concerns) + [f"Multiple smells: {', '.join(smells[:3])}"]

    notes = "; ".join(concerns) if concerns else "No concerns"
    return approved, notes

# ── Main Pipeline ────────────────────────────────────────────────────────────

def run_avo_pipeline(
    context: str,
    tried_hashes: set[str],
    memory_context: str,
    lineage_context: str,
    yield_check: Callable | None = None,
) -> AVOResult:
    """Execute the full AVO 5-phase variation pipeline.

    Args:
        context: System state context from _build_evolution_context()
        tried_hashes: Set of hypothesis hashes to avoid repeating
        memory_context: Formatted evolutionary memory string
        lineage_context: Variant archive genealogy string
        yield_check: Callable that returns True if we should abort (user task arrived)

    Returns:
        AVOResult with mutation (if successful) or abandoned_reason (if not)
    """
    result = AVOResult()

    def _should_yield() -> bool:
        if yield_check and yield_check():
            result.abandoned_reason = "Yielded to user task"
            return True
        return False

    # Phase 1: Planning
    if _should_yield():
        return result

    plan = _phase_planning(context, memory_context, lineage_context, tried_hashes)
    if plan is None:
        result.abandoned_reason = "Planning produced no viable hypothesis"
        return result
    result.phases_completed = 1

    logger.info(f"AVO Phase 1: {plan.get('hypothesis', '?')[:80]}")

    # Phase 2→3: Implementation + Local Testing (with repair loop)
    if _should_yield():
        return result

    repair_errors = None
    files = None

    for attempt in range(_MAX_REPAIR_ATTEMPTS):
        result.repair_attempts = attempt + 1

        # Phase 2: Implementation
        files = _phase_implementation(plan, repair_errors)
        if files is None:
            result.abandoned_reason = f"Implementation failed on attempt {attempt + 1}"
            return result
        result.phases_completed = 2

        # Phase 3: Local Testing
        ok, errors = _phase_local_testing(files)
        if ok:
            result.phases_completed = 3
            break

        logger.info(f"AVO Phase 3: {len(errors)} errors on attempt {attempt + 1}: {errors[:3]}")
        repair_errors = errors

    else:
        # All repair attempts exhausted
        result.abandoned_reason = f"Local testing failed after {_MAX_REPAIR_ATTEMPTS} repair attempts: {repair_errors}"
        return result

    # Phase 4: Self-Critique
    if _should_yield():
        return result

    approved, notes = _phase_self_critique(plan, files, memory_context)
    result.critique_notes = notes
    result.phases_completed = 4

    if not approved:
        result.abandoned_reason = f"Self-critique rejected: {notes}"
        logger.info(f"AVO Phase 4: rejected — {notes}")
        return result

    # Phase 5: Submission
    hypothesis = plan.get("hypothesis", "unknown")
    change_type = plan.get("change_type", "skill")
    exp_id = generate_experiment_id(hypothesis)

    result.mutation = MutationSpec(
        experiment_id=exp_id,
        hypothesis=hypothesis,
        change_type=change_type,
        files=files,
    )
    result.phases_completed = 5

    logger.info(
        f"AVO complete: {change_type} mutation with {len(files)} file(s) "
        f"({result.repair_attempts} repair attempts, critique: {notes[:60]})"
    )
    return result
