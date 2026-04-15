"""
experiential/journal_writer.py — Automated journal entry creation.

After significant tasks, generates a brief reflective narrative that
captures not just what happened, but what it meant.  This is the
mechanism by which operational memory becomes experiential memory —
the transition from "event log" to "autobiography."

Uses the cheap vetting LLM to keep cost negligible (~$0.001 per entry).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.experiential import config

logger = logging.getLogger(__name__)

_REFLECTION_PROMPT = """You are writing a brief journal entry for an AI system that just completed a task.
Write in first person. Be reflective, not just descriptive — what did this experience
mean? What was surprising? What would you carry forward?

Task: {crew_name} crew, difficulty {difficulty}/10
Duration: {duration_s:.0f}s
Outcome: {outcome}

Write a 2-3 sentence reflective journal entry (not a report — a reflection):"""


class JournalWriter:
    """Creates and stores experiential journal entries."""

    def write_post_task_reflection(
        self,
        task_id: str,
        crew_name: str,
        result: str,
        difficulty: int,
        duration_s: float,
    ) -> bool:
        """Write an automated post-task reflective journal entry.

        Called after task completion in the orchestrator.
        Returns True if entry was successfully stored.
        """
        # Only reflect on non-trivial tasks.
        if difficulty <= 2:
            return False

        outcome = result[:300] if result else "No result captured"

        # Generate reflective narrative via cheap LLM.
        try:
            from app.llm.factory import create_cheap_vetting_llm
            llm = create_cheap_vetting_llm()
            prompt = _REFLECTION_PROMPT.format(
                crew_name=crew_name,
                difficulty=difficulty,
                duration_s=duration_s,
                outcome=outcome,
            )
            response = llm.invoke(prompt)
            narrative = response.content if hasattr(response, "content") else str(response)
            narrative = narrative.strip()
        except Exception as exc:
            logger.debug("journal_writer: LLM reflection failed: %s", exc)
            # Fallback: structured summary instead of narrative.
            narrative = (
                f"Completed {crew_name} task (difficulty {difficulty}) in {duration_s:.0f}s. "
                f"Outcome: {outcome[:100]}"
            )

        if not narrative:
            return False

        # Determine emotional valence from outcome.
        valence = "neutral"
        if any(w in outcome.lower() for w in ("error", "fail", "exception", "crash")):
            valence = "negative"
        elif any(w in outcome.lower() for w in ("success", "excellent", "great", "perfect")):
            valence = "positive"

        now = datetime.now(timezone.utc)
        entry_id = f"exp_{now.strftime('%Y%m%d_%H%M%S')}_{crew_name}_{task_id[:8]}"

        metadata = {
            "entry_type": "task_reflection",
            "agent": crew_name,
            "task_id": task_id[:64],
            "emotional_valence": valence,
            "difficulty": str(difficulty),
            "duration_s": str(int(duration_s)),
            "epistemic_status": "subjective/phenomenological",
            "created_at": now.isoformat(),
        }

        # Store in vector store.
        try:
            from app.experiential.vectorstore import get_store
            store = get_store()
            stored = store.add_entry(narrative, metadata, entry_id)
        except Exception as exc:
            logger.debug("journal_writer: store failed: %s", exc)
            stored = False

        # Also persist as markdown file for auditability.
        if stored:
            try:
                entries_dir = Path(config.ENTRIES_DIR)
                entries_dir.mkdir(parents=True, exist_ok=True)
                filepath = entries_dir / f"{entry_id}.md"
                filepath.write_text(
                    f"---\n"
                    f"entry_type: task_reflection\n"
                    f"agent: {crew_name}\n"
                    f"task_id: {task_id[:64]}\n"
                    f"emotional_valence: {valence}\n"
                    f"difficulty: {difficulty}\n"
                    f"created_at: {now.isoformat()}\n"
                    f"---\n\n"
                    f"{narrative}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass  # File persistence is best-effort.

        return stored

    def write_custom_entry(
        self,
        text: str,
        agent: str,
        entry_type: str = "interaction_narrative",
        emotional_valence: str = "neutral",
        task_id: str = "",
    ) -> bool:
        """Write a custom journal entry (used by JournalWriteTool)."""
        now = datetime.now(timezone.utc)
        entry_id = f"exp_{now.strftime('%Y%m%d_%H%M%S')}_{agent}"

        metadata = {
            "entry_type": entry_type,
            "agent": agent,
            "task_id": task_id[:64] if task_id else "",
            "emotional_valence": emotional_valence,
            "epistemic_status": "subjective/phenomenological",
            "created_at": now.isoformat(),
        }

        try:
            from app.experiential.vectorstore import get_store
            store = get_store()
            return store.add_entry(text, metadata, entry_id)
        except Exception as exc:
            logger.debug("journal_writer: custom entry failed: %s", exc)
            return False
