"""
Consciousness indicator configuration — externalized, tunable parameters.

All thresholds are infrastructure-level (DGM safety invariant):
agents cannot modify workspace capacity, salience weights, or belief thresholds.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("/app/workspace/consciousness_config.json")


@dataclass
class ConsciousnessConfig:
    """Externalized parameters for consciousness indicators."""

    # ── GWT-2: Competitive Workspace ─────────────────────────────────
    workspace_capacity: int = 5           # Max items in workspace simultaneously
    salience_w_goal: float = 0.35         # Goal alignment weight
    salience_w_novelty: float = 0.25      # Novelty weight
    salience_w_urgency: float = 0.15      # Agent-reported urgency weight
    salience_w_surprise: float = 0.25     # PP-1 surprise signal weight (0 until PP-1 active)
    decay_rate: float = 0.05             # Salience decay per cycle
    novelty_floor_pct: float = 0.20      # Top 20% novelty guaranteed entry
    consumption_decay: float = 0.50      # Salience drop after item acted on

    # ── GWT-3: Global Broadcast ──────────────────────────────────────
    reaction_threshold: float = 0.30     # Min relevance for agent to deeply process
    attention_budget: int = 3            # Max broadcasts an agent processes per cycle

    # ── HOT-3: Belief Store ──────────────────────────────────────────
    belief_suspension_threshold: float = 0.20   # Confidence below this → SUSPENDED
    confidence_decay_factor: float = 0.995      # Per-cycle decay for unvalidated beliefs
    mandatory_review_count: int = 3             # Oldest unvalidated beliefs reviewed per slow loop
    disconfirmation_rate: float = 0.15          # Confidence drop per major mismatch
    confirmation_rate: float = 0.05             # Confidence gain per confirmation (slower)
    max_beliefs_per_domain: int = 50            # Cap per domain to prevent unbounded growth


def load_config() -> ConsciousnessConfig:
    """Load config from disk, falling back to defaults."""
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text())
            return ConsciousnessConfig(**{
                k: data[k] for k in data
                if k in ConsciousnessConfig.__dataclass_fields__
            })
    except Exception:
        logger.debug("consciousness config: using defaults")
    return ConsciousnessConfig()


def save_config(config: ConsciousnessConfig) -> None:
    """Persist config to disk (atomic write)."""
    try:
        from app.safe_io import safe_write_json
        safe_write_json(_CONFIG_PATH, {
            k: getattr(config, k)
            for k in ConsciousnessConfig.__dataclass_fields__
        })
    except Exception:
        logger.debug("consciousness config: save failed", exc_info=True)
