"""
evolution.py — Continuous autonomous improvement loop.

Inspired by Karpathy's autoresearch: the system experiments on itself,
measures results, keeps improvements, discards regressions.

Principles applied:
  1. LOOP FOREVER — run on cron, never stop evolving
  2. FIXED METRIC — task success rate + avg response quality
  3. EXPERIMENT → MEASURE → KEEP/DISCARD — propose change, test, evaluate
  4. SINGLE MUTATION — one change at a time for clean attribution
  5. LOG EVERYTHING — experiment journal with full results
  6. ADVANCE ON IMPROVEMENT — auto-apply skills that help; queue code changes
  7. SIMPLICITY — prefer removing complexity over adding it

Experiment journal: /app/workspace/evolution_journal.json
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from crewai import Agent, Task, Crew, Process, LLM
from app.config import get_settings, get_anthropic_api_key
from app.tools.web_search import web_search
from app.tools.memory_tool import create_memory_tools
from app.tools.file_manager import file_manager
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.proposals import create_proposal, list_proposals
from app.self_heal import get_error_patterns, get_recent_errors

logger = logging.getLogger(__name__)
settings = get_settings()

JOURNAL_PATH = Path("/app/workspace/evolution_journal.json")
SKILLS_DIR = Path("/app/workspace/skills")


# ── Experiment journal ────────────────────────────────────────────────────────

def _load_journal() -> list[dict]:
    try:
        if JOURNAL_PATH.exists():
            return json.loads(JOURNAL_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_journal(entries: list[dict]) -> None:
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        JOURNAL_PATH.write_text(json.dumps(entries[-200:], indent=2))
    except OSError:
        logger.warning("Failed to write evolution journal", exc_info=True)


def log_experiment(
    hypothesis: str,
    change_type: str,
    result: str,
    status: str,
    metric_before: str = "",
    metric_after: str = "",
) -> None:
    """Record an experiment in the journal."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hypothesis": hypothesis[:300],
        "type": change_type,
        "result": result[:500],
        "status": status,  # "keep", "discard", "crash"
        "metric_before": metric_before,
        "metric_after": metric_after,
    }
    journal = _load_journal()
    journal.append(entry)
    _save_journal(journal)


def get_journal_summary(n: int = 10) -> str:
    """Return recent experiments as formatted text."""
    journal = _load_journal()[-n:]
    if not journal:
        return "No experiments recorded yet."
    lines = []
    for e in journal:
        lines.append(
            f"[{e['ts'][:10]}] {e['status'].upper()}: {e['hypothesis'][:80]} "
            f"({e['type']})"
        )
    return "\n".join(lines)


# ── System metrics ────────────────────────────────────────────────────────────

def _gather_metrics() -> dict:
    """Collect current system health metrics for the evolution agent."""
    errors = get_recent_errors(20)
    patterns = get_error_patterns()

    # Count skills
    skill_count = 0
    skill_names = []
    if SKILLS_DIR.exists():
        for f in sorted(SKILLS_DIR.glob("*.md")):
            if f.name != "learning_queue.md":
                skill_count += 1
                skill_names.append(f.stem)

    # Count proposals
    pending_proposals = len(list_proposals("pending"))
    approved_proposals = len(list_proposals("approved"))

    # Error rate (last 20 entries)
    total_errors = len(errors)
    diagnosed = sum(1 for e in errors if e.get("diagnosed"))
    undiagnosed = total_errors - diagnosed

    # Previous experiments
    journal = _load_journal()[-20:]
    kept = sum(1 for e in journal if e.get("status") == "keep")
    discarded = sum(1 for e in journal if e.get("status") == "discard")

    return {
        "skill_count": skill_count,
        "skill_names": skill_names[:20],
        "pending_proposals": pending_proposals,
        "approved_proposals": approved_proposals,
        "total_errors_recent": total_errors,
        "undiagnosed_errors": undiagnosed,
        "error_patterns": dict(list(patterns.items())[:10]),
        "experiments_kept": kept,
        "experiments_discarded": discarded,
        "experiment_history": [
            f"{e['status']}: {e['hypothesis'][:60]}" for e in journal[-5:]
        ],
    }


# ── Evolution loop ────────────────────────────────────────────────────────────

def run_evolution_cycle() -> str:
    """
    One cycle of the evolution loop. Called by cron or manually.

    The cycle:
    1. Gather system metrics (errors, skills, experiment history)
    2. Agent analyzes the state and proposes ONE improvement
    3. If it's a skill: apply immediately, log as "keep"
    4. If it's code: create proposal for user approval, log as "pending"
    5. Return summary of what was done
    """
    task_id = crew_started("self_improvement", "Evolution cycle", eta_seconds=120)

    try:
        metrics = _gather_metrics()
        result = _run_evolution_agent(metrics)
        crew_completed("self_improvement", task_id, result[:200])
        return result
    except Exception as exc:
        crew_failed("self_improvement", task_id, str(exc)[:200])
        logger.error(f"Evolution cycle failed: {exc}")
        return f"Evolution cycle failed: {str(exc)[:200]}"


