"""
trigger_scanner.py — Post-execution proactive trigger detection.

Scans crew results and system state for conditions that should trigger
unsolicited helpful actions.  Called synchronously from Commander.handle()
after each crew execution.

Implements ProAgent's anticipatory behavior (Zhang et al. AAAI 2024)
and Google's Sensible Agent proactive trigger architecture.
"""

import json
import logging
from datetime import datetime, timezone

from app.memory.chromadb_manager import retrieve_with_metadata, retrieve
from app.memory.belief_state import get_beliefs
from app.memory.scoped_memory import store_scoped

logger = logging.getLogger(__name__)

SELF_REPORTS_COLLECTION = "self_reports"


def scan_for_triggers(
    crew_results: dict,
    task_description: str,
) -> list[dict]:
    """Scan for conditions that should trigger proactive intervention.

    Args:
        crew_results: Dict with 'result' (str) and 'crews' (list of crew names)
        task_description: The original user request

    Returns:
        List of trigger dicts: {"trigger_type": str, "description": str, "suggested_action": str, "role": str}
    """
    triggers = []
    result_text = crew_results.get("result", "")
    crew_name = crew_results.get("crews", "")
    # Normalize crew_name to a single role
    role = crew_name.split(",")[0].strip().replace("_", "") if crew_name else ""
    role_map = {"research": "researcher", "coding": "coder", "writing": "writer",
                "media": "critic", "selfimprovement": "commander"}
    role = role_map.get(role, role)

    # Pattern 1: Low confidence in recent self-reports
    try:
        low_conf_trigger = _check_low_confidence()
        if low_conf_trigger:
            low_conf_trigger["role"] = role
            triggers.append(low_conf_trigger)
    except Exception:
        logger.debug("Low confidence check failed", exc_info=True)

    # Pattern 2: Unfulfilled team needs
    try:
        needs_trigger = _check_unfulfilled_needs()
        if needs_trigger:
            needs_trigger["role"] = role
            triggers.append(needs_trigger)
    except Exception:
        logger.debug("Unfulfilled needs check failed", exc_info=True)

    # Pattern 3: Quality drift (confidence trending down)
    try:
        drift_trigger = _check_quality_drift()
        if drift_trigger:
            drift_trigger["role"] = role
            triggers.append(drift_trigger)
    except Exception:
        logger.debug("Quality drift check failed", exc_info=True)

    # Pattern 4: Role-specific content triggers from proactive_behaviors.py
    try:
        content_triggers = _check_content_triggers(result_text, role)
        triggers.extend(content_triggers)
    except Exception:
        logger.debug("Content trigger check failed", exc_info=True)

    return triggers


def _check_content_triggers(result_text: str, role: str) -> list[dict]:
    """Detect role-specific triggers by analyzing crew output content."""
    import re
    triggers = []
    text_lower = result_text.lower() if result_text else ""

    if not text_lower:
        return []

    # Contradiction detection (all roles, especially researcher)
    contradiction_signals = [
        r"however.*contradicts", r"on the other hand.*disagrees",
        r"conflicting.*sources", r"inconsistent.*with",
        r"some sources say.*while others",
    ]
    for pattern in contradiction_signals:
        if re.search(pattern, text_lower):
            triggers.append({
                "trigger_type": "contradictory_sources",
                "role": role or "researcher",
                "description": "Contradictory information detected in output",
                "suggested_action": "",  # Will be filled from behaviors
            })
            break

    # Security concern detection (coder)
    security_signals = [
        r"eval\(", r"exec\(", r"subprocess\.call.*shell\s*=\s*True",
        r"sql injection", r"xss", r"unsanitized",
        r"hardcoded.*password", r"hardcoded.*secret",
    ]
    for pattern in security_signals:
        if re.search(pattern, text_lower):
            triggers.append({
                "trigger_type": "security_concern",
                "role": "coder",
                "description": f"Potential security issue detected in code output",
                "suggested_action": "",
            })
            break

    # Test failure detection (coder)
    if any(s in text_lower for s in ["test failed", "assertion error", "traceback", "failures=", "errors="]):
        triggers.append({
            "trigger_type": "test_failure",
            "role": "coder",
            "description": "Test failures detected in code output",
            "suggested_action": "",
        })

    # Ambiguity/incompleteness detection (all roles)
    if any(s in text_lower for s in ["i'm not sure", "unclear", "ambiguous", "insufficient information", "could not determine"]):
        triggers.append({
            "trigger_type": "low_confidence",
            "role": role,
            "description": "Agent expressed uncertainty in output",
            "suggested_action": "",
        })

    return triggers[:3]  # Cap at 3 content triggers


