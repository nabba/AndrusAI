"""
meta_evolution.py — Meta-evolution loop: evolves the evolution engine itself.

This module implements second-order improvement: instead of evolving the
agents or skills, it evolves the *parameters* that control how the evolution
engine proposes, evaluates, and selects mutations.

Meta-parameters live in workspace/meta/ as evolvable files:
  - avo_planning_prompt.md      — How mutations are conceived
  - avo_critique_prompt.md      — How mutations are self-critiqued
  - composite_weights.json      — What "improvement" means
  - ensemble_weights.json       — Which models are used when
  - judge_rubric.json           — How quality is assessed
  - selection_criteria.json     — Keep/discard sensitivity

The meta-evolution loop runs at 1/5 the frequency of standard evolution
(wired as a HEAVY job in idle_scheduler). It requires 3 consecutive
improvements before promoting a meta-change, limiting mutations to 3/week.

DGM-Hyperagents showed that when the meta-level improvement procedure is
itself editable, the system discovers new engineering infrastructure
autonomously — the engine gets better at proposing mutations.

IMMUTABLE — this module is in TIER_IMMUTABLE and cannot modify itself.
The meta-evolution engine can only modify workspace/meta/ files.
"""

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── IMMUTABLE CONSTANTS ─────────────────────────────────────────────────────

MAX_META_MUTATIONS_PER_WEEK = 3
CONSECUTIVE_IMPROVEMENTS_TO_PROMOTE = 3
META_EVAL_EXPERIMENTS = 5  # Run 5 standard evolution cycles to measure effectiveness
META_COOLDOWN_HOURS = 8    # Minimum hours between meta-evolution cycles

META_DIR = Path("/app/workspace/meta")
META_HISTORY_PATH = Path("/app/workspace/meta_evolution_history.json")
META_BACKUP_DIR = Path("/app/workspace/meta_backups")


# ── Effectiveness measurement ───────────────────────────────────────────────

def measure_evolution_effectiveness(n: int = 50) -> dict[str, float]:
    """Measure how effective the current evolution parameters are.

    This measures the *evolution engine's* performance, not the system's
    task quality (which is standard evolution's domain).

    Returns:
        Dict with effectiveness metrics:
          - kept_ratio: fraction of recent experiments that were kept
          - avg_delta: average improvement of kept mutations
          - diversity: hypothesis diversity (unique patterns / total)
          - code_ratio: fraction of code changes vs skill changes
          - sample_size: number of experiments analyzed
    """
    try:
        from app.results_ledger import get_recent_results
        recent = get_recent_results(n)
    except Exception:
        return {
            "kept_ratio": 0.0, "avg_delta": 0.0,
            "diversity": 0.0, "code_ratio": 0.0, "sample_size": 0,
        }

    if not recent:
        return {
            "kept_ratio": 0.0, "avg_delta": 0.0,
            "diversity": 0.0, "code_ratio": 0.0, "sample_size": 0,
        }

    kept = [r for r in recent if r.get("status") == "keep"]
    kept_ratio = len(kept) / len(recent)

    deltas = [r.get("delta", 0.0) for r in kept]
    avg_delta = sum(deltas) / max(1, len(deltas))

    # Hypothesis diversity: unique first-20-char patterns / total
    hypotheses = [r.get("hypothesis", "")[:20] for r in recent]
    unique_patterns = len(set(h for h in hypotheses if h))
    diversity = unique_patterns / max(1, len(recent))

    # Code vs skill ratio
    code_count = sum(1 for r in recent if r.get("change_type") == "code")
    code_ratio = code_count / max(1, len(recent))

    return {
        "kept_ratio": round(kept_ratio, 3),
        "avg_delta": round(avg_delta, 6),
        "diversity": round(diversity, 3),
        "code_ratio": round(code_ratio, 3),
        "sample_size": len(recent),
    }


# ── History management ──────────────────────────────────────────────────────

