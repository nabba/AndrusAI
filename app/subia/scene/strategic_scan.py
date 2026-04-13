"""
subia.scene.strategic_scan — Amendment A.3: commander-invokable
wide-view scan.

When the focal scene (5) + peripheral tier (~12) do not surface
something the commander needs to see, the strategic scan returns
a structured overview of what exists outside those tiers.

Design:
  - CALLED ON DEMAND — not on every task. Target cost: ~200 tokens
    per invocation.
  - Filtered to sections with active commitments by default.
  - Read-only. Does not mutate the gate, the kernel, or the scored
    items list.
  - Source of truth is whatever the caller provides as a
    "universe" of candidate items, typically the result of a wiki
    index walk filtered by ventures.

Typical triggers (per Amendment A.3):
  - At the start of a planning session
  - When peripheral alerts suggest something important outside focus
  - When switching venture context
  - Weekly as a general strategic check

This module is the mechanism; the *policy* (when to scan) belongs
to the commander and is a Phase 8 concern.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# Soft cap: keep reports under ~200 tokens per invocation.
_MAX_ENTRIES_PER_SECTION = 5
_MAX_SECTIONS = 6


@dataclass
class StrategicScanEntry:
    """One item in a strategic scan report."""
    item_id: str = ""
    summary: str = ""
    section: str = "unknown"
    salience: float = 0.0
    status: str = ""            # 'active' | 'stale' | 'orphan' | etc.
    deadline: str | None = None

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id[:40],
            "summary": self.summary[:80],
            "section": self.section,
            "salience": round(self.salience, 2),
            "status": self.status,
            "deadline": self.deadline,
        }


@dataclass
class StrategicScanReport:
    """Report returned by run_strategic_scan."""
    by_section: dict = field(default_factory=dict)   # section -> list[Entry]
    total_items: int = 0
    sections_with_commitments: list = field(default_factory=list)
    excluded_in_focal: int = 0
    excluded_in_peripheral: int = 0
    token_estimate: int = 0

    def to_dict(self) -> dict:
        return {
            "by_section": {
                s: [e.to_dict() for e in entries]
                for s, entries in self.by_section.items()
            },
            "total_items": self.total_items,
            "sections_with_commitments": list(self.sections_with_commitments),
            "excluded_in_focal": self.excluded_in_focal,
            "excluded_in_peripheral": self.excluded_in_peripheral,
            "token_estimate": self.token_estimate,
        }


def run_strategic_scan(
    universe: Iterable,
    *,
    focal_ids: Iterable[str] = (),
    peripheral_ids: Iterable[str] = (),
    active_commitments: Iterable = (),
    ventures: Iterable[str] | None = None,
) -> StrategicScanReport:
    """Build a section-grouped report of items outside focal/peripheral.

    Args:
        universe:             the full candidate set (already-scored
                              items — typically a wiki-index walk
                              narrowed by filter).
        focal_ids:            item_ids currently in the focal tier.
        peripheral_ids:       item_ids currently in the peripheral tier.
        active_commitments:   iterable of Commitment-like objects; we
                              only report sections that have at least
                              one active commitment. If None or empty,
                              report all sections.
        ventures:             optional explicit venture filter. If
                              provided, only those section tags pass.

    Returns a StrategicScanReport. Never raises.
    """
    report = StrategicScanReport()
    focal_set = set(focal_ids or ())
    peripheral_set = set(peripheral_ids or ())

    commitment_sections = {
        _commitment_venture(c) for c in (active_commitments or ())
        if _commitment_status(c) == "active"
    }
    commitment_sections.discard("")
    report.sections_with_commitments = sorted(commitment_sections)
    venture_filter = set(ventures or ()) if ventures is not None else None

    grouped: dict[str, list[StrategicScanEntry]] = {}
    try:
        universe_list = list(universe)
    except Exception:
        universe_list = []

    for item in universe_list:
        item_id = str(getattr(item, "item_id", ""))[:40]
        if item_id in focal_set:
            report.excluded_in_focal += 1
            continue
        if item_id in peripheral_set:
            report.excluded_in_peripheral += 1
            continue

        section = _derive_section(item)
        # Filter by ventures / active commitments
        if venture_filter is not None and section not in venture_filter:
            continue
        if commitment_sections and section not in commitment_sections:
            continue

        entry = StrategicScanEntry(
            item_id=item_id,
            summary=(
                str(getattr(item, "content", ""))
                or str(getattr(item, "summary", ""))
            )[:80],
            section=section,
            salience=float(getattr(item, "salience_score", 0.0)),
            status=str(
                (getattr(item, "metadata", {}) or {}).get("status", "")
                if isinstance(getattr(item, "metadata", {}) or {}, dict)
                else ""
            ),
            deadline=(
                (getattr(item, "metadata", {}) or {}).get("deadline")
                if isinstance(getattr(item, "metadata", {}) or {}, dict)
                else None
            ),
        )
        grouped.setdefault(section, []).append(entry)

    # Cap per section + overall sections
    sorted_sections = sorted(
        grouped.keys(),
        key=lambda s: sum(-e.salience for e in grouped[s]),
    )[:_MAX_SECTIONS]
    for section in sorted_sections:
        entries = sorted(grouped[section],
                         key=lambda e: e.salience, reverse=True)
        report.by_section[section] = entries[:_MAX_ENTRIES_PER_SECTION]

    report.total_items = sum(len(v) for v in report.by_section.values())
    report.token_estimate = _estimate_tokens(report)

    return report


def format_scan_block(report: StrategicScanReport) -> str:
    """Render a StrategicScanReport as a terse context-injection block.

    Target: ≤200 tokens. Used by the commander when surfacing scan
    results to downstream crews.
    """
    if report.total_items == 0:
        return "[strategic_scan: no items outside focal/peripheral]"
    lines = ["[strategic_scan]"]
    for section, entries in report.by_section.items():
        lines.append(f"{section}:")
        for entry in entries:
            flags = ""
            if entry.deadline:
                flags += f" [deadline: {entry.deadline}]"
            if entry.status and entry.status != "active":
                flags += f" [{entry.status}]"
            lines.append(
                f"  · {entry.summary} (s:{entry.salience:.2f}){flags}"
            )
    lines.append("[/strategic_scan]")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────

def _derive_section(item) -> str:
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


def _commitment_status(c) -> str:
    if isinstance(c, dict):
        return str(c.get("status", "active"))
    return str(getattr(c, "status", "active"))


def _commitment_venture(c) -> str:
    if isinstance(c, dict):
        return str(c.get("venture", ""))[:20]
    return str(getattr(c, "venture", ""))[:20]


def _estimate_tokens(report: StrategicScanReport) -> int:
    """Rough token estimate: ~4 chars/token."""
    total_chars = 0
    for entries in report.by_section.values():
        for e in entries:
            total_chars += len(e.summary) + len(e.section) + 20
    total_chars += sum(
        len(s) + 4 for s in report.by_section
    )
    return total_chars // 4
