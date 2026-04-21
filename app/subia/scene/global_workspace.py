"""
global_workspace.py — Global Workspace Theory (GWT) broadcast for AndrusAI.

Implements "global broadcast" — when important information arises,
it becomes available to ALL agents simultaneously (not just stored
in shared memory for optional pull).

Research: Butlin, Long, Chalmers (2025) — GWT identifies global broadcast
as a key consciousness indicator.

Architecture:
  - In-memory ring buffer (max 50 messages) for fast access
  - ALL broadcasts persisted to Postgres ``subia_broadcasts`` table
  - On singleton creation, the in-memory ring is hydrated from the most
    recent N rows of that table so broadcasts survive gateway restarts
    (previously every restart started the deque empty, which pinned the
    GWT probe at its 0.4 "no broadcasts, sent test" floor for up to an
    hour).
  - Agents check for unread broadcasts before each task
  - Broadcasts can only ADD caution (safety property)

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class BroadcastMessage:
    """A single broadcast to the global workspace."""
    content: str
    importance: str = "normal"       # normal | high | critical
    source_agent: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    read_by: set[str] = field(default_factory=set)
    broadcast_id: int = 0

    def to_dict(self) -> dict:
        return {
            "content": self.content[:500],
            "importance": self.importance,
            "source_agent": self.source_agent,
            "timestamp": self.timestamp,
            "broadcast_id": self.broadcast_id,
        }

@dataclass
class WorkspaceCandidate:
    """A candidate competing for workspace broadcast access (GWT bottleneck).

    Multiple signals compete per step; only the most salient 1-2 get broadcast.
    This implements the winner-take-all workspace bottleneck from Baars/Dehaene.
    """
    content: str
    salience: float       # [0, 1] — signal magnitude (ignition threshold: 0.3)
    signal_type: str      # certainty_shift | somatic_flip | trend_reversal | free_energy_spike | disposition
    source_agent: str = ""

class GlobalWorkspace:
    """GWT-inspired broadcast mechanism for cross-agent coordination."""

    _instance: "GlobalWorkspace" | None = None
    _lock = threading.Lock()

    # Hydrate this many recent broadcasts from Postgres on startup.  Keep
    # at / just under the deque's ``max_messages`` (50) so a fresh process
    # inherits a full working set immediately without repeated DB reads.
    _HYDRATE_LIMIT: int = 50

    def __init__(self, max_messages: int = 50):
        self._messages: deque[BroadcastMessage] = deque(maxlen=max_messages)
        self._counter = 0
        self._msg_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "GlobalWorkspace":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = cls()
                    inst._hydrate_from_db()
                    cls._instance = inst
        return cls._instance

    def _hydrate_from_db(self) -> None:
        """Load the most-recent ``_HYDRATE_LIMIT`` broadcasts from Postgres
        into the in-memory deque.

        Runs once when the singleton is created (process startup).  Rows
        are re-appended in chronological order so ``get_recent()``'s
        "take last N" slice continues to return the newest messages.

        The ``read_by`` set is not persisted — after a restart every
        agent will (correctly) see these rehydrated messages as unread
        on its next ``check_broadcasts()`` call.  That's intentional;
        pre-restart read-state wouldn't be reliable anyway (the new
        process serves different agent identities).
        """
        try:
            from app.control_plane.db import execute
            rows = execute(
                """
                SELECT content, importance, source_agent,
                       ts, broadcast_id
                  FROM subia_broadcasts
              ORDER BY ts DESC
                 LIMIT %s
                """,
                (self._HYDRATE_LIMIT,),
                fetch=True,
            )
            if not rows:
                return
            with self._msg_lock:
                # Re-insert oldest-first so the deque order (chronological,
                # newest at the right end) matches what ``get_recent(n)``
                # callers expect.
                for r in reversed(rows):
                    r = r if isinstance(r, dict) else {}
                    ts = r.get("ts")
                    ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    msg = BroadcastMessage(
                        content=r.get("content", "") or "",
                        importance=r.get("importance", "normal") or "normal",
                        source_agent=r.get("source_agent", "") or "",
                        timestamp=ts_iso,
                        broadcast_id=r.get("broadcast_id", 0) or 0,
                    )
                    self._messages.append(msg)
                # Resume the local id counter from the largest persisted
                # ``broadcast_id`` so new sends don't collide or regress.
                max_id = max(
                    (m.broadcast_id for m in self._messages if m.broadcast_id),
                    default=0,
                )
                self._counter = max_id
            logger.info(
                "GWT: hydrated %d broadcasts from DB (counter resumed at %d)",
                len(rows), self._counter,
            )
        except Exception:
            logger.debug("GWT: hydration skipped (non-fatal)", exc_info=True)

    def broadcast(
        self,
        content: str,
        importance: str = "normal",
        source_agent: str = "",
    ) -> BroadcastMessage:
        """Broadcast a message to the global workspace.

        All agents will see this in their next context injection.  Every
        broadcast — regardless of importance — is persisted to the
        ``subia_broadcasts`` table so the in-memory ring survives
        gateway restarts.  DB writes are best-effort; a failed insert
        never drops the in-memory broadcast.
        """
        with self._msg_lock:
            self._counter += 1
            msg = BroadcastMessage(
                content=content,
                importance=importance,
                source_agent=source_agent,
                broadcast_id=self._counter,
            )
            self._messages.append(msg)

        # Persist every broadcast (normal + high + critical) to the
        # dedicated ``subia_broadcasts`` table.  Rationale:
        #   * Having normal broadcasts persisted is what lets the GWT
        #     probe's baseline score survive a restart.
        #   * The prior design wrote only critical broadcasts, and wrote
        #     them into ``internal_states`` under a bespoke
        #     ``meta_strategy_assessment='critical_broadcast'`` string,
        #     which silently polluted the HOT-2 and INT probe readers
        #     (both filter on the same column).  The dedicated table
        #     keeps the two concerns separate.
        try:
            from app.control_plane.db import execute
            execute(
                """
                INSERT INTO subia_broadcasts
                    (source_agent, importance, content, broadcast_id)
                VALUES (%s, %s, %s, %s)
                """,
                (source_agent, importance, content[:5000], msg.broadcast_id),
            )
        except Exception:
            logger.debug("GWT: broadcast persist failed (non-fatal)",
                         exc_info=True)

        logger.info(f"GWT broadcast [{importance}] from {source_agent}: {content[:80]}")
        return msg

    def compete_for_broadcast(
        self, candidates: list[WorkspaceCandidate],
    ) -> list[BroadcastMessage]:
        """Winner-take-all workspace bottleneck (GWT core mechanism).

        Multiple signals compete for limited broadcast bandwidth.
        Only the most salient 1-2 candidates pass the ignition threshold
        and get broadcast to all agents.

        Args:
            candidates: List of competing signals with salience scores.

        Returns:
            List of broadcast messages (0-2 winners).
        """
        # Ignition threshold: salience must exceed 0.3 to enter workspace
        viable = [c for c in candidates if c.salience > 0.3]
        if not viable:
            return []

        viable.sort(key=lambda c: c.salience, reverse=True)

        # Bandwidth limit: top 1 normally, top 2 on critical ignition (salience > 0.8)
        winners = viable[:2] if viable[0].salience > 0.8 else viable[:1]

        results = []
        for w in winners:
            importance = (
                "critical" if w.salience > 0.7
                else ("high" if w.salience > 0.4 else "normal")
            )
            msg = self.broadcast(
                content=w.content,
                importance=importance,
                source_agent=w.source_agent,
            )
            results.append(msg)
        return results

    def check_broadcasts(
        self,
        agent_id: str,
        importance_filter: str = "high",
    ) -> list[BroadcastMessage]:
        """Return unread broadcasts for this agent at or above importance level.

        Marks returned messages as read by this agent.
        """
        importance_rank = {"normal": 0, "high": 1, "critical": 2}
        min_rank = importance_rank.get(importance_filter, 1)

        unread = []
        with self._msg_lock:
            for msg in self._messages:
                if agent_id not in msg.read_by:
                    if importance_rank.get(msg.importance, 0) >= min_rank:
                        unread.append(msg)
                        msg.read_by.add(agent_id)

        return unread

    def format_broadcasts(self, agent_id: str) -> str:
        """Format unread broadcasts for context injection."""
        unread = self.check_broadcasts(agent_id, importance_filter="high")
        if not unread:
            return ""

        lines = ["[Global Workspace Broadcasts]"]
        for msg in unread[-3:]:  # Max 3 broadcasts in context
            icon = "🔴" if msg.importance == "critical" else "🟡"
            lines.append(f"  {icon} [{msg.source_agent}] {msg.content[:200]}")
        return "\n".join(lines) + "\n"

    def get_recent(self, n: int = 10) -> list[dict]:
        """Return recent broadcasts for dashboard display."""
        with self._msg_lock:
            return [msg.to_dict() for msg in list(self._messages)[-n:]]

def get_workspace() -> GlobalWorkspace:
    return GlobalWorkspace.get_instance()

def broadcast(content: str, importance: str = "normal", source_agent: str = "") -> None:
    """Module-level convenience function for broadcasting."""
    get_workspace().broadcast(content, importance, source_agent)
