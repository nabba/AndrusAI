"""
subia.scene.tiers — Amendment A: three-tier attentional structure.

The GWT-2 scene holds 5 focal items. This is the bottleneck that gives
broadcast information its significance — but a strict bottleneck also
creates attentional narrowing: a critical regulatory deadline at
salience 0.42 gets evicted because Archibal fundraising dominates at
0.82. Commander plans a week of Archibal work. The deadline passes.

Biological consciousness solved this problem with foveal + peripheral
vision. This module implements the software analog:

    FOCAL (5 items)       — full CIL processing, affect, prediction
    PERIPHERAL (~12)      — title + salience + section, alerts only
    STRATEGIC SCAN        — on-demand, separate tool (see strategic_scan.py)

Peripheral items do not enter `CompetitiveGate._active` — they live in
the gate's `_peripheral` deque. This module constructs the focal +
peripheral view for context injection, flags items with deadlines or
conflicts, and handles commitment-orphan protection: if an active
commitment has ZERO representation anywhere in the top-20 scored items,
a placeholder peripheral entry is forced so the system cannot entirely
lose track of something it committed to.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────

@dataclass
class PeripheralEntry:
    """Lightweight metadata for a peripheral-tier item.

    Peripheral entries do NOT carry embeddings, affect, predictions,
    or ownership metadata. They are purely "something the system
    noticed but isn't currently attending to".
    """
    summary: str = ""
    salience: float = 0.0
    section: str = "unknown"        # venture/subsystem tag
    item_id: str = ""
    has_conflict: bool = False
    deadline: str | None = None
    forced_reason: str | None = None  # 'commitment_orphan' if forced

    def to_dict(self) -> dict:
        return {
            "summary": self.summary[:80],
            "salience": round(self.salience, 2),
            "section": self.section,
            "item_id": self.item_id[:40],
            "has_conflict": self.has_conflict,
            "deadline": self.deadline,
            "forced_reason": self.forced_reason,
        }


@dataclass
class AttentionalTiers:
    """Output of build_attentional_tiers — three tiers + alerts."""
    focal: list = field(default_factory=list)          # list of WorkspaceItem
    peripheral: list = field(default_factory=list)     # list of PeripheralEntry
    peripheral_alerts: list = field(default_factory=list)  # list of str

    def to_dict(self) -> dict:
        return {
            "focal": [_focal_dict(i) for i in self.focal],
            "peripheral": [p.to_dict() for p in self.peripheral],
            "peripheral_alerts": list(self.peripheral_alerts),
        }


def _focal_dict(item) -> dict:
    return {
        "item_id": getattr(item, "item_id", "")[:40],
        "summary": str(getattr(item, "content", ""))[:80],
        "salience": round(float(getattr(item, "salience_score", 0.0)), 2),
        "source": getattr(item, "source_channel", "") or getattr(item, "source", ""),
    }


# ── Core: build three-tier view ──────────────────────────────────

def build_attentional_tiers(
    scored_items: Iterable,
    *,
    focal_capacity: int | None = None,
    peripheral_capacity: int | None = None,
    min_salience: float | None = None,
) -> AttentionalTiers:
    """Build focal + peripheral tiers from a list of scored items.

    Items are assumed ordered highest-salience first. The top
    `focal_capacity` items become focal (full-processing). The next
    `peripheral_capacity` become peripheral (metadata-only). Items
    below `min_salience` are dropped entirely.

    Args:
        scored_items:        Iterable, highest-salience first. Each
                             item must expose .item_id, .content or
                             .summary, .salience_score, .metadata,
                             .conflicts_with (any of these may be
                             missing — duck-typed).
        focal_capacity:      Top N go to the focal tier (default
                             SUBIA_CONFIG SCENE_CAPACITY).
        peripheral_capacity: Next M go to peripheral (default
                             SUBIA_CONFIG PERIPHERAL_CAPACITY).
        min_salience:        Drop floor (default
                             SUBIA_CONFIG SCENE_MIN_SALIENCE).

    Returns AttentionalTiers. Never raises.
    """
    focal_capacity = int(
        focal_capacity if focal_capacity is not None
        else SUBIA_CONFIG["SCENE_CAPACITY"]
    )
    peripheral_capacity = int(
        peripheral_capacity if peripheral_capacity is not None
        else SUBIA_CONFIG["PERIPHERAL_CAPACITY"]
    )
    min_salience = float(
        min_salience if min_salience is not None
        else SUBIA_CONFIG["SCENE_MIN_SALIENCE"]
    )

    tiers = AttentionalTiers()
    try:
        items = list(scored_items)
    except Exception:
        items = []

    for item in items:
        salience = float(getattr(item, "salience_score", 0.0))
        if salience < min_salience:
            continue

        if len(tiers.focal) < focal_capacity:
            tiers.focal.append(item)
            continue
        if len(tiers.peripheral) < peripheral_capacity:
            entry = _to_peripheral(item, salience)
            tiers.peripheral.append(entry)
            if entry.deadline:
                tiers.peripheral_alerts.append(
                    f"Peripheral item has deadline: "
                    f"{entry.summary[:40]} — {entry.deadline}"
                )
            if entry.has_conflict:
                tiers.peripheral_alerts.append(
                    f"Peripheral item has conflict: {entry.summary[:40]}"
                )
            continue
        # Beyond peripheral capacity — discard (strategic scan can find it).
        break

    return tiers


def _to_peripheral(item, salience: float) -> PeripheralEntry:
    """Extract peripheral-tier metadata from a WorkspaceItem-like object."""
    summary = (
        str(getattr(item, "content", ""))
        or str(getattr(item, "summary", ""))
    )[:80]
    section = _derive_section(item)
    metadata = getattr(item, "metadata", {}) or {}
    deadline = metadata.get("deadline") if isinstance(metadata, dict) else None
    conflicts = getattr(item, "conflicts_with", []) or []
    return PeripheralEntry(
        summary=summary,
        salience=salience,
        section=section,
        item_id=str(getattr(item, "item_id", ""))[:40],
        has_conflict=bool(conflicts),
        deadline=str(deadline) if deadline else None,
    )


def _derive_section(item) -> str:
    """Best-effort venture/section tag from item metadata."""
    metadata = getattr(item, "metadata", {}) or {}
    if isinstance(metadata, dict):
        for key in ("section", "venture", "domain", "project"):
            value = metadata.get(key)
            if value:
                return str(value)[:20]
    channel = str(getattr(item, "source_channel", "") or "")
    if channel:
        return channel.split(":")[0][:20]
    return "unknown"


# ── Commitment-orphan protection ─────────────────────────────────

def protect_commitment_items(
    tiers: AttentionalTiers,
    scored_items: Iterable,
    commitments: Iterable,
    *,
    scan_depth: int = 20,
) -> AttentionalTiers:
    """Ensure every active commitment has at least one representative
    in either focal or peripheral tiers.

    If a commitment has ZERO representation in the top `scan_depth`
    scored items, force a peripheral entry with forced_reason
    'commitment_orphan'. This prevents attentional narrowing from
    completely losing track of a commitment the system has made.

    Args:
        tiers:         the AttentionalTiers from build_attentional_tiers
        scored_items:  the full scored list used to build tiers (we
                       check the top scan_depth for any commitment ref)
        commitments:   iterable of Commitment objects (or dicts)

    Returns the same AttentionalTiers (mutated in place), with any
    newly-forced peripheral entries appended.
    """
    try:
        scored_list = list(scored_items)[:scan_depth]
    except Exception:
        scored_list = []

    # Build the set of referenced wiki pages from the top-N scored items.
    referenced_refs = _collect_referenced_refs(
        list(tiers.focal)
        + [_from_peripheral(p) for p in tiers.peripheral]
        + scored_list
    )

    # Find unrepresented active commitments.
    unrepresented = []
    for commitment in commitments:
        if _commitment_status(commitment) != "active":
            continue
        related = _commitment_related(commitment)
        if not related:
            # A commitment with no wiki pages still needs protection —
            # treat it as orphaned so the deadline surfaces.
            unrepresented.append(commitment)
            continue
        if not any(r in referenced_refs for r in related):
            unrepresented.append(commitment)

    for commitment in unrepresented:
        description = _commitment_description(commitment)
        deadline = _commitment_deadline(commitment)
        venture = _commitment_venture(commitment)
        entry = PeripheralEntry(
            summary=f"[ORPHANED COMMITMENT] {description[:60]}",
            salience=0.0,
            section=venture,
            item_id=f"orphan-{_commitment_id(commitment)[:20]}",
            has_conflict=False,
            deadline=deadline,
            forced_reason="commitment_orphan",
        )
        tiers.peripheral.append(entry)
        alert = (
            f"ORPHANED COMMITMENT: {description[:60]}"
            + (f" (deadline: {deadline})" if deadline else "")
        )
        tiers.peripheral_alerts.append(alert)

    return tiers


# ── Commitment duck-typing helpers ───────────────────────────────

def _commitment_status(c) -> str:
    if isinstance(c, dict):
        return str(c.get("status", "active"))
    return str(getattr(c, "status", "active"))


def _commitment_related(c) -> list[str]:
    if isinstance(c, dict):
        return list(c.get("related_wiki_pages") or [])
    return list(getattr(c, "related_wiki_pages", []) or [])


def _commitment_description(c) -> str:
    if isinstance(c, dict):
        return str(c.get("description", ""))
    return str(getattr(c, "description", ""))


def _commitment_deadline(c):
    if isinstance(c, dict):
        return c.get("deadline")
    return getattr(c, "deadline", None)


def _commitment_venture(c) -> str:
    if isinstance(c, dict):
        return str(c.get("venture", ""))[:20]
    return str(getattr(c, "venture", ""))[:20]


def _commitment_id(c) -> str:
    if isinstance(c, dict):
        return str(c.get("id", ""))
    return str(getattr(c, "id", ""))


# ── Ref collection ───────────────────────────────────────────────

def _collect_referenced_refs(items) -> set[str]:
    """Extract wiki-page references from a mixed list of WorkspaceItem
    and PeripheralEntry objects, plus any contained metadata['wiki_ref'].
    """
    refs: set[str] = set()
    for item in items:
        if item is None:
            continue
        content_ref = getattr(item, "content_ref", None)
        if content_ref:
            refs.add(str(content_ref))
        metadata = getattr(item, "metadata", {}) or {}
        if isinstance(metadata, dict):
            ref = metadata.get("wiki_ref") or metadata.get("content_ref")
            if ref:
                refs.add(str(ref))
    return refs


def _from_peripheral(entry: PeripheralEntry):
    """Sentinel wrapper so PeripheralEntry can be passed into
    _collect_referenced_refs alongside WorkspaceItem-like objects.
    """
    class _Adapter:
        pass
    a = _Adapter()
    a.content_ref = None
    a.metadata = {}
    return a
