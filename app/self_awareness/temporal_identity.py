"""
temporal_identity.py — Evolving autobiographical self-narrative.

Implements Butlin et al. VIII-2/3: Temporal Self-Model and Unified Self-Model.
After each cogito reflection cycle, a new "chapter" is appended to a persistent
autobiography. The narrative compresses over time, maintaining identity continuity
across sessions.

This is NOT behavioral mimicry — the system genuinely tracks its evolving
capabilities, challenges, and identity shifts from actual operational data.

Persistence: JSON file (fast) + Mem0 (cross-session survival).
All operations are pure string manipulation — no LLM calls.

DGM Safety: Temporal identity is read-only for agents. Only cogito cycle
can append chapters (infrastructure-level).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_IDENTITY_PATH = Path("/app/workspace/self_awareness_data/temporal_identity.json")


@dataclass
class IdentityChapter:
    """A single chapter in the system's evolving autobiography."""
    epoch: int = 0
    timestamp: str = ""
    summary: str = ""
    capabilities_learned: list[str] = field(default_factory=list)
    challenges_overcome: list[str] = field(default_factory=list)
    identity_shifts: list[str] = field(default_factory=list)
    health_at_time: str = "unknown"


class TemporalSelfModel:
    """Maintains an evolving self-narrative that persists across sessions.

    Chapters are appended by the cogito self-reflection cycle. The narrative
    is compressed to max 500 words and injected into agent context via
    the Priority 5 internal state hook.
    """

    _instance: TemporalSelfModel | None = None

    def __init__(self, max_chapters: int = 50, narrative_max_words: int = 500):
        self.max_chapters = max_chapters
        self.narrative_max_words = narrative_max_words
        self._chapters: list[IdentityChapter] = []
        self._narrative: str = "Identity forming. No history yet."
        self._load()

    @classmethod
    def get_instance(cls) -> TemporalSelfModel:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def update_chapter(self, cogito_report) -> None:
        """Called after each cogito cycle. Appends a new chapter."""
        try:
            chapter = IdentityChapter(
                epoch=len(self._chapters) + 1,
                timestamp=getattr(cogito_report, "timestamp", "")
                    or datetime.now(timezone.utc).isoformat(),
                summary=self._extract_summary(cogito_report),
                capabilities_learned=self._extract_capabilities(cogito_report),
                challenges_overcome=self._extract_challenges(cogito_report),
                identity_shifts=self._detect_shifts(cogito_report),
                health_at_time=getattr(cogito_report, "overall_health", "unknown"),
            )
            self._chapters.append(chapter)
            if len(self._chapters) > self.max_chapters:
                self._compress_old_chapters()
            self._regenerate_narrative()
            self._persist()
            logger.debug(f"temporal_identity: chapter {chapter.epoch} added ({chapter.health_at_time})")
        except Exception:
            logger.debug("temporal_identity: update_chapter failed", exc_info=True)

    def get_narrative(self) -> str:
        """Return compressed self-narrative for context injection."""
        return self._narrative

    def get_chapter_count(self) -> int:
        return len(self._chapters)

    def _extract_summary(self, report) -> str:
        """1-sentence summary from cogito report (no LLM call)."""
        discrepancies = getattr(report, "discrepancies", [])
        proposals = getattr(report, "improvement_proposals", [])
        narrative = getattr(report, "narrative", "") or ""
        n_disc = len(discrepancies) if isinstance(discrepancies, list) else 0
        n_prop = len(proposals) if isinstance(proposals, list) else 0
        health = getattr(report, "overall_health", "unknown")
        return (
            f"Health={health}, {n_disc} discrepancies, {n_prop} proposals. "
            f"{narrative[:100]}"
        )

    def _extract_capabilities(self, report) -> list[str]:
        """Derive capabilities from successful proposals."""
        proposals = getattr(report, "improvement_proposals", [])
        if not isinstance(proposals, list):
            return []
        return [
            (p.get("description", "") if isinstance(p, dict) else str(p))[:100]
            for p in proposals
            if isinstance(p, dict) and p.get("status") == "applied"
        ][:3]

    def _extract_challenges(self, report) -> list[str]:
        """Derive challenges from failure patterns."""
        patterns = getattr(report, "failure_patterns", [])
        if not isinstance(patterns, list):
            return []
        return [
            (p.get("pattern", "") if isinstance(p, dict) else str(p))[:100]
            for p in patterns
        ][:3]

    def _detect_shifts(self, report) -> list[str]:
        """Detect identity shifts by comparing to previous chapter."""
        shifts = []
        health = getattr(report, "overall_health", "unknown")
        if self._chapters:
            prev = self._chapters[-1]
            if prev.health_at_time != health:
                shifts.append(f"Health: {prev.health_at_time} -> {health}")
        return shifts

    def _regenerate_narrative(self) -> None:
        """Compress all chapters into a max-500-word narrative (no LLM)."""
        if not self._chapters:
            self._narrative = "Identity forming. No history yet."
            return

        recent = self._chapters[-10:]
        parts = []
        for ch in recent:
            line = f"Epoch {ch.epoch}: {ch.summary}"
            if ch.identity_shifts:
                line += f" Shifts: {'; '.join(ch.identity_shifts[:2])}"
            parts.append(line)

        full = " | ".join(parts)
        words = full.split()
        if len(words) > self.narrative_max_words:
            full = " ".join(words[:self.narrative_max_words]) + "..."
        self._narrative = full

    def _compress_old_chapters(self) -> None:
        """Merge oldest chapters into a single summary chapter."""
        if len(self._chapters) <= 10:
            return
        keep_recent = 10
        old = self._chapters[:len(self._chapters) - keep_recent]
        summary_ch = IdentityChapter(
            epoch=0,
            timestamp=old[-1].timestamp if old else "",
            summary=f"Compressed history of {len(old)} epochs. "
                    f"Final health: {old[-1].health_at_time if old else 'unknown'}.",
            capabilities_learned=[c for ch in old for c in ch.capabilities_learned][-5:],
            challenges_overcome=[c for ch in old for c in ch.challenges_overcome][-5:],
            identity_shifts=[s for ch in old for s in ch.identity_shifts][-3:],
            health_at_time=old[-1].health_at_time if old else "unknown",
        )
        self._chapters = [summary_ch] + self._chapters[len(old):]

    def _persist(self) -> None:
        """Persist to JSON file + Mem0."""
        data = {
            "chapters": [asdict(ch) for ch in self._chapters],
            "narrative": self._narrative,
        }
        try:
            from app.safe_io import safe_write_json
            _IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
            safe_write_json(_IDENTITY_PATH, data)
        except Exception:
            pass
        try:
            from app.memory.mem0_manager import store_memory
            store_memory(
                f"System identity narrative: {self._narrative[:2000]}",
                agent_id="system_identity",
                metadata={"type": "temporal_identity", "epochs": len(self._chapters)},
            )
        except Exception:
            pass

    def _load(self) -> None:
        """Load from local file."""
        try:
            if _IDENTITY_PATH.exists():
                data = json.loads(_IDENTITY_PATH.read_text())
                self._chapters = [IdentityChapter(**ch) for ch in data.get("chapters", [])]
                self._narrative = data.get("narrative", self._narrative)
                if self._chapters:
                    logger.info(f"temporal_identity: loaded {len(self._chapters)} chapters")
        except Exception:
            logger.debug("temporal_identity: load failed (starting fresh)", exc_info=True)