def _load_history() -> list[dict]:
    """Load meta-evolution history from disk."""
    try:
        if META_HISTORY_PATH.exists():
            return json.loads(META_HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_history(history: list[dict]) -> None:
    """Persist meta-evolution history to disk."""
    try:
        META_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        META_HISTORY_PATH.write_text(json.dumps(history, indent=2))
    except OSError as e:
        logger.warning(f"meta_evolution: failed to save history: {e}")


def _count_weekly_mutations() -> int:
    """Count meta-mutations in the last 7 days."""
    history = _load_history()
    cutoff = time.time() - 7 * 24 * 3600
    return sum(
        1 for h in history
        if h.get("promoted", False) and h.get("timestamp", 0) > cutoff
    )


def _hours_since_last_cycle() -> float:
    """Hours since the last meta-evolution cycle."""
    history = _load_history()
    if not history:
        return float("inf")
    last_ts = max(h.get("timestamp", 0) for h in history)
    if last_ts <= 0:
        return float("inf")
    return (time.time() - last_ts) / 3600


# ── Meta-parameter management ──────────────────────────────────────────────

def _load_meta_files() -> dict[str, str]:
    """Load all meta-parameter files from workspace/meta/."""
    meta_files: dict[str, str] = {}
    if META_DIR.exists():
        for f in META_DIR.iterdir():
            if f.is_file() and f.suffix in (".json", ".md"):
                try:
                    meta_files[f.name] = f.read_text()[:3000]
                except OSError:
                    pass
    return meta_files


def _backup_meta_file(filename: str) -> Path | None:
    """Create a timestamped backup of a meta-parameter file."""
    src = META_DIR / filename
    if not src.exists():
        return None
    META_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = META_BACKUP_DIR / f"{filename}.{ts}.bak"
    try:
        shutil.copy2(src, dst)
        return dst
    except OSError:
        return None


def _restore_meta_file(filename: str, backup_path: Path) -> bool:
    """Restore a meta-parameter file from backup."""
    dst = META_DIR / filename
    try:
        shutil.copy2(backup_path, dst)
        return True
    except OSError:
        return False


# ── Core meta-evolution cycle ───────────────────────────────────────────────

def run_meta_evolution_cycle() -> dict[str, Any]:
    """Run one cycle of meta-evolution.

    1. Check rate limits and cooldown
    2. Measure current evolution effectiveness (baseline)
    3. Propose ONE change to ONE meta-parameter file
    4. Apply the meta-parameter change
    5. Run N standard evolution cycles with new parameter
    6. Measure effectiveness again
    7. Keep if improved on 3 consecutive cycles; revert otherwise

    Returns:
        Dict with cycle results: status, baseline, after, delta, file_changed, etc.
    """
    result: dict[str, Any] = {
        "status": "skipped",
        "timestamp": time.time(),
        "baseline": {},
        "after": {},
        "delta": 0.0,
        "file_changed": "",
        "promoted": False,
        "reason": "",
    }

    # Gate 1: Weekly rate limit
    weekly_count = _count_weekly_mutations()
    if weekly_count >= MAX_META_MUTATIONS_PER_WEEK:
        result["reason"] = f"Weekly limit reached ({weekly_count}/{MAX_META_MUTATIONS_PER_WEEK})"
        logger.info(f"meta_evolution: {result['reason']}")
        return result

    # Gate 2: Cooldown between cycles
    hours_since = _hours_since_last_cycle()
    if hours_since < META_COOLDOWN_HOURS:
        result["reason"] = f"Cooldown active ({hours_since:.1f}h < {META_COOLDOWN_HOURS}h)"
        logger.info(f"meta_evolution: {result['reason']}")
        return result

    # Gate 3: Must have enough evolution data to measure
    meta_files = _load_meta_files()
    if not meta_files:
        result["reason"] = "No meta-parameter files found in workspace/meta/"
        logger.info(f"meta_evolution: {result['reason']}")
        return result

    # Step 1: Measure baseline
    baseline = measure_evolution_effectiveness()
    result["baseline"] = baseline
    logger.info(f"meta_evolution: baseline effectiveness: {baseline}")

    if baseline["sample_size"] < 10:
        result["reason"] = f"Insufficient data ({baseline['sample_size']} < 10 experiments)"
        logger.info(f"meta_evolution: {result['reason']}")
        return result

    # Step 2: Propose a meta-mutation
    proposal = _propose_meta_mutation(baseline, meta_files)
    if not proposal:
        result["reason"] = "LLM failed to propose a valid meta-mutation"
        result["status"] = "error"
        return result

    target_file = proposal["file"]
    new_content = proposal["new_content"]
    result["file_changed"] = target_file

    # Step 3: Backup and apply
    backup = _backup_meta_file(target_file)
    if not backup and (META_DIR / target_file).exists():
        result["reason"] = "Failed to create backup"
        result["status"] = "error"
        return result

    try:
        target_path = META_DIR / target_file
        target_path.write_text(new_content)
        logger.info(f"meta_evolution: applied change to {target_file}")
    except OSError as e:
        result["reason"] = f"Failed to write meta-parameter: {e}"
        result["status"] = "error"
        return result

    # Step 4: Run standard evolution cycles to measure impact
    consecutive_improvements = 0
    try:
        from app.evolution import run_evolution_session
        for cycle_i in range(META_EVAL_EXPERIMENTS):
            logger.info(f"meta_evolution: running eval cycle {cycle_i + 1}/{META_EVAL_EXPERIMENTS}")

            # Run 2 evolution iterations per cycle
            run_evolution_session(max_iterations=2)

            # Measure effectiveness after this cycle
            current = measure_evolution_effectiveness(20)

            # Compare to baseline — consider it an improvement if:
            # kept_ratio is closer to target (0.30-0.50) OR avg_delta improved
            improved = _is_improvement(baseline, current)
            if improved:
                consecutive_improvements += 1
            else:
                consecutive_improvements = 0

            logger.info(
                f"meta_evolution: cycle {cycle_i + 1} — "
                f"kept_ratio={current['kept_ratio']:.2f}, "
                f"improved={improved}, "
                f"consecutive={consecutive_improvements}"
            )

            # Early promotion: 3 consecutive improvements
            if consecutive_improvements >= CONSECUTIVE_IMPROVEMENTS_TO_PROMOTE:
                break

    except Exception as e:
        logger.error(f"meta_evolution: eval cycles failed: {e}")
        # Revert on error
        if backup:
            _restore_meta_file(target_file, backup)
        result["status"] = "error"
        result["reason"] = f"Eval cycles failed: {e}"
        return result

    # Step 5: Measure final effectiveness
    after = measure_evolution_effectiveness()
    result["after"] = after
    result["status"] = "completed"

    # Step 6: Keep or revert
    if consecutive_improvements >= CONSECUTIVE_IMPROVEMENTS_TO_PROMOTE:
        result["promoted"] = True
        result["reason"] = f"Promoted after {consecutive_improvements} consecutive improvements"
        result["delta"] = after["kept_ratio"] - baseline["kept_ratio"]
        logger.info(f"meta_evolution: PROMOTED change to {target_file}")

        # Commit the change
        try:
            from app.workspace_versioning import workspace_commit
            workspace_commit(f"meta-evolution: improved {target_file}")
        except Exception:
            pass
    else:
        # Revert
        if backup:
            _restore_meta_file(target_file, backup)
            logger.info(f"meta_evolution: REVERTED change to {target_file}")
        result["reason"] = (
            f"Reverted — only {consecutive_improvements} consecutive improvements "
            f"(need {CONSECUTIVE_IMPROVEMENTS_TO_PROMOTE})"
        )

    # Record in history
    history = _load_history()
    history.append(result)
    # Keep last 100 entries
    if len(history) > 100:
        history = history[-100:]
    _save_history(history)

    return result


# ── Meta-mutation proposal ──────────────────────────────────────────────────

def _propose_meta_mutation(
    baseline: dict[str, float],
    meta_files: dict[str, str],
) -> dict[str, str] | None:
    """Propose a change to one meta-parameter file.

    Uses a premium LLM (architecture role) to analyze evolution effectiveness
    and suggest a targeted parameter change.

    Returns:
        Dict with 'file', 'change', 'new_content' on success; None on failure.
    """
    try:
        from app.llm_factory import create_specialist_llm
        llm = create_specialist_llm(max_tokens=2048, role="architecture")
    except Exception as e:
        logger.error(f"meta_evolution: failed to create LLM: {e}")
        return None

    prompt = _build_proposal_prompt(baseline, meta_files)

    try:
        raw = str(llm.call(prompt)).strip()

        # Parse JSON response
        import re
        # Find JSON block in response
        json_match = re.search(r'\{[^{}]*"file"[^{}]*\}', raw, re.DOTALL)
        if not json_match:
            # Try to find a larger JSON block
            json_match = re.search(r'\{.*?"file".*?"new_content".*?\}', raw, re.DOTALL)

        if not json_match:
            logger.warning(f"meta_evolution: no JSON in LLM response: {raw[:200]}")
            return None

        proposal = json.loads(json_match.group())

        # Validate proposal
        if "file" not in proposal or "new_content" not in proposal:
            logger.warning("meta_evolution: proposal missing required fields")
            return None

        target = proposal["file"]
        if target not in meta_files and not (META_DIR / target).exists():
            logger.warning(f"meta_evolution: proposed file {target} does not exist")
            return None

        # Validate EVOLVE-BLOCK constraints if the file uses them
        try:
            from app.evolve_blocks import validate_modification, has_evolve_blocks
            if target in meta_files and has_evolve_blocks(meta_files[target]):
                validation = validate_modification(meta_files[target], proposal["new_content"])
                if not validation.get("valid", True):
                    logger.warning(
                        f"meta_evolution: FREEZE-BLOCK violation in {target}: "
                        f"{validation.get('reason', '?')}"
                    )
                    return None
        except Exception:
            pass

        return proposal

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"meta_evolution: proposal failed: {e}")
        return None


