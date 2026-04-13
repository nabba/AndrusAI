"""
SubIA infrastructure-level configuration.

These values are INFRASTRUCTURE-level: they cannot be modified by
agents, the Self-Improver, or any runtime path outside human override
or PDS-driven set-point derivation.

Operational tuning that CAN be agent-mutable lives in
app/self_awareness/sentience_config.py (bounded ±20%, logged).
These two are layered intentionally:
  - This module: what the system IS allowed to become
  - sentience_config: how the system tunes within allowed bounds

Any attempt to patch this dict at runtime should be rejected by
app/subia/safety/setpoint_guard.py (coming in Phase 3).
"""

from __future__ import annotations

# Frozen dict pattern: import SUBIA_CONFIG, treat as read-only.
SUBIA_CONFIG: dict = {
    # ── Scene (GWT-2 + peripheral tier) ──────────────────────────────
    "SCENE_CAPACITY": 5,
    "SCENE_DECAY_RATE": 0.15,
    "SCENE_MIN_SALIENCE": 0.10,
    "PERIPHERAL_CAPACITY": 12,
    "PERIPHERAL_MIN_SALIENCE": 0.05,

    # ── Salience weights (sum to 1.0) ────────────────────────────────
    "SALIENCE_WEIGHTS": {
        "task_relevance":          0.25,
        "homeostatic_impact":      0.20,
        "novelty":                 0.15,
        "cross_reference_density": 0.10,
        "social_relevance":        0.10,
        "prediction_error":        0.10,
        "recency":                 0.05,
        "epistemic_weight":        0.05,
    },
    "EPISTEMIC_WEIGHT_MAP": {
        "factual":      1.0,
        "inferred":     0.8,
        "synthesized":  0.7,
        "speculative":  0.4,
        "creative":     0.2,
    },
    "PLANNING_WEIGHTS": {
        "focal":              1.0,
        "peripheral":         0.3,
        "peripheral_alert":   0.6,
        "strategic_scan":     0.15,
    },

    # ── Homeostasis ──────────────────────────────────────────────────
    "HOMEOSTATIC_VARIABLES": [
        "coherence",
        "safety",
        "trustworthiness",
        "contradiction_pressure",
        "progress",
        "overload",
        "novelty_balance",
        "social_alignment",
        "commitment_load",
    ],
    "HOMEOSTATIC_DEFAULT_SETPOINT": 0.5,
    "HOMEOSTATIC_DEVIATION_THRESHOLD": 0.3,

    # ── Prediction ───────────────────────────────────────────────────
    "PREDICTION_CONFIDENCE_THRESHOLD": 0.6,
    "PREDICTION_HISTORY_WINDOW": 50,
    "PREDICTION_MODEL_TIER": "tier_1",
    "PREDICTION_CACHE_MAX_ENTRIES": 100,
    "PREDICTION_CACHE_MIN_USES": 3,

    # ── Meta-Monitor ─────────────────────────────────────────────────
    "MONITOR_ANOMALY_THRESHOLD": 0.4,
    "MONITOR_KNOWN_UNKNOWNS_LIMIT": 20,

    # ── Social Model ─────────────────────────────────────────────────
    "SOCIAL_MODEL_HUMANS": ["andrus"],
    "SOCIAL_MODEL_UPDATE_FREQUENCY": 5,

    # ── Consolidation (dual-tier memory) ─────────────────────────────
    "CONSOLIDATION_EPISODE_THRESHOLD": 0.5,
    "CONSOLIDATION_RELATION_THRESHOLD": 0.3,
    "HOT_MD_MAX_TOKENS": 500,

    # ── Loop classification ──────────────────────────────────────────
    "FULL_LOOP_OPERATIONS": [
        "ingest", "task_execute", "lint",
        "user_interaction", "cross_venture_synthesis",
    ],
    "COMPRESSED_LOOP_OPERATIONS": [
        "wiki_read", "wiki_search", "routine_query",
    ],

    # ── Cascade tier modulation ──────────────────────────────────────
    "CASCADE_UNCERTAINTY_ESCALATION": True,
    "CASCADE_CONFIDENCE_THRESHOLD": 0.4,
    "CASCADE_PREMIUM_CONFIDENCE_FLOOR": 0.2,

    # ── Safety (DGM extensions) ──────────────────────────────────────
    "SETPOINT_MODIFICATION_ALLOWED": False,
    "AUDIT_SUPPRESSION_ALLOWED": False,
    "NARRATIVE_DRIFT_CHECK_FREQUENCY": 10,

    # ── Performance budget (Amendment B) ─────────────────────────────
    "FULL_LOOP_LATENCY_BUDGET_MS": 1200,
    "COMPRESSED_LOOP_LATENCY_BUDGET_MS": 100,
    "FULL_LOOP_TOKEN_BUDGET": 400,
    "COMPRESSED_LOOP_TOKEN_BUDGET": 0,
}


def get_config() -> dict:
    """Return a shallow copy of SUBIA_CONFIG to discourage in-place mutation."""
    return dict(SUBIA_CONFIG)
