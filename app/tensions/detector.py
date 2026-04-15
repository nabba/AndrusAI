"""
tensions/detector.py — Automatic tension detection between retrieved passages.

When cross-KB retrieval returns content that appears to conflict — e.g.,
philosophy says X but experience shows Y — this module detects the tension
and records it.

Uses the cheap vetting LLM to keep cost negligible.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DETECTION_PROMPT = """You are analyzing two text passages for genuine intellectual tension or contradiction.

Passage A (from {source_a}):
{text_a}

Passage B (from {source_b}):
{text_b}

Context: {context}

Do these passages contain a genuine tension, contradiction, or unresolved conflict?
If YES, respond with a JSON object:
{{"is_tension": true, "tension_type": "principle_conflict|philosophy_vs_experience|competing_values|unresolved_question", "pole_a": "brief description of position A", "pole_b": "brief description of position B", "summary": "one-sentence description of the tension"}}
If NO (they're complementary, unrelated, or one subsumes the other), respond:
{{"is_tension": false}}

JSON only:"""


def detect_tension(
    text_a: str,
    text_b: str,
    context: str = "",
    source_a: str = "KB A",
    source_b: str = "KB B",
    detected_by: str = "system",
) -> dict | None:
    """Check if two passages contain a genuine tension.

    Returns a tension dict if detected, None otherwise.
    Cost: ~$0.001 per call via cheap vetting LLM.
    """
    if not text_a or not text_b:
        return None

    try:
        import json
        import re
        from app.llm.factory import create_cheap_vetting_llm

        llm = create_cheap_vetting_llm()
        prompt = _DETECTION_PROMPT.format(
            text_a=text_a[:500],
            text_b=text_b[:500],
            context=context[:200],
            source_a=source_a,
            source_b=source_b,
        )
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None

        parsed = json.loads(match.group())
        if not parsed.get("is_tension"):
            return None

        return {
            "tension_type": parsed.get("tension_type", "unresolved_question"),
            "pole_a": parsed.get("pole_a", text_a[:100]),
            "pole_b": parsed.get("pole_b", text_b[:100]),
            "summary": parsed.get("summary", ""),
            "source_a": source_a,
            "source_b": source_b,
            "detected_by": detected_by,
            "context": context[:200],
        }
    except Exception as exc:
        logger.debug("tension detection failed: %s", exc)
        return None


def detect_and_store(
    text_a: str,
    text_b: str,
    context: str = "",
    source_a: str = "KB A",
    source_b: str = "KB B",
    detected_by: str = "system",
) -> bool:
    """Detect a tension and store it in the tensions KB if genuine."""
    result = detect_tension(text_a, text_b, context, source_a, source_b, detected_by)
    if result is None:
        return False

    now = datetime.now(timezone.utc)
    summary = result.get("summary", "")
    full_text = (
        f"Tension: {summary}\n\n"
        f"Pole A ({result['source_a']}): {result['pole_a']}\n\n"
        f"Pole B ({result['source_b']}): {result['pole_b']}"
    )

    metadata = {
        "tension_type": result["tension_type"],
        "pole_a": result["pole_a"][:200],
        "pole_b": result["pole_b"][:200],
        "detected_by": detected_by,
        "context": context[:200],
        "resolution_status": "unresolved",
        "epistemic_status": "unresolved/dialectical",
        "created_at": now.isoformat(),
    }

    try:
        from app.tensions.vectorstore import get_store
        store = get_store()
        tension_id = f"ten_{now.strftime('%Y%m%d_%H%M%S')}_{detected_by}"
        return store.add_tension(full_text, metadata, tension_id)
    except Exception as exc:
        logger.debug("tension storage failed: %s", exc)
        return False