def _build_proposal_prompt(
    baseline: dict[str, float],
    meta_files: dict[str, str],
) -> str:
    """Build the prompt for the meta-mutation proposal LLM."""
    files_section = ""
    for name, content in meta_files.items():
        files_section += f"\n--- {name} ---\n{content[:1500]}\n"

    return (
        "You are the META-EVOLUTION engine. Your job is to improve the evolution\n"
        "engine itself by modifying its parameters.\n\n"
        "## Current Evolution Effectiveness\n"
        f"  Kept ratio: {baseline['kept_ratio']:.2f} (target: 0.30-0.50)\n"
        f"  Avg delta of kept mutations: {baseline['avg_delta']:.6f}\n"
        f"  Hypothesis diversity: {baseline['diversity']:.2f} (target: >= 0.40)\n"
        f"  Code vs skill ratio: {baseline['code_ratio']:.2f} (target: >= 0.50)\n"
        f"  Sample size: {baseline['sample_size']} experiments\n\n"
        "## Interpretation Guide\n"
        "  - kept_ratio > 0.70: mutations too easy → TIGHTEN criteria/thresholds\n"
        "  - kept_ratio < 0.20: mutations too ambitious → SIMPLIFY, smaller steps\n"
        "  - code_ratio < 0.30: planning prompt isn't pushing enough code changes\n"
        "  - diversity < 0.30: strategies too repetitive → diversify prompts\n"
        "  - avg_delta near 0: improvements are marginal → change composite weights\n\n"
        "## Current Meta-Parameter Files\n"
        f"{files_section}\n\n"
        "## Instructions\n"
        "Propose ONE change to ONE meta-parameter file that would improve\n"
        "evolution effectiveness. The change should be:\n"
        "  1. Targeted — change one aspect, not everything\n"
        "  2. Measurable — should affect one of the metrics above\n"
        "  3. Reversible — don't delete content, modify it\n"
        "  4. FREEZE-BLOCK aware — do NOT modify FREEZE-BLOCK content\n\n"
        "Respond with ONLY a JSON object:\n"
        '{"file": "filename.ext", "change": "one-line description", '
        '"new_content": "full updated file content"}\n'
    )


