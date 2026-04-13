"""Phase 12 inter-proposal bridges (Proposals §"How they relate").

Five bridges, one file (Phase 10 convention). Each bridge is a pure
function over the kernel + proposal outputs; no global state.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.kernel import SubjectivityKernel
from app.subia.wonder.detector import UnderstandingDepth, WonderSignal, detect_wonder
from app.subia.wonder.register import apply_wonder_to_kernel, is_wonder_event
from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)

# In-memory queues (kernel-local; persisted via consolidator on its
# next pass). Plain lists rather than threads so SubIA's single-writer
# discipline is preserved.
_REVERIE_PRIORITY_TOPICS: list[str] = []
_UNDERSTANDING_VERIFY_QUEUE: list[dict] = []


# ── Reverie → Understanding (verify analogy candidates) ──────────────
def reverie_analogy_to_understanding(reverie_result) -> int:
    """Push every reverie-discovered analogy onto the Understanding
    pass queue for verification. Returns count enqueued."""
    n = 0
    for r in (reverie_result.resonances if reverie_result else []):
        _UNDERSTANDING_VERIFY_QUEUE.append({
            "kind": "analogy_candidate",
            "concept_a": r.get("a"),
            "concept_b": r.get("b"),
            "from_reverie_cycle": reverie_result.cycle_id,
        })
        n += 1
    return n


def drain_understanding_queue(limit: int = 5) -> list:
    """Caller (idle scheduler) drains and consumes."""
    out, remaining = _UNDERSTANDING_VERIFY_QUEUE[:limit], _UNDERSTANDING_VERIFY_QUEUE[limit:]
    _UNDERSTANDING_VERIFY_QUEUE.clear()
    _UNDERSTANDING_VERIFY_QUEUE.extend(remaining)
    return out


# ── Understanding → Wonder (depth → affect) ──────────────────────────
def understanding_to_wonder(
    kernel: SubjectivityKernel,
    depth: UnderstandingDepth,
    *,
    triggering_topic: str = "",
    triggering_item_id: Optional[str] = None,
) -> WonderSignal:
    """Convert an UnderstandingDepth into a WonderSignal and apply it."""
    signal = detect_wonder(
        depth,
        inhibit_threshold=float(SUBIA_CONFIG.get("WONDER_INHIBIT_THRESHOLD", 0.3)),
        event_threshold=float(SUBIA_CONFIG.get("WONDER_EVENT_THRESHOLD", 0.7)),
        triggering_topic=triggering_topic,
    )
    apply_wonder_to_kernel(kernel, signal, item_id=triggering_item_id)
    if is_wonder_event(signal) and triggering_topic:
        # Wonder → Reverie: surface this topic for next reverie cycle.
        wonder_to_reverie(triggering_topic)
    return signal


# ── Wonder → Reverie (priority topics for next cycle) ────────────────
def wonder_to_reverie(topic: str) -> None:
    if topic and topic not in _REVERIE_PRIORITY_TOPICS:
        _REVERIE_PRIORITY_TOPICS.append(topic)


def drain_reverie_priority_topics() -> list:
    out = list(_REVERIE_PRIORITY_TOPICS)
    _REVERIE_PRIORITY_TOPICS.clear()
    return out


# ── Shadow → SelfState (discovered limitations) ──────────────────────
def shadow_findings_to_self_state(
    kernel: SubjectivityKernel,
    findings: list,
) -> int:
    """APPEND-ONLY discovered_limitations write. Cannot delete prior
    findings (DGM constraint, Proposal 3 §3.2 output)."""
    if not findings:
        return 0
    existing = {f.get("name") for f in kernel.self_state.discovered_limitations}
    n = 0
    for f in findings:
        name = getattr(f, "name", None) or (f.get("name") if isinstance(f, dict) else None)
        if not name or name in existing:
            continue
        kernel.self_state.discovered_limitations.append({
            "name": name,
            "kind": getattr(f, "kind", None) or f.get("kind"),
            "detail": getattr(f, "detail", None) or f.get("detail", ""),
            "quantitative": getattr(f, "quantitative", None) or f.get("quantitative", {}),
        })
        n += 1
    # Self-coherence drops when shadow finds many divergences.
    if n:
        h = kernel.homeostasis
        if "self_coherence" in h.variables:
            h.variables["self_coherence"] = round(
                max(0.0, h.variables["self_coherence"] - 0.05 * min(n, 5)), 4,
            )
    return n


# ── Boundary → Consolidator (mode-aware routing) ─────────────────────
def boundary_route_for_kernel(kernel: SubjectivityKernel) -> dict:
    """Return per-item routing preferences for the consolidator."""
    from app.subia.boundary.differential import consolidator_route_for
    out: dict[str, dict] = {}
    for item in kernel.scene or []:
        mode = getattr(item, "processing_mode", None)
        if mode:
            out[item.id] = consolidator_route_for(mode)
    return out