def _check_low_confidence() -> dict | None:
    """Check recent self-reports for low confidence."""
    items = retrieve_with_metadata(SELF_REPORTS_COLLECTION, "low confidence", n=5)
    if not items:
        return None

    now = datetime.now(timezone.utc)
    recent_low = []
    for item in items:
        meta = item.get("metadata", {})
        if meta.get("confidence") != "low":
            continue
        # Only consider reports from the last hour
        ts_str = meta.get("ts", "")
        if ts_str:
            try:
                report_time = datetime.fromisoformat(ts_str)
                age_hours = (now - report_time).total_seconds() / 3600
                if age_hours > 1.0:
                    continue
            except (ValueError, TypeError):
                continue
        recent_low.append(item)

    if not recent_low:
        return None

    # Extract details from the most recent low-confidence report
    try:
        report = json.loads(recent_low[0]["document"])
        role = report.get("role", "unknown")
        task = report.get("task_summary", "")[:80]
        blockers = report.get("blockers", "")
    except (json.JSONDecodeError, KeyError):
        role, task, blockers = "unknown", "", ""

    return {
        "trigger_type": "low_confidence",
        "description": (
            f"{role} reported low confidence on: {task}. "
            f"Blockers: {blockers}" if blockers else f"{role} reported low confidence on: {task}"
        ),
        "suggested_action": (
            f"Consider verifying the {role}'s output with additional sources "
            f"or assigning a follow-up task to address the reported blockers."
        ),
    }


def _check_unfulfilled_needs() -> dict | None:
    """Check if any agent has unmet needs from teammates."""
    beliefs = get_beliefs()
    if not beliefs:
        return None

    for belief in beliefs:
        needs = belief.get("needs", [])
        state = belief.get("state", "")
        agent = belief.get("agent", "")

        if needs and state in ("working", "blocked"):
            return {
                "trigger_type": "unfulfilled_needs",
                "description": (
                    f"{agent} needs: {', '.join(needs[:3])} "
                    f"(currently {state})"
                ),
                "suggested_action": (
                    f"Address {agent}'s needs by dispatching the appropriate "
                    f"crew or storing the needed information in team memory."
                ),
            }

    return None


def _check_quality_drift() -> dict | None:
    """Check if confidence is trending downward compared to recent history."""
    items = retrieve_with_metadata(SELF_REPORTS_COLLECTION, "confidence assessment", n=15)
    if len(items) < 5:
        return None  # Not enough data

    # Map confidence to numeric values
    conf_map = {"high": 3, "medium": 2, "low": 1}

    # Sort by timestamp (most recent first)
    sorted_items = []
    for item in items:
        meta = item.get("metadata", {})
        conf = meta.get("confidence", "medium")
        ts = meta.get("ts", "")
        sorted_items.append((ts, conf_map.get(conf, 2)))

    sorted_items.sort(key=lambda x: x[0], reverse=True)

    if len(sorted_items) < 6:
        return None

    # Compare recent 3 vs previous 3
    recent_avg = sum(s[1] for s in sorted_items[:3]) / 3
    previous_avg = sum(s[1] for s in sorted_items[3:6]) / 3

    # Significant drop: recent average is more than 0.5 lower
    if previous_avg - recent_avg >= 0.5:
        return {
            "trigger_type": "quality_drift",
            "description": (
                f"Confidence trending down: recent avg {recent_avg:.1f}/3 "
                f"vs previous avg {previous_avg:.1f}/3"
            ),
            "suggested_action": (
                "Quality may be declining. Consider reviewing recent outputs "
                "more carefully or running a retrospective analysis."
            ),
        }

    return None


def execute_proactive_action(trigger: dict, original_result: str) -> str | None:
    """Execute a proactive response to a detected trigger.

    Uses role-specific action prompts from proactive_behaviors.py when available,
    falls back to generic actions otherwise.

    Args:
        trigger: Trigger dict from scan_for_triggers()
        original_result: The crew's output that triggered this

    Returns:
        A note to append to the result, or None if no action taken.
    """
    trigger_type = trigger.get("trigger_type", "")
    description = trigger.get("description", "")
    role = trigger.get("role", "")

    # Get role-specific action from proactive_behaviors.py
    action = trigger.get("suggested_action", "")
    if not action:
        try:
            from app.proactive.proactive_behaviors import get_proactive_prompt
            action = get_proactive_prompt(role, trigger_type)
        except Exception:
            pass

    # Fall back to generic actions if no role-specific one
    if not action:
        _GENERIC_ACTIONS = {
            "low_confidence": "Verify output with additional sources or assign a follow-up task.",
            "unfulfilled_needs": "Address the team need by dispatching the appropriate crew.",
            "quality_drift": "Review recent outputs and consider running a retrospective.",
            "contradictory_sources": "Flag the contradiction. Search for additional sources to resolve.",
            "security_concern": "Flag the security issue. Suggest specific mitigation steps.",
            "test_failure": "Run additional targeted tests. Check edge cases and error handling.",
        }
        action = _GENERIC_ACTIONS.get(trigger_type, f"Review the {trigger_type} condition.")

    # Store in team memory for future reference
    store_scoped(
        "scope_team",
        f"[PROACTIVE:{trigger_type}] {description}. Action: {action}",
        importance="high",
    )

    logger.info(f"Proactive action executed: {trigger_type} for {role} — {action[:80]}")
    return f"[{trigger_type}] {description}"
