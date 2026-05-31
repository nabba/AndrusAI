"""interest_goal_emitter — autonomous interest-driven research goals.

Gap 2 of the 2026-05-24 ultrathink analysis closure.

The complement to ``affect/goal_emitter.py``. That module translates
sustained allostatic error (physiology) into autonomous goals on
``SelfState.current_goals``. This module translates sustained
cross-modal convergence (interest) into autonomous *research* goals
queued through the §62 autonomous_executor with a strict per-goal
budget cap.

What it watches
===============

``app/companion/cross_modal_patterns.py`` already detects
convergence — when a topic appears in ≥3 modalities (interest_model
sources + tickets) over a 21-day window with strength ≥0.7, it
persists a Pattern record. Without this emitter the pattern lands
in the daily briefing and dies there.

This module gates Pattern records into actual *autonomous research*:
the executor researches the topic, prepares a brief, and either
files the brief into ``notes/`` (operator-readable) or queues it
for the next morning briefing.

Goodhart + welfare guards
=========================

The "system initiated work that ate $50 of LLM credits without
permission" anti-pattern is mitigated by five guards:

  1. **Master switch off by default** — operator opts in via
     ``runtime_settings.set_interest_goal_emitter_enabled(True)``.
  2. **Per-goal budget cap** $2 — hardcoded in the executor run
     creation; the executor's existing Budget enforcement closes the
     loop.
  3. **One emission per 7 days** — the emitter writes its state to a
     JSON file and refuses a second emission inside the cooldown.
  4. **Welfare-breach pause** — when ``arbiter.welfare_breaching()``
     returns True the emitter sleeps. Affect-physiology gates the
     interest path.
  5. **Operator-absent pause** — when ``operator_transition`` reports
     ABSENT_90D or READ_MOSTLY, the emitter declines to emit (the
     operator can't disable in time if it goes wrong).

Operator interaction
====================

  * Emission fires a Signal alert with goal + reasoning + 👍/👎
    reactions.
  * **👎 reaction** → run is aborted via
    ``store.save`` with ``status=ABORTED``; topic enters a 30-day
    cooldown; identity-continuity ledger records the decline.
  * **👍 reaction** → no-op (the run was already started; this is
    operator acknowledgment).
  * **No reaction for 7 days** → run continues to completion. The
    silent-adopt path matches the briefing_evolution pattern.

The Signal reaction → run id correlation is the same pattern used by
the change-request system and governance amendments (signal_ts → id
JSON registry with 25h auto-purge).

Wiring
======

  * Registered as a LIGHT idle job ``interest-goal-emitter`` in
    ``app/companion/loop.py``.
  * Master switch ``interest_goal_emitter_enabled`` (default OFF).
  * Persists state to ``workspace/companion/interest_goal_emitter_state.json``.
  * Emits identity-continuity ledger landmarks on every emission +
    every decline.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────

# Minimum pattern strength before emission. Cross-modal_patterns'
# detector already gates at 0.7; we re-check here so the threshold
# can be raised independently if false positives appear.
_MIN_STRENGTH = 0.7

# Minimum age of the pattern. The detector uses a 21-day window;
# patterns are persisted with detected_at. We additionally require
# the pattern to have been DETECTED on at least one earlier pass —
# read recent-patterns history; an in-window pattern with <2 prior
# detections doesn't qualify (suppresses one-week spikes).
_MIN_PRIOR_DETECTIONS = 2

# Maximum emissions per cooldown window. 1 every 7d caps cost +
# noise from this surface.
_MAX_EMISSIONS_PER_WINDOW = 1
_EMISSION_WINDOW_DAYS = 7

# Per-emission executor budget. $2 cap is enforced via Budget; the
# executor's existing Budget machinery refuses to overspend.
_PER_EMISSION_BUDGET_USD = 2.0

# Per-topic decline cooldown after 👎. Operator may explicitly
# clear via the slash command.
_DECLINE_COOLDOWN_DAYS = 30

# Opt-in expiry. A run parked in PENDING_APPROVAL with no operator
# 👍/👎 within this window is auto-aborted by the next run() pass.
# Mirrors the change-request gate's silence-is-not-consent semantics:
# the operator must actively approve before any budget is spent.
_EXPIRY_DAYS = 7

# Topic identity is normalised to lowercase trimmed for dedup keys.
_TOPIC_KEY_MAXLEN = 80

# Per-emission Signal alert prefix. Topic-keyed so the arbiter can
# dedup multiple alerts on the same topic.
_SIGNAL_TOPIC_PREFIX = "interest_goal_emission"


# ── State file ───────────────────────────────────────────────────────────

_STATE_LOCK = threading.RLock()


def _state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT) / "companion" / "interest_goal_emitter_state.json"
    except Exception:
        return Path("/app/workspace/companion/interest_goal_emitter_state.json")


def _load_state() -> dict[str, Any]:
    """Read the persisted state. Corrupt/missing → fresh."""
    p = _state_path()
    if not p.exists():
        return {"emissions": [], "declines": {}}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return {"emissions": [], "declines": {}}
        data.setdefault("emissions", [])
        data.setdefault("declines", {})
        return data
    except Exception:
        logger.debug("interest_goal_emitter: state load failed", exc_info=True)
        return {"emissions": [], "declines": {}}


def _save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str))
        tmp.replace(p)
    except Exception:
        logger.debug("interest_goal_emitter: state save failed", exc_info=True)


# ── Dedup / cooldown helpers ─────────────────────────────────────────────


def _topic_key(topic: str) -> str:
    return (topic or "").strip().lower()[:_TOPIC_KEY_MAXLEN]


def _within_emission_window(state: dict[str, Any], now: datetime) -> int:
    cutoff = now - timedelta(days=_EMISSION_WINDOW_DAYS)
    cutoff_iso = cutoff.isoformat()
    count = 0
    for row in state.get("emissions") or []:
        if not isinstance(row, dict):
            continue
        ts = row.get("emitted_at") or ""
        if ts >= cutoff_iso:
            count += 1
    return count


def _topic_declined_recently(state: dict[str, Any], topic_key: str, now: datetime) -> bool:
    declines = state.get("declines") or {}
    until = declines.get(topic_key)
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(str(until))
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return now < until_dt


# ── External gates ───────────────────────────────────────────────────────


def _master_switch_on() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_interest_goal_emitter_enabled())
    except Exception:
        return False


def _executor_enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_autonomous_executor_enabled())
    except Exception:
        return False


def _welfare_breaching() -> bool:
    """The same affect-physiology guard the arbiter exposes. Failure-
    open: when arbiter is unavailable, assume safe.
    """
    try:
        from app.notify import arbiter as _arbiter  # type: ignore

        fn = getattr(_arbiter, "welfare_breaching", None)
        if callable(fn):
            return bool(fn())
    except Exception:
        return False
    return False


def _operator_unavailable() -> bool:
    """When the operator is ABSENT_90D / READ_MOSTLY / TRANSITIONED,
    declining to emit is the conservative call — the operator can't
    react fast enough to a runaway goal. Failure-open: when the
    transition module isn't wired, treat as ACTIVE.
    """
    try:
        from app.operator_transition import state as _opstate  # type: ignore

        load = getattr(_opstate, "load_state", None)
        if not callable(load):
            return False
        snapshot = load()
        phase = str(getattr(snapshot, "phase", "") or "").upper()
        return phase in {"ABSENT_90D", "READ_MOSTLY", "TRANSITIONED"}
    except Exception:
        return False


# ── Pattern qualification ────────────────────────────────────────────────


@dataclass
class QualifiedPattern:
    """A cross-modal pattern that survived every dedup + gate check
    and is ready to become an autonomous research goal."""

    topic: str
    modalities: list[str]
    occurrences_total: int
    strength: float
    detected_at: str
    prior_detections: int

    def as_goal_text(self) -> str:
        """Render the executor-facing goal description. Operator-
        readable; the executor planner reads this verbatim."""
        return (
            f"Research the topic '{self.topic}' which has appeared in "
            f"{len(self.modalities)} modalities ({', '.join(self.modalities)}) "
            f"with {self.occurrences_total} occurrences over the past 21 days. "
            f"Prepare a 1-page brief covering: (1) why this is trending in "
            f"my inputs, (2) the top 3 specific questions or decisions it "
            f"implies, (3) up to 5 high-quality external sources I should "
            f"read. Save the brief to notes/ as a markdown file."
        )

    def as_reasoning(self) -> str:
        """The 'why this goal' string surfaced to the operator. Used
        in the Signal alert + ledger landmark."""
        return (
            f"Strength {self.strength:.2f}, {self.occurrences_total} hits "
            f"across {len(self.modalities)} modalities, "
            f"detected on {self.prior_detections} prior passes."
        )


def _qualify_patterns(
    patterns: Iterable[dict[str, Any]],
    state: dict[str, Any],
    now: datetime,
) -> list[QualifiedPattern]:
    """Apply all gates to the list of candidate patterns from
    ``cross_modal_patterns.list_recent_patterns``. Returns the
    subset that survived every check, sorted by strength descending
    so the strongest wins if multiple qualify on the same pass.
    """
    candidates: dict[str, dict[str, Any]] = {}
    prior_counts: dict[str, int] = {}
    for p in patterns:
        if not isinstance(p, dict):
            continue
        if str(p.get("kind") or "topic") != "topic":
            continue  # person patterns route through a different surface
        if float(p.get("strength") or 0.0) < _MIN_STRENGTH:
            continue
        topic = str(p.get("topic") or "")
        if not topic:
            continue
        key = _topic_key(topic)
        if not key:
            continue
        prior_counts[key] = prior_counts.get(key, 0) + 1
        prev = candidates.get(key)
        if prev is None or float(p.get("strength") or 0.0) > float(prev.get("strength") or 0.0):
            candidates[key] = p

    qualified: list[QualifiedPattern] = []
    for key, p in candidates.items():
        if prior_counts.get(key, 0) < _MIN_PRIOR_DETECTIONS:
            continue
        if _topic_declined_recently(state, key, now):
            continue
        qualified.append(
            QualifiedPattern(
                topic=str(p.get("topic") or ""),
                modalities=list(p.get("modalities") or []),
                occurrences_total=int(p.get("occurrences_total") or 0),
                strength=float(p.get("strength") or 0.0),
                detected_at=str(p.get("detected_at") or ""),
                prior_detections=prior_counts.get(key, 0),
            )
        )
    qualified.sort(key=lambda q: q.strength, reverse=True)
    return qualified


# ── Emission ─────────────────────────────────────────────────────────────


def _spawn_executor_run(qp: QualifiedPattern, *, requestor: str) -> dict[str, Any]:
    """Create + persist an ExecutorRun for this qualified pattern.

    Mirrors ``app/autonomous_executor/tools/delegate_tool.py`` — the
    same path the operator's ``/delegate`` Signal command and the
    REST endpoint use. Budget enforced via Budget.cap_usd; the
    executor refuses to overspend.
    """
    try:
        import importlib

        store = importlib.import_module("app.autonomous_executor.store")
        models_mod = importlib.import_module("app.autonomous_executor.models")
        Budget = models_mod.Budget
        ExecutorRun = models_mod.ExecutorRun
        ExecutorStatus = models_mod.ExecutorStatus
    except Exception as exc:
        return {"ok": False, "reason": f"executor modules unavailable: {exc}"}

    # G3 (goal-seeding): interest goals are research questions — seed a
    # *research* run (literature -> hypotheses -> investigate -> draft -> gate)
    # rather than a bare single-step run. build_research_run pre-populates the
    # plan and the driver runs a pre-populated plan straight through (skips the
    # planner — driver._handle_planning: "plan present -> RUNNING").
    # experiment=False by deliberate default: an auto-emitted goal does
    # literature review + draft, NOT autonomous code execution in the sandbox —
    # the operator opts into experiments manually via /delegate.
    try:
        from app.research.run import build_research_run

        run = build_research_run(
            qp.as_goal_text(),
            requestor=requestor,
            zone="autonomous",
            budget=Budget(cap_usd=float(_PER_EMISSION_BUDGET_USD)),
            experiment=False,
        )
        # Opt-in gate: park at PENDING_APPROVAL — the scheduler skips it (see
        # scheduler_job._pick_run) until the operator approves via 👍. No budget
        # is spent while it waits; on approval -> CREATED -> the research plan
        # runs.
        run.status = ExecutorStatus.PENDING_APPROVAL
    except Exception:
        logger.debug(
            "interest_goal_emitter: research-run build failed; bare run",
            exc_info=True,
        )
        run = ExecutorRun(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            goal=qp.as_goal_text(),
            requestor=requestor,
            status=ExecutorStatus.PENDING_APPROVAL,
            budget=Budget(cap_usd=float(_PER_EMISSION_BUDGET_USD)),
            zone="autonomous",
        )
    run_id = run.run_id
    try:
        store.save(run)
    except Exception as exc:
        return {"ok": False, "reason": f"persistence failed: {exc}"}

    try:
        from app.autonomous_executor import audit as _audit

        _audit.record(
            run_id=run_id,
            kind="run_pending_approval",
            actor=requestor,
            payload={
                "goal_preview": qp.as_goal_text()[:140],
                "budget_usd": _PER_EMISSION_BUDGET_USD,
                "source": "interest_goal_emitter",
                "topic": qp.topic,
            },
        )
    except Exception:
        logger.debug("interest_goal_emitter: audit emission failed", exc_info=True)

    return {"ok": True, "run_id": run_id}


def _signal_alert(qp: QualifiedPattern, run_id: str) -> str | None:
    """Best-effort Signal alert. Returns the message timestamp or
    None on failure. The timestamp is the operator's link to the
    👍/👎 reaction handler.
    """
    try:
        from app.signal_client import send_message_blocking  # type: ignore
    except Exception:
        return None
    body = (
        f"💡 Interest signal — autonomous research awaiting approval\n\n"
        f"Topic: *{qp.topic}*\n"
        f"Why: {qp.as_reasoning()}\n\n"
        f"I'd like to run a research goal on this (budget cap "
        f"${_PER_EMISSION_BUDGET_USD:.2f}). "
        f"👍 approves + starts it; "
        f"👎 skips + adds the topic to a {_DECLINE_COOLDOWN_DAYS}-day cooldown. "
        f"No reaction within {_EXPIRY_DAYS} days = the request expires "
        f"(nothing runs, no spend).\n\n"
        f"Run id: `{run_id}`"
    )
    try:
        ts = send_message_blocking(body, topic=f"{_SIGNAL_TOPIC_PREFIX}:{qp.topic}")
        return str(ts) if ts else None
    except Exception:
        logger.debug("interest_goal_emitter: Signal alert failed", exc_info=True)
        return None


def _register_signal_bridge(signal_ts: str | None, run_id: str) -> None:
    """Bind Signal timestamp → executor run id so the reaction
    handler can find the run when 👎 lands. Best-effort — the bridge
    is a sibling to the governance + change-request bridges.
    """
    if not signal_ts:
        return
    try:
        from app import interest_goal_signal_bridge

        interest_goal_signal_bridge.register(signal_ts, run_id)
    except Exception:
        logger.debug("interest_goal_emitter: bridge register failed", exc_info=True)


def _emit_landmark(kind: str, qp: QualifiedPattern, run_id: str | None) -> None:
    """Append to the identity-continuity ledger. The kind is
    ``interest_goal_emission`` for successful emissions and
    ``interest_goal_decline`` for 👎 declines.
    """
    try:
        from app.identity import continuity_ledger

        record = getattr(continuity_ledger, "record_event", None)
        if not callable(record):
            return
        record(
            kind=kind,
            actor="interest_goal_emitter",
            summary=(
                f"{kind} topic={qp.topic!r} strength={qp.strength:.2f}"
            ),
            detail={
                "topic": qp.topic,
                "modalities": qp.modalities,
                "occurrences_total": qp.occurrences_total,
                "strength": qp.strength,
                "run_id": run_id,
            },
        )
    except Exception:
        logger.debug("interest_goal_emitter: ledger emission failed", exc_info=True)


# ── Public run entry ─────────────────────────────────────────────────────


def emit_for_pattern(qp: QualifiedPattern, *, requestor: str = "interest_goal_emitter") -> dict[str, Any]:
    """Run the full emission pipeline for a single qualified pattern.

    Composed of: spawn run → Signal alert → bridge register → state
    write → ledger landmark. Each step failure-isolated; the function
    returns the run_id even if Signal/bridge/ledger all failed (the
    run is the load-bearing artifact).
    """
    spawn = _spawn_executor_run(qp, requestor=requestor)
    if not spawn.get("ok"):
        return {"ok": False, "reason": spawn.get("reason") or "spawn failed"}
    run_id = str(spawn.get("run_id") or "")
    signal_ts = _signal_alert(qp, run_id)
    _register_signal_bridge(signal_ts, run_id)

    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("emissions", []).append(
            {
                "run_id": run_id,
                "topic": qp.topic,
                "topic_key": _topic_key(qp.topic),
                "strength": qp.strength,
                "modalities": qp.modalities,
                "emitted_at": datetime.now(timezone.utc).isoformat(),
                "signal_ts": signal_ts,
            }
        )
        # Keep emission history bounded — last 200 rows
        if len(state["emissions"]) > 200:
            state["emissions"] = state["emissions"][-200:]
        _save_state(state)

    _emit_landmark("interest_goal_emission", qp, run_id)
    return {"ok": True, "run_id": run_id, "signal_ts": signal_ts}


def decline(topic: str, *, run_id: str | None = None, source: str = "operator") -> dict[str, Any]:
    """Mark a topic declined. Sets the cooldown + records the ledger
    landmark. Idempotent — calling twice in the same window is fine.

    Called by:
      * The 👎 Signal reaction handler
      * The ``/interest decline <topic>`` slash command (future)
    """
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "reason": "empty topic"}
    key = _topic_key(topic)
    until = datetime.now(timezone.utc) + timedelta(days=_DECLINE_COOLDOWN_DAYS)
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("declines", {})[key] = until.isoformat()
        _save_state(state)

    if run_id:
        try:
            import importlib

            store = importlib.import_module("app.autonomous_executor.store")
            run = store.get(run_id)
            if run and not run.is_terminal:
                models_mod = importlib.import_module("app.autonomous_executor.models")
                run.transition(
                    models_mod.ExecutorStatus.ABORTED,
                    reason="declined by operator (interest goal)",
                )
                store.save(run)
        except Exception:
            logger.debug("interest_goal_emitter: run abort failed", exc_info=True)

    qp = QualifiedPattern(
        topic=topic,
        modalities=[],
        occurrences_total=0,
        strength=0.0,
        detected_at="",
        prior_detections=0,
    )
    _emit_landmark("interest_goal_decline", qp, run_id)
    return {"ok": True, "topic_key": key, "until": until.isoformat()}


def topic_for_run(run_id: str) -> str | None:
    """Resolve the topic for an emitted run from the emission history.

    The Signal bridge only stores ``signal_ts → run_id`` (no topic), so
    the 👎 reaction handler needs this to feed :func:`decline` a topic
    for the cooldown. Most-recent emission wins on the (rare) case of a
    recycled id. Returns None if the run isn't one of ours.
    """
    run_id = (run_id or "").strip()
    if not run_id:
        return None
    try:
        state = _load_state()
    except Exception:
        return None
    for row in reversed(state.get("emissions") or []):
        if isinstance(row, dict) and row.get("run_id") == run_id:
            topic = row.get("topic")
            return str(topic) if topic else None
    return None


def approve(run_id: str, *, source: str = "operator") -> dict[str, Any]:
    """Approve a pending interest-goal run: PENDING_APPROVAL → CREATED.

    Once CREATED, the scheduler's ``_pick_run`` will advance it on the
    next tick (the normal CREATED→PLANNING→RUNNING path). This is the
    opt-in counterpart to :func:`decline`.

    Called by:
      * The 👍 Signal reaction handler
      * (future) an ``/interest approve <run_id>`` slash command

    If the run is already past PENDING_APPROVAL (operator double-tapped,
    or it expired), this is a no-op reported as ok=True so the reaction
    handler stays quiet.
    """
    run_id = (run_id or "").strip()
    if not run_id:
        return {"ok": False, "reason": "empty run_id"}
    try:
        import importlib

        store = importlib.import_module("app.autonomous_executor.store")
        models_mod = importlib.import_module("app.autonomous_executor.models")
    except Exception as exc:
        return {"ok": False, "reason": f"executor modules unavailable: {exc}"}

    run = store.get(run_id)
    if run is None:
        return {"ok": False, "reason": "run not found"}
    if run.status is not models_mod.ExecutorStatus.PENDING_APPROVAL:
        return {"ok": True, "run_id": run_id, "already": run.status.value}
    try:
        run.transition(
            models_mod.ExecutorStatus.CREATED,
            reason="approved by operator (interest goal)",
        )
        store.save(run)
    except Exception as exc:
        return {"ok": False, "reason": f"transition failed: {exc}"}

    topic = topic_for_run(run_id) or ""
    qp = QualifiedPattern(
        topic=topic,
        modalities=[],
        occurrences_total=0,
        strength=0.0,
        detected_at="",
        prior_detections=0,
    )
    _emit_landmark("interest_goal_approved", qp, run_id)
    return {"ok": True, "run_id": run_id}


def _expire_stale_pending(now: datetime) -> int:
    """Abort our PENDING_APPROVAL runs older than ``_EXPIRY_DAYS``.

    Silence-is-not-consent: a run the operator never reacted to must not
    linger as a standing invitation. Returns the count aborted.
    Failure-isolated end-to-end — best-effort housekeeping that must
    never block the emission pipeline.
    """
    try:
        import importlib

        store = importlib.import_module("app.autonomous_executor.store")
        models_mod = importlib.import_module("app.autonomous_executor.models")
    except Exception:
        return 0
    try:
        active = store.list_active(limit=200)
    except Exception:
        return 0
    cutoff = now - timedelta(days=_EXPIRY_DAYS)
    aborted = 0
    for run in active:
        try:
            if run.status is not models_mod.ExecutorStatus.PENDING_APPROVAL:
                continue
            if run.requestor != "interest_goal_emitter":
                continue
            try:
                created_dt = datetime.fromisoformat(run.created_at or "")
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if created_dt > cutoff:
                continue
            run.transition(
                models_mod.ExecutorStatus.ABORTED,
                reason=(
                    f"expired (no operator approval within "
                    f"{_EXPIRY_DAYS} days)"
                ),
            )
            store.save(run)
            aborted += 1
        except Exception:
            logger.debug(
                "interest_goal_emitter: expiry sweep skipped a run",
                exc_info=True,
            )
            continue
    return aborted


def run() -> dict[str, Any]:
    """LIGHT idle-job entry. Single-pass. Failure-isolated."""
    result: dict[str, Any] = {
        "checked": 0,
        "qualified": 0,
        "emitted": 0,
        "expired": 0,
        "skipped_reason": None,
    }
    if not _master_switch_on():
        result["skipped_reason"] = "master_switch_off"
        return result

    # Housekeeping first: expire pending-approval runs the operator never
    # reacted to. Runs even when the executor is temporarily disabled —
    # an ignored invitation shouldn't outlive its window.
    try:
        result["expired"] = _expire_stale_pending(datetime.now(timezone.utc))
    except Exception:
        logger.debug("interest_goal_emitter: expiry sweep failed", exc_info=True)

    if not _executor_enabled():
        result["skipped_reason"] = "executor_disabled"
        return result
    if _welfare_breaching():
        result["skipped_reason"] = "welfare_breach"
        return result
    if _operator_unavailable():
        result["skipped_reason"] = "operator_unavailable"
        return result

    state = _load_state()
    now = datetime.now(timezone.utc)
    if _within_emission_window(state, now) >= _MAX_EMISSIONS_PER_WINDOW:
        result["skipped_reason"] = "emission_window_full"
        return result

    try:
        from app.companion.cross_modal_patterns import list_recent_patterns

        patterns = list_recent_patterns(n=50, min_strength=_MIN_STRENGTH)
    except Exception:
        logger.debug("interest_goal_emitter: cross_modal_patterns unavailable", exc_info=True)
        result["skipped_reason"] = "patterns_unavailable"
        return result

    result["checked"] = len(patterns)
    qualified = _qualify_patterns(patterns, state, now)
    result["qualified"] = len(qualified)
    if not qualified:
        result["skipped_reason"] = "no_qualified_patterns"
        return result

    # Emit at most one per run — _MAX_EMISSIONS_PER_WINDOW caps the
    # 7-day rate; doing more than one in a single pass would burn
    # multiple emissions on a single observation cycle.
    target = qualified[0]
    outcome = emit_for_pattern(target)
    if outcome.get("ok"):
        result["emitted"] = 1
        result["run_id"] = outcome.get("run_id")
        result["topic"] = target.topic
    else:
        result["emit_failed_reason"] = outcome.get("reason")
    return result


__all__ = [
    "QualifiedPattern",
    "approve",
    "decline",
    "emit_for_pattern",
    "run",
    "topic_for_run",
]
