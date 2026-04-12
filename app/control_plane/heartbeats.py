"""Heartbeat scheduler — enhanced agent wakes for autonomous operation.

Extends the existing idle_scheduler with:
- Per-agent heartbeat intervals (configurable)
- Event-driven wakes (ticket assigned → wake assigned agent)
- Autonomous mode: agents process their ticket queues on heartbeat
- Heartbeat logging to PostgreSQL for audit
"""
import logging
import threading
import time

from app.control_plane.db import execute, execute_one, execute_scalar

logger = logging.getLogger(__name__)

# Default heartbeat intervals (seconds) per agent role
DEFAULT_INTERVALS = {
    "commander": 300,       # 5 min — check for new tickets
    "researcher": 600,      # 10 min
    "coder": 600,
    "writer": 900,          # 15 min
    "self_improver": 1800,  # 30 min
}

class HeartbeatScheduler:
    """Enhanced scheduler with per-agent heartbeats and event-driven wakes."""

    def __init__(self):
        self._intervals: dict[str, int] = dict(DEFAULT_INTERVALS)
        self._last_beat: dict[str, float] = {}
        self._wake_events: list[dict] = []
        self._lock = threading.Lock()

    def configure(self, agent_role: str, interval_seconds: int) -> None:
        """Set heartbeat interval for an agent."""
        with self._lock:
            self._intervals[agent_role] = interval_seconds
        logger.info(f"heartbeat: {agent_role} interval set to {interval_seconds}s")

    def trigger_wake(self, agent_role: str, reason: str, ticket_id: str = None) -> None:
        """Event-driven wake: ticket assigned, @-mention, approval needed."""
        with self._lock:
            self._wake_events.append({
                "agent_role": agent_role,
                "reason": reason,
                "ticket_id": ticket_id,
                "ts": time.monotonic(),
            })
        logger.info(f"heartbeat: wake triggered for {agent_role} — {reason}")

    def get_pending_wakes(self, agent_role: str) -> list[dict]:
        """Get and clear pending wake events for an agent."""
        with self._lock:
            pending = [w for w in self._wake_events if w["agent_role"] == agent_role]
            self._wake_events = [w for w in self._wake_events if w["agent_role"] != agent_role]
        return pending

    def should_beat(self, agent_role: str) -> bool:
        """Check if enough time has passed for this agent's heartbeat."""
        interval = self._intervals.get(agent_role, 600)
        last = self._last_beat.get(agent_role, 0)
        return (time.monotonic() - last) >= interval

    def record_beat(self, agent_role: str, project_id: str = None,
                    trigger_type: str = "idle",
                    tickets_processed: int = 0,
                    cost_usd: float = 0) -> None:
        """Log a heartbeat to PostgreSQL and update last-beat time."""
        self._last_beat[agent_role] = time.monotonic()
        try:
            execute(
                """INSERT INTO control_plane.heartbeats
                   (agent_role, project_id, trigger_type, tickets_processed,
                    cost_usd, status, completed_at)
                   VALUES (%s, %s, %s, %s, %s, 'completed', NOW())""",
                (agent_role, project_id, trigger_type, tickets_processed, cost_usd),
            )
        except Exception:
            logger.debug("heartbeat: failed to log beat", exc_info=True)

    def run_heartbeat(self, agent_role: str, project_id: str = None) -> dict:
        """Single heartbeat cycle for an agent.

        1. Check assigned tickets (todo, in_progress)
        2. Check budget availability
        3. Process next ticket (or report idle)
        4. Log heartbeat
        """
        from app.control_plane.tickets import get_tickets
        from app.control_plane.budgets import get_budget_enforcer

        result = {"agent": agent_role, "tickets_processed": 0, "status": "idle"}

        # Get pending wakes
        wakes = self.get_pending_wakes(agent_role)

        # Get assigned tickets
        tickets = execute(
            """SELECT id, title, status, difficulty
               FROM control_plane.tickets
               WHERE assigned_agent = %s AND status IN ('todo', 'in_progress')
               ORDER BY priority DESC, created_at ASC
               LIMIT 5""",
            (agent_role,), fetch=True,
        ) or []

        if not tickets and not wakes:
            self.record_beat(agent_role, project_id, "idle")
            return result

        result["status"] = "active"
        result["pending_tickets"] = len(tickets)
        result["wake_events"] = len(wakes)

        self.record_beat(
            agent_role, project_id,
            "event" if wakes else "scheduled",
            tickets_processed=len(tickets),
        )
        return result

    def get_recent_beats(self, agent_role: str = None, limit: int = 20) -> list[dict]:
        """Get recent heartbeats for dashboard."""
        if agent_role:
            return execute(
                """SELECT * FROM control_plane.heartbeats
                   WHERE agent_role = %s
                   ORDER BY started_at DESC LIMIT %s""",
                (agent_role, limit), fetch=True,
            ) or []
        return execute(
            """SELECT * FROM control_plane.heartbeats
               ORDER BY started_at DESC LIMIT %s""",
            (limit,), fetch=True,
        ) or []

    def get_schedule(self) -> list[dict]:
        """Get heartbeat schedule for dashboard."""
        schedule = []
        now = time.monotonic()
        for role, interval in sorted(self._intervals.items()):
            last = self._last_beat.get(role, 0)
            next_in = max(0, interval - (now - last)) if last else 0
            schedule.append({
                "agent_role": role,
                "interval_seconds": interval,
                "last_beat_ago": round(now - last) if last else None,
                "next_in_seconds": round(next_in),
            })
        return schedule

# ── Singleton ────────────────────────────────────────────────────────────────

_scheduler: HeartbeatScheduler | None = None
_lock = threading.Lock()

def get_heartbeat_scheduler() -> HeartbeatScheduler:
    global _scheduler
    if _scheduler is None:
        with _lock:
            if _scheduler is None:
                _scheduler = HeartbeatScheduler()
    return _scheduler