def _run_evolution_agent(metrics: dict) -> str:
    """Spawn the evolution agent to propose and execute one improvement."""
    llm = LLM(
        model=f"anthropic/{settings.specialist_model}",
        api_key=get_anthropic_api_key(),
        max_tokens=4096,
    )
    memory_tools = create_memory_tools(collection="skills")

    metrics_text = json.dumps(metrics, indent=2)

    # Build context from recent experiment history
    recent_experiments = get_journal_summary(10)

    agent = Agent(
        role="Evolution Engineer",
        goal="Make one small, measurable improvement to the agent team per cycle.",
        backstory=(
            "You are the evolution engine of an autonomous AI agent team. "
            "Like Karpathy's autoresearch, you experiment on the system itself: "
            "propose ONE change, measure the impact, keep or discard. "
            "You follow these principles:\n"
            "1. ONE CHANGE per cycle — single mutation for clean attribution\n"
            "2. SIMPLICITY — prefer removing complexity over adding it\n"
            "3. MEASURE — every change must be testable\n"
            "4. ADVANCE ON IMPROVEMENT — keep what helps, discard what doesn't\n"
            "5. LEARN FROM FAILURES — error patterns reveal what to fix\n"
            "6. NEVER REPEAT — check experiment history before proposing"
        ),
        llm=llm,
        tools=[web_search, file_manager] + memory_tools,
        verbose=False,
    )

    task = Task(
        description=(
            f"You are running one evolution cycle. Analyze the system state and "
            f"propose ONE improvement.\n\n"
            f"## Current System Metrics\n{metrics_text}\n\n"
            f"## Recent Experiments\n{recent_experiments}\n\n"
            f"## Your Task\n"
            f"1. Identify the HIGHEST-IMPACT improvement opportunity:\n"
            f"   - Recurring errors → fix the root cause\n"
            f"   - Missing skills → research and create them\n"
            f"   - Capability gaps → propose new tools\n"
            f"   - Inefficiencies → simplify or optimize\n\n"
            f"2. Execute ONE of these actions:\n\n"
            f"   a) SKILL FIX (immediate): Research a topic and save a skill file "
            f"using file_manager (action 'write', path 'skills/<name>.md'). "
            f"Also store a summary in shared team memory. Then respond with:\n"
            f'   {{"action": "skill", "hypothesis": "what you improved", '
            f'"file": "skills/<name>.md"}}\n\n'
            f"   b) CODE PROPOSAL (needs approval): Respond with:\n"
            f'   {{"action": "code", "hypothesis": "what to change and why", '
            f'"title": "short title", "description": "detailed description", '
            f'"files": {{"path/to/file.py": "file content"}}}}\n\n'
            f"3. DO NOT repeat experiments from the history above.\n"
            f"4. Prefer skill fixes over code changes (they apply immediately).\n"
            f"5. If the system is healthy and no obvious improvements exist, "
            f"research an advanced topic to expand team capabilities.\n\n"
            f"Reply with ONLY the JSON object."
        ),
        expected_output="A JSON object describing the improvement action taken.",
        agent=agent,
    )

    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    raw = str(crew.kickoff()).strip()

    # Parse result
    raw_clean = re.sub(r'^```(?:json)?\s*', '', raw)
    raw_clean = re.sub(r'\s*```$', '', raw_clean)

    try:
        result = json.loads(raw_clean)
    except json.JSONDecodeError:
        log_experiment("unparseable response", "unknown", raw[:200], "crash")
        return f"Evolution agent returned unparseable result: {raw[:100]}"

    action = result.get("action", "")
    hypothesis = result.get("hypothesis", "unknown")

    if action == "skill":
        # Skill was already saved by the agent via file_manager tool
        log_experiment(hypothesis, "skill", f"Saved: {result.get('file', '?')}", "keep")
        return f"Evolution: applied skill fix — {hypothesis}"

    elif action == "code":
        # Create a proposal for user approval
        pid = create_proposal(
            title=result.get("title", hypothesis)[:100],
            description=result.get("description", hypothesis)[:2000],
            proposal_type="code",
            files=result.get("files") if isinstance(result.get("files"), dict) else None,
        )
        log_experiment(hypothesis, "code", f"Proposal #{pid}", "pending")
        return f"Evolution: created code proposal #{pid} — {hypothesis}"

    else:
        log_experiment(hypothesis, action or "unknown", str(result)[:200], "discard")
        return f"Evolution: {hypothesis} (no action taken)"
