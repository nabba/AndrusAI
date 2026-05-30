"""Autonomous executor — typed state machine for `/delegate <goal>` runs.

Mirrors the threads + workflows + change_requests pattern: dataclasses
+ enum + terminal-state guard + JSON-friendly serialisation.

State machine
─────────────

    PENDING_APPROVAL ──→ CREATED   (operator 👍 approves; 👎 / 7-day expiry ──→ ABORTED)
    CREATED ──→ PLANNING ──→ RUNNING ──┬──→ COMPLETED  ← terminal
       │                       │       │
       │                       │       ├──→ FAILED      ← terminal
       │                       │       │
       │                       │       └──→ BUDGET_EXHAUSTED ← terminal
       │                       │
       │                       ├──→ BLOCKED ──→ RUNNING  (operator unblocks)
       │                       │       ↓
       │                       │     ABORTED ← terminal
       │                       │
       │                       └──→ PAUSED ──→ RUNNING  (operator resumes)
       │                              ↓
       │                            ABORTED  ← terminal
       │
       └──→ ABORTED  ← terminal (cancelled before starting)

Terminal states are immutable. Any further transition attempt raises
:class:`InvalidExecutorTransition`. This invariant is the load-bearing
safety property the autonomous executor depends on — once a run has
booked its budget consumption and emitted its final result, the record
must not silently change.

Budget tracking is per-run and is the OTHER safety property: every
step consumes USD + tokens + wall-clock seconds; exceeding any cap
forces a transition to BUDGET_EXHAUSTED. The budget itself is set at
run-creation time and cannot be raised post-creation — operator must
abort + re-create with a higher cap.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _emit_milestone_safe(
    *,
    run_id: str,
    from_status: "ExecutorStatus",
    to_status: "ExecutorStatus",
    reason: str = "",
    goal_preview: str = "",
) -> None:
    """Emit ``executor_milestone`` to the identity continuity ledger.

    Failure-isolated end-to-end — ledger write errors are silently
    swallowed so a transient I/O issue never blocks a legitimate run
    transition. Lazy-imports the ledger module so this file can be
    imported in stripped test environments without the ledger
    machinery present.

    Verified Implementation Plan Gap #3 (2026-05-22): annual
    reflection's :func:`summarise_drift` Counter auto-surfaces this
    kind ("executor_milestone: N") via its dynamic group-by, so
    delegate-and-forget activity becomes visible in the identity-
    drift narrative without bespoke instrumentation.
    """
    try:
        import importlib
        cl = importlib.import_module("app.identity.continuity_ledger")
        cl.record_event(
            kind="executor_milestone",
            actor="autonomous_executor",
            summary=(
                f"{run_id}: {from_status.value} → {to_status.value}"
                + (f" — {reason}" if reason else "")
            ),
            detail={
                "run_id": run_id,
                "from": from_status.value,
                "to": to_status.value,
                "reason": reason,
                "goal_preview": goal_preview,
            },
        )
    except Exception:
        # Ledger module unavailable / disabled / write error — never
        # rises out of the executor's hot path.
        logger.debug(
            "autonomous_executor: milestone emission failed",
            exc_info=True,
        )


def _audit_transition_safe(
    *,
    run_id: str,
    from_status: "ExecutorStatus",
    to_status: "ExecutorStatus",
    reason: str = "",
) -> None:
    """Append a hash-chained audit row for a status transition.

    Verified Plan Risk #3 closure (2026-05-22): the fourth audit
    chain (alongside coding_session, change_request, governance_
    amendment) that records every transition + step outcome for
    forensic replay. Failure-isolated.
    """
    try:
        import importlib
        au = importlib.import_module("app.autonomous_executor.audit")
        au.record(
            run_id=run_id,
            kind="transition",
            actor="autonomous_executor",
            payload={
                "from": from_status.value,
                "to": to_status.value,
                "reason": (reason or "")[:200],
            },
        )
    except Exception:
        logger.debug(
            "autonomous_executor: audit append failed",
            exc_info=True,
        )


def _escalate_blocker_safe(
    *, run_id: str, reason: str, goal_preview: str = "",
) -> None:
    """Fire the BLOCKED-state Signal escalation. Failure-isolated.

    Lazy-imports the escalation module so models.py stays load-clean
    in stripped test environments. Verified Implementation Plan
    Gap #2 (2026-05-22).
    """
    try:
        import importlib
        esc = importlib.import_module(
            "app.autonomous_executor.escalation",
        )
        esc.escalate_blocker(
            run_id=run_id, reason=reason, goal_preview=goal_preview,
        )
    except Exception:
        logger.debug(
            "autonomous_executor: escalate_blocker failed",
            exc_info=True,
        )


class InvalidExecutorTransition(Exception):
    """Raised when a state-machine transition is illegal."""


class ExecutorStatus(str, enum.Enum):
    """Run lifecycle state. Terminal states (see ``TERMINAL_STATUSES``)
    cannot be transitioned out of."""

    PENDING_APPROVAL = "pending_approval"
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ABORTED = "aborted"


TERMINAL_STATUSES: frozenset[ExecutorStatus] = frozenset({
    ExecutorStatus.COMPLETED,
    ExecutorStatus.FAILED,
    ExecutorStatus.BUDGET_EXHAUSTED,
    ExecutorStatus.ABORTED,
})


# Legal transitions: source → set[destination]. Computed from the
# diagram above. Used by :func:`assert_can_transition`.
_LEGAL_TRANSITIONS: dict[ExecutorStatus, frozenset[ExecutorStatus]] = {
    # Opt-in gate: a run parked here is never picked up by the scheduler
    # (see scheduler_job._pick_run). Approval routes it to CREATED so the
    # normal CREATED→PLANNING path is untouched; 👎 / expiry routes to ABORTED.
    ExecutorStatus.PENDING_APPROVAL: frozenset({
        ExecutorStatus.CREATED,
        ExecutorStatus.ABORTED,
    }),
    ExecutorStatus.CREATED: frozenset({
        ExecutorStatus.PLANNING,
        ExecutorStatus.ABORTED,
    }),
    ExecutorStatus.PLANNING: frozenset({
        ExecutorStatus.RUNNING,
        ExecutorStatus.FAILED,
        ExecutorStatus.ABORTED,
    }),
    ExecutorStatus.RUNNING: frozenset({
        ExecutorStatus.COMPLETED,
        ExecutorStatus.FAILED,
        ExecutorStatus.BUDGET_EXHAUSTED,
        ExecutorStatus.BLOCKED,
        ExecutorStatus.PAUSED,
        ExecutorStatus.ABORTED,
    }),
    ExecutorStatus.BLOCKED: frozenset({
        ExecutorStatus.RUNNING,
        ExecutorStatus.ABORTED,
    }),
    ExecutorStatus.PAUSED: frozenset({
        ExecutorStatus.RUNNING,
        ExecutorStatus.ABORTED,
    }),
    # Terminal states: empty set — no outbound transitions.
    ExecutorStatus.COMPLETED: frozenset(),
    ExecutorStatus.FAILED: frozenset(),
    ExecutorStatus.BUDGET_EXHAUSTED: frozenset(),
    ExecutorStatus.ABORTED: frozenset(),
}


def assert_can_transition(
    current: ExecutorStatus,
    target: ExecutorStatus,
) -> None:
    """Raise :class:`InvalidExecutorTransition` if ``current → target``
    is not legal. No-op on legal transitions (including self-loops are
    rejected by default — call sites that allow them must check first).
    """
    if current is target:
        raise InvalidExecutorTransition(
            f"self-transition not allowed: status is already "
            f"{current.value!r}",
        )
    legal = _LEGAL_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise InvalidExecutorTransition(
            f"illegal transition {current.value!r} → {target.value!r}; "
            f"legal: {sorted(s.value for s in legal)}",
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Step ────────────────────────────────────────────────────────────


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ExecutorStep:
    """One step of the executor's plan.

    A step is a Commander dispatch — text in, text out. The crew_hint
    field is optional; when set, it overrides Commander's routing.
    """

    step_id: str
    description: str
    crew_hint: str = ""
    status: StepStatus = StepStatus.PENDING
    result_text: str = ""
    failure_reason: str = ""
    cost_usd: float = 0.0
    tokens_used: int = 0
    started_at: str = ""
    ended_at: str = ""
    # Phase A.2 closure (2026-05-22) — CRs attributed to this step.
    # Populated by the driver after the step completes via
    # ``attribute_crs_to_step()`` which scans the change-request store
    # for entries created in the step's time window with a requestor
    # matching the executor's run/session prefix. Empty when the step
    # produced no CRs (most "research X" steps, all failed steps that
    # crashed before any tool invocation, etc).
    cr_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutorStep":
        raw_status = data.get("status", "pending")
        try:
            status = StepStatus(raw_status)
        except ValueError:
            status = StepStatus.PENDING
        return cls(
            step_id=str(data.get("step_id", "")),
            description=str(data.get("description", "")),
            crew_hint=str(data.get("crew_hint", "")),
            status=status,
            result_text=str(data.get("result_text", "")),
            failure_reason=str(data.get("failure_reason", "")),
            cost_usd=float(data.get("cost_usd", 0.0)),
            tokens_used=int(data.get("tokens_used", 0)),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            cr_ids=list(data.get("cr_ids") or []),
        )


# ── Budget ──────────────────────────────────────────────────────────


@dataclass
class Budget:
    """Per-run budget caps + spent counters. Caps are immutable after
    run creation; spent counters are monotonic (never decrease)."""

    cap_usd: float = 1.0
    cap_tokens: int = 20_000
    cap_wall_clock_s: int = 600  # 10 minutes default

    spent_usd: float = 0.0
    spent_tokens: int = 0
    started_at_monotonic: float = 0.0  # time.monotonic() at start

    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def remaining_tokens(self) -> int:
        return max(0, self.cap_tokens - self.spent_tokens)

    def elapsed_s(self) -> float:
        if self.started_at_monotonic <= 0:
            return 0.0
        return time.monotonic() - self.started_at_monotonic

    def remaining_wall_clock_s(self) -> float:
        return max(0.0, self.cap_wall_clock_s - self.elapsed_s())

    def is_exhausted(self) -> bool:
        if self.spent_usd >= self.cap_usd:
            return True
        if self.spent_tokens >= self.cap_tokens:
            return True
        if self.started_at_monotonic > 0 and \
                self.elapsed_s() >= self.cap_wall_clock_s:
            return True
        return False

    def can_afford(self, usd: float = 0.0, tokens: int = 0) -> bool:
        """Pre-check: would consuming (usd, tokens) keep us under cap?"""
        if usd < 0 or tokens < 0:
            raise ValueError("can_afford: amounts must be non-negative")
        if (self.spent_usd + usd) > self.cap_usd:
            return False
        if (self.spent_tokens + tokens) > self.cap_tokens:
            return False
        if self.started_at_monotonic > 0 and \
                self.elapsed_s() >= self.cap_wall_clock_s:
            return False
        return True

    def consume(self, usd: float = 0.0, tokens: int = 0) -> None:
        """Add to spent counters. Monotonic — never decreases.

        Does NOT raise on going over budget — the run's driver is
        responsible for checking ``is_exhausted`` after each consume
        and transitioning to BUDGET_EXHAUSTED. This separation lets a
        single step legally finish even if it brings the run over cap
        (closer to the operator's mental model of "one final step that
        edged us over").
        """
        if usd < 0 or tokens < 0:
            raise ValueError("consume: amounts must be non-negative")
        self.spent_usd += float(usd)
        self.spent_tokens += int(tokens)

    def start_clock(self) -> None:
        """Mark the wall-clock origin. Idempotent — second call is a
        no-op so resumes from BLOCKED/PAUSED don't reset the clock."""
        if self.started_at_monotonic <= 0:
            self.started_at_monotonic = time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        # started_at_monotonic is a process-local reference; persist as
        # delta-since-now so the field survives restarts (otherwise on
        # restart the new process's monotonic origin would be different
        # and elapsed_s() would be wrong).
        elapsed = self.elapsed_s()
        return {
            "cap_usd": self.cap_usd,
            "cap_tokens": self.cap_tokens,
            "cap_wall_clock_s": self.cap_wall_clock_s,
            "spent_usd": self.spent_usd,
            "spent_tokens": self.spent_tokens,
            "elapsed_s_at_save": elapsed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Budget":
        b = cls(
            cap_usd=float(data.get("cap_usd", 1.0)),
            cap_tokens=int(data.get("cap_tokens", 20_000)),
            cap_wall_clock_s=int(data.get("cap_wall_clock_s", 600)),
            spent_usd=float(data.get("spent_usd", 0.0)),
            spent_tokens=int(data.get("spent_tokens", 0)),
        )
        # Rebase the monotonic origin so elapsed_s reflects the
        # already-spent time. ``elapsed_s_at_save`` is the delta we
        # subtract from the new monotonic origin.
        elapsed_at_save = float(data.get("elapsed_s_at_save", 0.0))
        if elapsed_at_save > 0:
            b.started_at_monotonic = time.monotonic() - elapsed_at_save
        return b


# ── Run ─────────────────────────────────────────────────────────────


@dataclass
class ExecutorRun:
    """The top-level record. One per `/delegate <goal>` invocation."""

    run_id: str
    goal: str
    requestor: str  # agent_id or "operator:signal:<sender_id>"

    # Verification zone — feeds the gate_output extension chain
    # (Phase 1 piece 1). Defaults to "chat"; ZONE_FREE is reserved for
    # genuinely sandboxed steps; ZONE_AUTONOMOUS is the typical
    # `/delegate` zone since the executor runs without an operator
    # in the loop.
    zone: str = "chat"

    status: ExecutorStatus = ExecutorStatus.CREATED
    plan: list[ExecutorStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)

    # Timestamps
    created_at: str = field(default_factory=_now_iso)
    started_at: str = ""
    ended_at: str = ""
    last_touched_at: str = field(default_factory=_now_iso)

    # Lifecycle reasons
    failure_reason: str = ""
    abort_reason: str = ""
    blocked_reason: str = ""
    pause_reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def touch(self) -> None:
        """Update ``last_touched_at`` to now. Called by every state
        transition + step update. Terminal runs are still touchable
        for notes only — see :func:`record_note`."""
        self.last_touched_at = _now_iso()

    def transition(
        self,
        target: ExecutorStatus,
        *,
        reason: str = "",
    ) -> None:
        """Move the run to ``target``. Raises on illegal transitions.

        Side effects:
          * ``started_at`` set on first entry into PLANNING.
          * ``ended_at`` set on entry into any terminal state.
          * The appropriate reason field is populated for FAILED /
            ABORTED / BLOCKED / PAUSED transitions.
          * The wall-clock budget timer starts when entering RUNNING
            for the first time.
        """
        assert_can_transition(self.status, target)

        # First-time entry into PLANNING records the original start.
        if (
            target is ExecutorStatus.PLANNING
            and not self.started_at
        ):
            self.started_at = _now_iso()

        # First-time entry into RUNNING starts the wall-clock budget.
        if target is ExecutorStatus.RUNNING:
            self.budget.start_clock()

        if target in TERMINAL_STATUSES:
            self.ended_at = _now_iso()

        if target is ExecutorStatus.FAILED:
            self.failure_reason = reason or self.failure_reason or "unspecified"
        elif target is ExecutorStatus.BUDGET_EXHAUSTED:
            # Budget exhaustion is a class of failure — store the
            # diagnostic in ``failure_reason`` so operator-facing
            # surfaces have a single field to render "why this ended".
            self.failure_reason = (
                reason or self.failure_reason or "budget exhausted"
            )
        elif target is ExecutorStatus.ABORTED:
            self.abort_reason = reason or self.abort_reason or "operator-abort"
        elif target is ExecutorStatus.BLOCKED:
            self.blocked_reason = reason or self.blocked_reason or "blocked"
        elif target is ExecutorStatus.PAUSED:
            self.pause_reason = reason or self.pause_reason or "paused"

        prior_status = self.status
        self.status = target
        self.touch()

        # Verified Implementation Plan Gap #3 (2026-05-22): emit
        # ``executor_milestone`` to the identity continuity ledger on
        # every status transition. Failure-isolated — a ledger write
        # error must NEVER block a legitimate state change.
        _emit_milestone_safe(
            run_id=self.run_id,
            from_status=prior_status,
            to_status=target,
            reason=reason,
            goal_preview=(self.goal or "")[:140],
        )

        # Verified Plan Risk #3 (2026-05-22): append to the hash-
        # chained audit ledger (the FOURTH chain alongside
        # coding_session / change_request / governance_amendment).
        # Failure-isolated.
        _audit_transition_safe(
            run_id=self.run_id,
            from_status=prior_status,
            to_status=target,
            reason=reason,
        )

        # Verified Implementation Plan Gap #2 (2026-05-22): fire a
        # Signal escalation when entering BLOCKED so the operator can
        # see + resume. Failure-isolated — broken Signal client must
        # not block the legitimate state change (the BLOCKED state is
        # already committed above).
        if target is ExecutorStatus.BLOCKED:
            _escalate_blocker_safe(
                run_id=self.run_id,
                reason=self.blocked_reason,
                goal_preview=(self.goal or "")[:140],
            )

    def record_note(self, text: str) -> None:
        """Append an arbitrary note. Allowed in any state, including
        terminal — so post-mortem annotations are possible."""
        text = (text or "").strip()
        if not text:
            return
        self.notes.append(f"[{_now_iso()}] {text}")
        if not self.is_terminal:
            self.touch()

    def add_step(
        self,
        *,
        description: str,
        crew_hint: str = "",
    ) -> ExecutorStep:
        """Append a planned step. Only allowed while the run is in
        PLANNING. Returns the new step."""
        if self.status is not ExecutorStatus.PLANNING:
            raise InvalidExecutorTransition(
                f"cannot add_step: status is {self.status.value!r}; "
                f"expected 'planning'",
            )
        step = ExecutorStep(
            step_id=f"step-{len(self.plan) + 1:03d}",
            description=description.strip(),
            crew_hint=crew_hint.strip(),
        )
        self.plan.append(step)
        self.touch()
        return step

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "requestor": self.requestor,
            "zone": self.zone,
            "status": self.status.value,
            "plan": [s.to_dict() for s in self.plan],
            "notes": list(self.notes),
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "last_touched_at": self.last_touched_at,
            "failure_reason": self.failure_reason,
            "abort_reason": self.abort_reason,
            "blocked_reason": self.blocked_reason,
            "pause_reason": self.pause_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutorRun":
        try:
            status = ExecutorStatus(data.get("status", "created"))
        except ValueError:
            status = ExecutorStatus.CREATED
        return cls(
            run_id=str(data.get("run_id", "")),
            goal=str(data.get("goal", "")),
            requestor=str(data.get("requestor", "")),
            zone=str(data.get("zone", "chat")),
            status=status,
            plan=[
                ExecutorStep.from_dict(s)
                for s in (data.get("plan") or [])
                if isinstance(s, dict)
            ],
            notes=list(data.get("notes") or []),
            budget=Budget.from_dict(data.get("budget") or {}),
            created_at=str(data.get("created_at", _now_iso())),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            last_touched_at=str(data.get("last_touched_at", _now_iso())),
            failure_reason=str(data.get("failure_reason", "")),
            abort_reason=str(data.get("abort_reason", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            pause_reason=str(data.get("pause_reason", "")),
        )