# ── Improvement detection ───────────────────────────────────────────────────

def _is_improvement(baseline: dict[str, float], current: dict[str, float]) -> bool:
    """Determine if the current effectiveness is better than baseline.

    Uses a composite criterion:
      - kept_ratio closer to ideal range (0.30-0.50) is better
      - avg_delta higher is better
      - diversity higher is better
      - code_ratio higher is better (up to 0.80)
    """
    score_before = _effectiveness_score(baseline)
    score_after = _effectiveness_score(current)
    return score_after > score_before


def _effectiveness_score(metrics: dict[str, float]) -> float:
    """Compute a single effectiveness score from meta-metrics.

    The ideal evolution engine has:
      - kept_ratio around 0.40 (not too easy, not too hard)
      - high avg_delta (big improvements when mutations are kept)
      - high diversity (exploring varied improvements)
      - code_ratio around 0.60 (mostly code changes, some skills)
    """
    kr = metrics.get("kept_ratio", 0)
    # Penalize both too-high and too-low kept ratios
    # Optimal at 0.40; penalty grows quadratically away from target
    kr_score = 1.0 - 4.0 * (kr - 0.40) ** 2
    kr_score = max(0.0, kr_score)

    delta_score = min(1.0, metrics.get("avg_delta", 0) * 20)  # 0.05 → 1.0
    diversity_score = min(1.0, metrics.get("diversity", 0) * 2)  # 0.50 → 1.0

    cr = metrics.get("code_ratio", 0)
    cr_score = 1.0 - 2.0 * (cr - 0.60) ** 2
    cr_score = max(0.0, cr_score)

    return (
        0.35 * kr_score
        + 0.25 * delta_score
        + 0.20 * diversity_score
        + 0.20 * cr_score
    )


# ── Idle scheduler entry point ──────────────────────────────────────────────

def run_meta_evolution() -> None:
    """Entry point for the idle scheduler.

    Wraps run_meta_evolution_cycle() with error handling so it never
    crashes the idle loop.
    """
    try:
        result = run_meta_evolution_cycle()
        logger.info(
            f"meta_evolution: cycle complete — "
            f"status={result['status']}, promoted={result.get('promoted', False)}"
        )
    except Exception as e:
        logger.error(f"meta_evolution: unhandled error: {e}")
