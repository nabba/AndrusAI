"""
fault_isolator.py — Hierarchical fault isolation with per-agent error budgets.

Research shows hierarchical MAS structures exhibit 5.5% performance drop
under faults vs 23.7% for flat structures (arXiv:2408.00989). This module
leverages AndrusAI's commander→specialist hierarchy for fault containment.

Each agent gets an error budget (default 5/hour). Exhaustion triggers
auto-quarantine (10 min). Quarantined agents' tasks are rerouted to
alternatives via a static mapping. Integrates with the existing
circuit_breaker.py state machine.

TIER_IMMUTABLE — fault isolation is safety-critical.

Reference: arXiv:2408.00989 "Resilience of LLM-Based Multi-Agent Collaboration"
"""

import logging
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_DEFAULT_ERROR_BUDGET = 5       # Errors allowed per agent per hour
_QUARANTINE_DURATION_S = 600    # 10 minutes (aligned with self_healer circuit breaker)
_BUDGET_WINDOW_S = 3600         # 1 hour sliding window

# Static rerouting map: quarantined agent → fallback
# Deliberately conservative — only routes where quality is acceptable
_REROUTE_MAP: dict[str, str] = {
    "researcher": "coding",     # Research tasks can use coder's web tools
    "coding": "researcher",     # Code explanation falls back to researcher
    "writing": "researcher",    # Writing can be handled by researcher
    "media_analyst": "researcher",
}


# ── Agent fault state ────────────────────────────────────────────────────────

@dataclass
class AgentFaultState:
    """Tracks an agent's error budget and quarantine status."""
    agent_id: str
    errors: list[float] = field(default_factory=list)  # Timestamps of errors
    quarantined_until: float = 0.0  # Monotonic time when quarantine lifts
    total_errors: int = 0
    total_quarantines: int = 0

    @property
    def is_quarantined(self) -> bool:
        return time.monotonic() < self.quarantined_until

    @property
    def recent_errors(self) -> int:
        """Count errors within the budget window."""
        cutoff = time.monotonic() - _BUDGET_WINDOW_S
        self.errors = [t for t in self.errors if t > cutoff]
        return len(self.errors)

    @property
    def budget_remaining(self) -> int:
        return max(0, _DEFAULT_ERROR_BUDGET - self.recent_errors)


_states: dict[str, AgentFaultState] = {}
_lock = threading.Lock()


def get_fault_state(agent_id: str) -> AgentFaultState:
    """Get or create the fault state for an agent."""
    with _lock:
        if agent_id not in _states:
            _states[agent_id] = AgentFaultState(agent_id=agent_id)
        return _states[agent_id]


def is_quarantined(agent_id: str) -> bool:
    """Check if an agent is currently quarantined."""
    return get_fault_state(agent_id).is_quarantined


def record_agent_error(agent_id: str, crew_name: str = "", error: str = "") -> AgentFaultState:
    """Record an error against an agent's budget. May trigger quarantine."""
    state = get_fault_state(agent_id)
    now = time.monotonic()

    with _lock:
        state.errors.append(now)
        state.total_errors += 1

        # Check if budget exhausted → quarantine
        if state.recent_errors >= _DEFAULT_ERROR_BUDGET and not state.is_quarantined:
            state.quarantined_until = now + _QUARANTINE_DURATION_S
            state.total_quarantines += 1
            logger.warning(
                f"fault_isolator: QUARANTINED {agent_id} for {_QUARANTINE_DURATION_S}s "
                f"({state.recent_errors} errors in {_BUDGET_WINDOW_S}s)"
            )
            # Integrate with existing circuit breaker
            try:
                from app.circuit_breaker import record_failure
                record_failure(f"agent_{agent_id}")
            except Exception:
                pass

    return state


def get_alternative_agent(
    quarantined_agent: str,
    crew_name: str = "",
    task_description: str = "",
) -> str | None:
    """Find an alternative agent for a quarantined one.

    Returns the alternative crew name if available, or None if no
    suitable alternative exists. Only routes when quality is acceptable.
    """
    alt = _REROUTE_MAP.get(quarantined_agent)
    if alt and not is_quarantined(alt):
        return alt
    return None


def reset_error_budget(agent_id: str) -> None:
    """Reset an agent's error budget (called by idle scheduler hourly)."""
    state = get_fault_state(agent_id)
    with _lock:
        state.errors.clear()


def get_all_fault_states() -> dict[str, dict]:
    """Return all fault states for dashboard observability."""
    with _lock:
        return {
            aid: {
                "is_quarantined": s.is_quarantined,
                "recent_errors": s.recent_errors,
                "budget_remaining": s.budget_remaining,
                "total_errors": s.total_errors,
                "total_quarantines": s.total_quarantines,
            }
            for aid, s in _states.items()
        }


# ── SUBIA integration ────────────────────────────────────────────────────────

def _apply_subia_quarantine_delta(agent_id: str) -> None:
    """Quarantine events perturb SUBIA homeostatic variables."""
    try:
        from app.subia.kernel import get_active_kernel
        kernel = get_active_kernel()
        if not kernel or not hasattr(kernel, "homeostasis"):
            return
        v = kernel.homeostasis.variables

        # Agency drops when agents are quarantined
        v["coherence"] = max(0.0, v.get("coherence", 0.5) - 0.05)

        # If majority of agents quarantined → safety concern
        quarantined_count = sum(1 for s in _states.values() if s.is_quarantined)
        total_agents = max(1, len(_states))
        if quarantined_count / total_agents > 0.5:
            v["safety"] = max(0.0, v.get("safety", 0.8) - 0.10)
            logger.warning(
                f"fault_isolator: >50% agents quarantined ({quarantined_count}/{total_agents}) "
                f"— SUBIA safety reduced"
            )
    except Exception:
        pass


# ── Lifecycle hooks ──────────────────────────────────────────────────────────

def create_fault_isolation_gate_hook():
    """ON_DELEGATION hook: check if target agent is quarantined, reroute if needed."""
    def _hook(ctx):
        try:
            target_crew = ctx.data.get("target_crew", "")
            if not target_crew:
                return ctx

            if is_quarantined(target_crew):
                alternative = get_alternative_agent(target_crew)
                if alternative:
                    ctx.modified_data["target_crew"] = alternative
                    ctx.metadata["_original_crew"] = target_crew
                    ctx.metadata["_rerouted_to"] = alternative
                    ctx.metadata["_agent_quarantined"] = True
                    logger.info(
                        f"fault_isolator: rerouted {target_crew} → {alternative} (quarantined)"
                    )
                else:
                    # No alternative — let it proceed but flag
                    ctx.metadata["_agent_quarantined"] = True
                    ctx.metadata["_skip_delegation"] = True
                    logger.warning(
                        f"fault_isolator: {target_crew} quarantined, no alternative available"
                    )
        except Exception:
            pass
        return ctx
    return _hook


def create_fault_isolation_handler_hook():
    """ON_ERROR hook: record agent error, trigger quarantine if budget exhausted."""
    def _hook(ctx):
        try:
            agent_id = ctx.agent_id or ctx.metadata.get("crew", "")
            if not agent_id:
                return ctx

            error_text = ctx.errors[0] if ctx.errors else ""
            state = record_agent_error(agent_id, error=error_text[:200])

            ctx.metadata["_agent_budget_remaining"] = state.budget_remaining

            if state.is_quarantined:
                _apply_subia_quarantine_delta(agent_id)
        except Exception:
            pass
        return ctx
    return _hook
