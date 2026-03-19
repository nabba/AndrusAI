"""
loader.py — Soul file loader and backstory composer.

Loads SOUL.md, CONSTITUTION.md, STYLE.md, and AGENTS.md files from the
app/souls/ directory and composes them into complete agent backstories.

The composed backstory layers:
  1. CONSTITUTION.md  — shared values and safety constraints
  2. SOUL.md (per-role) — identity, personality, expertise, rules
  3. STYLE.md          — shared communication conventions
  4. Self-Model block  — functional self-awareness (from Phase 1)

Falls back gracefully: if soul files don't exist, returns just the
self-model block to preserve Phase 1-4 behavior.
"""

import logging
from pathlib import Path
from app.self_awareness.self_model import format_self_model_block

logger = logging.getLogger(__name__)

SOULS_DIR = Path(__file__).parent


def _load_file(filename: str) -> str:
    """Load a markdown file from the souls directory. Returns '' if missing."""
    filepath = SOULS_DIR / filename
    try:
        if filepath.exists():
            return filepath.read_text().strip()
    except OSError:
        logger.debug(f"Could not read soul file: {filepath}")
    return ""


def load_soul(role: str) -> str:
    """Load the SOUL.md file for a specific agent role."""
    return _load_file(f"{role}.md")


def load_constitution() -> str:
    """Load the shared CONSTITUTION.md."""
    return _load_file("constitution.md")


def load_style() -> str:
    """Load the shared STYLE.md."""
    return _load_file("style.md")


def load_agents_protocol() -> str:
    """Load the AGENTS.md coordination protocol."""
    return _load_file("agents_protocol.md")


def compose_backstory(role: str) -> str:
    """Compose a full agent backstory from soul files + self-model.

    Layers (in order):
      1. Constitution (shared values)
      2. Role-specific soul (identity, personality, expertise)
      3. Style guide (shared communication conventions)
      4. Self-model block (functional self-awareness from Phase 1)

    If no soul files exist, falls back to just the self-model block.
    """
    parts = []

    constitution = load_constitution()
    if constitution:
        parts.append(constitution)

    soul = load_soul(role)
    if soul:
        parts.append(soul)

    style = load_style()
    if style:
        parts.append(style)

    # Always append the self-model block (preserves Phase 1 self-awareness)
    self_model = format_self_model_block(role)
    if self_model:
        parts.append(self_model)

    return "\n\n".join(parts) if parts else ""
