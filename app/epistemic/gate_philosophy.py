"""gate_philosophy — philosophy panel as a gate_output evaluator.

Gap 3 of the 2026-05-24 ultrathink analysis closure.

Pre-existing state: ``app/philosophy/dialectics.py:consult_panel`` returns
structured ``PerspectiveTension`` records, wired into Tier-3 amendment
proposals + identity-claim ratification + post-apply welfare calibration.
The verification_extension chain (claim-source consistency + retrieval-on-
low-confidence) is the 4-evaluator gate_output chain CLAUDE.md describes.
Philosophy is consulted at *proposal time* and at *post-apply* time but
NOT at output-delivery time — the gap.

What this evaluator adds: at output-delivery time, on autonomous-zone or
financial-zone tasks, it asks ``consult_panel`` about the proposed action.
If the panel returns ``unresolved_tensions`` of length >= threshold, the
evaluator:

  1. Escalates the gate_output verdict to ``peer_review`` so the
     orchestrator does NOT auto-ship (always blocking for the operator's
     conscious review when philosophy disagrees with itself).
  2. Files a Q4.1 tension store entry referencing the perspectives so the
     operator's tension surface picks it up.
  3. Files a Q8 thread under "philosophy-flagged decisions" so the
     long-horizon line-of-inquiry primitive tracks the resolution.

Three deliberate non-features:

  * **No chat-zone activation** — latency-sensitive surface; philosophy
    consult is ~7 days TTL'd but still expensive cold. Chat zone keeps
    the existing 4-evaluator chain only.
  * **Goodhart of philosophy itself avoided** — the evaluator does NOT
    return suggested_action='ship' or 'verify' to UNESCALATE another
    evaluator's decision. It can only ADD escalation, matching the
    extension-chain pattern.
  * **Default OFF** until the operator calibrates on a few weeks of
    advisory observations.

Master switch: ``gate_philosophy_enabled`` (default OFF).
Activation gate: zone ∈ {"autonomous", "financial"} AND
                 question length >= MIN_QUESTION_CHARS.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.epistemic.calibration import CalibrationVerdict

logger = logging.getLogger(__name__)


# ── Activation tunables ──────────────────────────────────────────────────

# Zones where philosophy participates. The verification extension already
# uses these zone names; we follow the existing schema.
_ACTIVE_ZONES = {"autonomous", "financial"}

# Below this many characters the proposal is too short to be a meaningful
# "decision" worth consulting philosophy on. Most chat replies fall here.
_MIN_QUESTION_CHARS = 80

# Threshold for "the panel found unresolved tension." A single unresolved
# tension from one tradition is noise; ≥2 distinct unresolved tensions
# across traditions is a genuine philosophical conflict.
_MIN_UNRESOLVED_TENSIONS = 2

# Cap on the question length we pass to consult_panel. The dialectics
# module already truncates internally but we keep the bound explicit so
# the gate's behavior is predictable.
_MAX_QUESTION_CHARS = 2000

# Cap on the snippet stored in the Tension store. Tension storage caps
# at 200 chars per source; we stay under that.
_TENSION_SNIPPET_MAX = 160


# ── Public API ───────────────────────────────────────────────────────────


def _enabled() -> bool:
    """Master switch read. Defaults OFF until operator calibrates."""
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_gate_philosophy_enabled())
    except Exception:
        return False


def _zone_for_task(task_id: str) -> str:
    """Resolve the verification zone via the existing zone-hints map.
    Defaults to 'chat' when no caller pre-registered the task."""
    try:
        from app.epistemic.verification_extension import _resolve_zone

        return _resolve_zone(task_id)
    except Exception:
        return "chat"


def _should_activate(proposal_text: str, task_id: str) -> tuple[bool, str]:
    """Two-stage gate: master switch ON + zone ∈ {autonomous, financial}
    + non-trivial proposal length. Returns (should_activate, reason)."""
    if not _enabled():
        return False, "switch_off"
    if not proposal_text or len(proposal_text) < _MIN_QUESTION_CHARS:
        return False, f"too_short:{len(proposal_text or '')}"
    zone = _zone_for_task(task_id)
    if zone not in _ACTIVE_ZONES:
        return False, f"zone_inactive:{zone}"
    return True, "ok"


def _format_question(proposal_text: str) -> str:
    """Compress the proposal into a panel-ready question.

    The panel cache is keyed on (question, traditions); we truncate so
    long proposals share a cache entry with their substantively-identical
    siblings (very long proposals rarely differ in their philosophical
    upshot from the first ~2KB).
    """
    text = (proposal_text or "").strip()
    return text[:_MAX_QUESTION_CHARS]


def _file_tension(question: str, perspectives: list, unresolved: list[str]) -> Optional[str]:
    """Open a Q4.1 tension store entry referencing the perspectives.

    Returns the tension id on success or None on any failure path
    (the gate must still escalate even if tension filing fails — they
    are independent surfaces).
    """
    try:
        from app.companion.tensions import TensionSource, create_tension
    except Exception:
        logger.debug("gate_philosophy: tensions module unavailable", exc_info=True)
        return None

    sources: list = []
    try:
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        for p in (perspectives or [])[:5]:
            snippet = (str(getattr(p, "claim", "") or "")[:_TENSION_SNIPPET_MAX]).strip()
            tradition = str(getattr(p, "tradition", "") or "")
            if not snippet:
                continue
            sources.append(
                TensionSource(
                    kind="pattern",
                    ts=now_iso,
                    snippet=f"[{tradition}] {snippet}",
                    ref=None,
                )
            )
        for u in (unresolved or [])[:3]:
            snippet = str(u or "").strip()[:_TENSION_SNIPPET_MAX]
            if snippet:
                sources.append(
                    TensionSource(
                        kind="pattern",
                        ts=now_iso,
                        snippet=f"[panel] {snippet}",
                        ref=None,
                    )
                )
    except Exception:
        logger.debug("gate_philosophy: source build failed", exc_info=True)

    try:
        # The tension question carries the operator-readable summary;
        # the proposal-text-derived question is the panel input.
        q = (
            f"Philosophy-flagged decision: {question[:200]}"
            if question
            else "Philosophy-flagged decision (no question)"
        )
        t = create_tension(
            question=q,
            sources=sources,
            detection_source="gate_philosophy",
        )
        return t.id if t else None
    except Exception:
        logger.debug("gate_philosophy: create_tension failed", exc_info=True)
        return None


def _file_thread(question: str, tension_id: Optional[str]) -> Optional[str]:
    """Open a Q8 thread for the long-horizon line-of-inquiry. Returns
    the thread id on success or None on failure.
    """
    try:
        from app.threads.lifecycle import create_thread
    except Exception:
        logger.debug("gate_philosophy: threads module unavailable", exc_info=True)
        return None
    try:
        title = f"Philosophy-flagged: {(question or '')[:80]}"
        desc_parts = [
            "The philosophy panel returned unresolved tensions on this "
            "proposed action. Auto-filed by gate_philosophy.",
        ]
        if tension_id:
            desc_parts.append(f"Cross-reference: tension {tension_id}.")
        th = create_thread(title=title, description="\n\n".join(desc_parts))
        return getattr(th, "id", None)
    except Exception:
        logger.debug("gate_philosophy: create_thread failed", exc_info=True)
        return None


def evaluate(
    *,
    proposal_text: str,
    task_id: str,
    verdict: CalibrationVerdict,
) -> tuple[Optional[str], str]:
    """Run the gate_philosophy evaluator. Returns
    ``(suggested_action_or_None, note)``.

    Contract matches the verification_extension evaluator contract:

      * ``(None, "")`` — no opinion. Existing verdict stands.
      * ``("peer_review", note)`` — panel found unresolved tensions;
        escalate to peer_review and file tension + thread.
      * ``(None, note)`` — activation gate fired but the panel didn't
        find tension (or skipped due to disabled / KB-empty / etc).
        Returned with a diagnostic note for telemetry.

    Never raises — internal failures fall through to ``(None, "")``.
    """
    should, reason = _should_activate(proposal_text, task_id)
    if not should:
        return None, ""

    try:
        from app.philosophy.dialectics import consult_panel
    except Exception:
        logger.debug("gate_philosophy: dialectics unavailable", exc_info=True)
        return None, ""

    question = _format_question(proposal_text)
    try:
        panel = consult_panel(question)
    except Exception as exc:
        logger.debug("gate_philosophy: consult_panel raised: %s", exc, exc_info=True)
        return None, f"panel_error:{type(exc).__name__}"

    if getattr(panel, "skipped_reason", None):
        return None, f"panel_skipped:{panel.skipped_reason}"

    unresolved = list(getattr(panel, "unresolved_tensions", []) or [])
    if len(unresolved) < _MIN_UNRESOLVED_TENSIONS:
        return None, (
            f"panel_clear:{len(unresolved)}_unresolved_below_"
            f"{_MIN_UNRESOLVED_TENSIONS}"
        )

    # Escalate. File tension first (some operators rely on the tension
    # surface), then thread (cross-link to tension when available).
    tension_id = _file_tension(question, list(panel.perspectives), unresolved)
    thread_id = _file_thread(question, tension_id)
    note_parts = [
        f"{len(unresolved)} unresolved philosophical tensions across "
        f"{len(panel.perspectives)} traditions"
    ]
    if tension_id:
        note_parts.append(f"tension={tension_id}")
    if thread_id:
        note_parts.append(f"thread={thread_id}")
    return "peer_review", "; ".join(note_parts)


__all__ = ["evaluate"]
