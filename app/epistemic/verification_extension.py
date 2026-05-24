"""Verification-extension evaluators (2026-05-20).

Additive evaluators that sit between :func:`calibration_check` and the
dispatch arms of :func:`gate_output`. Each evaluator can only ESCALATE
the verdict's suggested action — never weaken it. With the master
switch off (``verification_extension_enabled=False``), every evaluator
returns ``None`` and gate_output behaves bit-identically to today.

──────────────────────────────────────────────────────────────────────
Evaluator chain (in order):

  1. _evaluate_claim_source_consistency
        Extracts high-stakes claims from the proposal text and looks
        up the topic in :class:`SourceRegistry`. Missing source for a
        claim's topic → ``hedge`` (or higher if zone threshold is
        strict). Present source with low registered confidence →
        ``verify``.

  2. _evaluate_retrieval_on_low_confidence
        When the calibration verdict already suggests verification AND
        the per-task retrieval budget is non-zero AND a retriever is
        available, attempts one retrieval and feeds the outcome back
        into the verdict. v1 ships with a no-op default retriever
        (returns ``None`` immediately) — tests inject a mock to
        exercise the flow. Production wiring happens in Phase 2.

  3. _resolve_zone
        Maps task_id to a verification zone. v1 defaults every task
        to ``"chat"``. Future callers (change_requests.lifecycle,
        autonomous_executor) will pre-register zone for their tasks
        via :func:`register_zone_for_task`.

Aggregator: :func:`_max_action`
  Picks the strictest suggested_action across the calibration verdict
  + every evaluator's contribution. Precedence:
        ship < hedge < verify < peer_review

Telemetry: every evaluator's note is preserved in the returned
notes list so the orchestrator can log diagnostic detail.
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Callable, Optional

from app.epistemic.calibration import CalibrationVerdict, SuggestedAction
from app.subia.grounding.claims import (
    FactualClaim,
    extract_claims,
)
from app.subia.grounding.source_registry import (
    RegisteredSource,
    SourceRegistry,
    get_default_registry,
)

logger = logging.getLogger(__name__)


# ── Action precedence (escalate-only) ───────────────────────────────

_ACTION_PRECEDENCE: dict[str, int] = {
    "ship": 0,
    "hedge": 1,
    "verify": 2,
    "peer_review": 3,
}


def _max_action(actions: list[str]) -> SuggestedAction:
    """Pick the strictest suggested_action from a list. Unknown
    actions are treated as 'ship' (safest fallthrough)."""
    best = "ship"
    best_rank = -1
    for a in actions:
        if not a:
            continue
        r = _ACTION_PRECEDENCE.get(a, -1)
        if r > best_rank:
            best = a
            best_rank = r
    return best  # type: ignore[return-value]


# ── Zone resolution ─────────────────────────────────────────────────

_VALID_ZONES = ("chat", "autonomous", "financial")

# Per-task zone hints. Populated by future callers (e.g.
# change_requests.lifecycle, autonomous_executor) before they dispatch
# to gate_output. v1 callers all leave this empty → every task
# resolves to "chat". Bounded by _MAX_ZONE_HINTS via FIFO trim.
_zone_hints: dict[str, str] = {}
_zone_hints_lock = threading.Lock()
_MAX_ZONE_HINTS = 10_000


def register_zone_for_task(task_id: str, zone: str) -> None:
    """Pre-register the verification zone for a task. Idempotent.

    Future callers (change_requests.lifecycle for ZONE_INFRASTRUCTURE
    writes, autonomous_executor for ZONE_FREE tasks) register the zone
    before they invoke gate_output. v1 ships with no callers — every
    task defaults to "chat".

    Unknown zones are rejected; the caller's bug should be surfaced,
    not silently downgraded.
    """
    if not task_id:
        return
    if zone not in _VALID_ZONES:
        raise ValueError(
            f"zone must be one of {_VALID_ZONES!r}, got {zone!r}",
        )
    with _zone_hints_lock:
        if len(_zone_hints) >= _MAX_ZONE_HINTS:
            # Evict oldest insertion to cap memory. dict iteration is
            # insertion-ordered since 3.7.
            try:
                oldest = next(iter(_zone_hints))
                _zone_hints.pop(oldest, None)
            except StopIteration:
                pass
        _zone_hints[task_id] = zone


def clear_zone_hints_for_tests() -> None:
    """Test helper — never call from production code."""
    with _zone_hints_lock:
        _zone_hints.clear()


def _resolve_zone(task_id: str) -> str:
    """Return the verification zone for a task. Defaults to ``chat``
    when no caller has pre-registered the task."""
    if not task_id:
        return "chat"
    with _zone_hints_lock:
        return _zone_hints.get(task_id, "chat")


# ── Per-task retrieval budget tracker ───────────────────────────────

_retrieval_used: dict[str, int] = {}
_retrieval_lock = threading.Lock()
_MAX_RETRIEVAL_HINTS = 10_000


def _claim_retrieval_budget(task_id: str, budget: int) -> bool:
    """Atomically check + consume one unit of retrieval budget for
    this task. Returns True if a unit was successfully claimed.

    Idempotent across multiple evaluator passes within the same task:
    the second pass observes ``used >= budget`` and returns False.
    """
    if not task_id or budget <= 0:
        return False
    with _retrieval_lock:
        used = _retrieval_used.get(task_id, 0)
        if used >= budget:
            return False
        if len(_retrieval_used) >= _MAX_RETRIEVAL_HINTS:
            try:
                _retrieval_used.pop(next(iter(_retrieval_used)), None)
            except StopIteration:
                pass
        _retrieval_used[task_id] = used + 1
        return True


def clear_retrieval_budget_for_tests() -> None:
    """Test helper — never call from production code."""
    with _retrieval_lock:
        _retrieval_used.clear()


# ── Evaluator 1: claim-source consistency ───────────────────────────


def _evaluate_claim_source_consistency(
    proposal_text: str,
    *,
    threshold: float,
    registry: Optional[SourceRegistry] = None,
) -> tuple[Optional[str], str]:
    """Check that every high-stakes claim has a registered source.

    Returns ``(suggested_action, note)``:
      * ``(None, "")`` — no high-stakes claims, no opinion. Existing
        verdict stands.
      * ``("hedge", note)`` — at least one high-stakes claim has no
        registered source for its topic.
      * ``("verify", note)`` — at least one high-stakes claim has a
        registered source but its confidence is below the threshold.

    Topic-hint missing (empty string) → claim is skipped (we can't
    look it up without a topic). Conservative: don't escalate based
    on a key we can't resolve.
    """
    if not proposal_text:
        return None, ""
    try:
        claims = extract_claims(proposal_text)
    except Exception as exc:
        logger.debug(
            "verification_extension: extract_claims failed: %s", exc,
        )
        return None, ""

    high_stakes = [c for c in claims if c.is_high_stakes()]
    if not high_stakes:
        return None, ""

    reg = registry or get_default_registry()

    missing_source = 0
    low_confidence = 0
    for claim in high_stakes:
        topic = (claim.topic_hint or "").strip()
        if not topic:
            # No topic to look up; conservative fallthrough.
            continue
        rs = _safe_get_registry(reg, topic, "default")
        if rs is None:
            missing_source += 1
            continue
        if rs.confidence < threshold:
            low_confidence += 1

    if missing_source > 0:
        note = (
            f"{missing_source} high-stakes claim(s) lack a registered source"
        )
        return "hedge", note

    if low_confidence > 0:
        note = (
            f"{low_confidence} high-stakes claim(s) bound to a low-confidence "
            f"source (threshold={threshold:.2f})"
        )
        return "verify", note

    return None, ""


def _safe_get_registry(
    registry: SourceRegistry,
    topic: str,
    key: str,
) -> Optional[RegisteredSource]:
    """Defensive wrapper — registry I/O must never crash the gate."""
    try:
        return registry.get(topic, key)
    except Exception as exc:
        logger.debug(
            "verification_extension: registry.get failed: %s", exc,
        )
        return None


# ── Evaluator 2: retrieval-on-low-confidence ────────────────────────


# Type alias for the injectable retriever. Returns True if retrieval
# successfully grounded the claim, False if it found nothing useful.
RetrieverFn = Callable[[str], bool]


def _evaluate_retrieval_on_low_confidence(
    proposal_text: str,
    *,
    verdict: CalibrationVerdict,
    task_id: str,
    threshold: float,
    budget: int,
    retriever: Optional[RetrieverFn] = None,
) -> tuple[Optional[str], str]:
    """Try one retrieval when calibration suggests verification.

    The evaluator only fires when:
      * verdict.suggested_action is ``hedge`` or ``verify`` (calibration
        already wants more confidence),
      * the per-task retrieval budget has not been exhausted,
      * an injectable ``retriever`` is available (production: None →
        evaluator returns None; tests: pass a mock).

    Outcomes:
      * retriever returns True → evidence found → emit ``verify``
        (matching what calibration suggested); note that retrieval ran.
      * retriever returns False → evidence not found → if zone threshold
        is strict (>= 0.90), escalate to ``peer_review``; otherwise
        keep ``verify`` (no-op vs. calibration).
      * budget exhausted → ``(None, "budget exhausted")`` so the
        verdict isn't escalated based on an unrun evaluator.
    """
    if verdict.suggested_action not in ("hedge", "verify"):
        # Calibration is already at ship or peer_review — nothing to
        # add from the retrieval evaluator.
        return None, ""
    if not proposal_text:
        return None, ""
    if not retriever:
        # Production-default no-op. Tests inject a mock to exercise.
        return None, ""

    if not _claim_retrieval_budget(task_id, budget):
        return None, "retrieval budget exhausted for this task"

    try:
        ok = bool(retriever(proposal_text))
    except Exception as exc:
        logger.debug(
            "verification_extension: retriever raised: %s", exc,
        )
        return None, "retrieval failed (kept calibration verdict)"

    if ok:
        return "verify", "retrieval grounded the claim"

    # Retrieval ran and found nothing. Escalate when zone is strict.
    if threshold >= 0.90:
        return (
            "peer_review",
            "retrieval found no supporting evidence (strict zone)",
        )
    return "verify", "retrieval found no supporting evidence"


# ── Aggregator entry-point ──────────────────────────────────────────


def apply_verification_extension(
    *,
    verdict: CalibrationVerdict,
    proposal_text: str,
    task_id: str,
    retriever: Optional[RetrieverFn] = None,
    registry: Optional[SourceRegistry] = None,
) -> tuple[CalibrationVerdict, list[str]]:
    """Run the verification-extension chain. Returns
    ``(extended_verdict, notes)``.

    ``extended_verdict`` is the calibration verdict (possibly with an
    escalated ``suggested_action``). The other fields are preserved.

    ``notes`` is a list of one-line diagnostic strings, one per
    evaluator that contributed (suitable for the gate's
    ``diagnostic_note`` field).

    With ``verification_extension_enabled`` OFF (the default), this
    function is a no-op: returns ``(verdict, [])``.

    Never raises — internal failures fall through to "no escalation"
    and the caller proceeds with the original verdict.
    """
    try:
        from app.runtime_settings import (
            get_verification_extension_enabled,
            get_verification_retrieval_budget_per_task,
            get_verification_threshold,
        )
        enabled = get_verification_extension_enabled()
    except Exception as exc:
        logger.debug(
            "verification_extension: runtime_settings unavailable: %s", exc,
        )
        return verdict, []

    if not enabled:
        return verdict, []

    try:
        zone = _resolve_zone(task_id)
        threshold = float(get_verification_threshold(zone))
        budget = int(get_verification_retrieval_budget_per_task())
    except Exception as exc:
        logger.debug(
            "verification_extension: threshold lookup failed: %s", exc,
        )
        return verdict, []

    notes: list[str] = []
    actions: list[str] = [verdict.suggested_action]

    # Evaluator 1: claim-source consistency
    try:
        cs_action, cs_note = _evaluate_claim_source_consistency(
            proposal_text,
            threshold=threshold,
            registry=registry,
        )
        if cs_action is not None:
            actions.append(cs_action)
            if cs_note:
                notes.append(f"claim-source: {cs_note}")
    except Exception as exc:
        logger.debug(
            "verification_extension: claim-source evaluator raised: %s", exc,
        )

    # Evaluator 2: retrieval-on-low-confidence
    # Uses the action chosen so far (verdict + claim-source) as the
    # signal of whether retrieval would help.
    try:
        provisional = _max_action(actions)
        provisional_verdict = (
            replace(verdict, suggested_action=provisional)
            if provisional != verdict.suggested_action
            else verdict
        )
        rt_action, rt_note = _evaluate_retrieval_on_low_confidence(
            proposal_text,
            verdict=provisional_verdict,
            task_id=task_id,
            threshold=threshold,
            budget=budget,
            retriever=retriever,
        )
        if rt_action is not None:
            actions.append(rt_action)
            if rt_note:
                notes.append(f"retrieval: {rt_note}")
        elif rt_note:
            notes.append(f"retrieval: {rt_note}")
    except Exception as exc:
        logger.debug(
            "verification_extension: retrieval evaluator raised: %s", exc,
        )

    # Evaluator 3 (Gap 3, 2026-05-24): gate_philosophy — escalation only.
    # Activates on autonomous/financial zones; consults the philosophy
    # panel; on unresolved tensions, escalates to peer_review and files
    # a Q4.1 tension + Q8 thread. Master switch default OFF.
    try:
        provisional = _max_action(actions)
        provisional_verdict = (
            replace(verdict, suggested_action=provisional)
            if provisional != verdict.suggested_action
            else verdict
        )
        from app.epistemic.gate_philosophy import evaluate as _phil_evaluate

        phil_action, phil_note = _phil_evaluate(
            proposal_text=proposal_text,
            task_id=task_id,
            verdict=provisional_verdict,
        )
        if phil_action is not None:
            actions.append(phil_action)
            if phil_note:
                notes.append(f"philosophy: {phil_note}")
        elif phil_note:
            notes.append(f"philosophy: {phil_note}")
    except Exception as exc:
        logger.debug(
            "verification_extension: gate_philosophy evaluator raised: %s",
            exc,
        )

    final_action = _max_action(actions)
    if final_action == verdict.suggested_action:
        return verdict, notes

    # Synthesize a new verdict with the escalated action. All other
    # fields preserved (biases_detected, forced_verifier_claim_ids,
    # note_for_post_mortem). The dispatcher in gate_output reads
    # suggested_action only.
    extended = replace(verdict, suggested_action=final_action)
    notes.append(
        f"verification-extension escalated to {final_action!r} "
        f"(zone={zone}, threshold={threshold:.2f})"
    )
    return extended, notes
