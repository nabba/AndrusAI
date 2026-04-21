"""
subia.memory.consolidator — Amendment C.2 dual-tier write path.

Every task outcome is consolidated into memory. Amendment C splits
this into two tiers:

  mem0_full      "subconscious" — EVERY experience (~200 tokens each)
                 Retains below-threshold records so retrospective
                 promotion can recover them when they become
                 significant later.

  mem0_curated   "conscious" — significant episodes only
                 (~500 tokens each). Smaller index, higher signal,
                 spontaneous-surfacing source.

Significance formula (Amendment C consolidation):

  significance = w_salience    × avg_scene_salience
               + w_pred_error  × |last_prediction_error|
               + w_homeo       × mean_|deviations|
               + w_commitment  × min(1.0, n_active * 0.2)

  w defaults: 0.3, 0.3, 0.2, 0.2

Above `CONSOLIDATION_EPISODE_THRESHOLD` (0.5 by default) → curated.
Every consolidation still writes a lightweight record to the full
tier. Relations above `CONSOLIDATION_RELATION_THRESHOLD` (0.3) go to
Neo4j if a client is provided.

Design: pure duck-typed MemoryClient — tests pass in-memory fakes.
Consolidator mutates nothing in the kernel; it only writes to the
given clients. Never raises.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 7.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# Significance weights — mirror Amendment C.2.
_W_SALIENCE = 0.30
_W_PREDICTION_ERROR = 0.30
_W_HOMEOSTATIC = 0.20
_W_COMMITMENT = 0.20


@dataclass
class ConsolidationResult:
    """Structured record of what the consolidator wrote."""
    significance: float = 0.0
    wrote_full: bool = False
    wrote_curated: bool = False
    relations_written: int = 0
    full_record_id: str | None = None
    curated_record_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "significance": round(self.significance, 4),
            "wrote_full": self.wrote_full,
            "wrote_curated": self.wrote_curated,
            "relations_written": self.relations_written,
            "full_record_id": self.full_record_id,
            "curated_record_id": self.curated_record_id,
        }


def compute_episode_significance(
    kernel,
    task_result: dict | None = None,
    *,
    weights: dict | None = None,
) -> float:
    """Compute the 4-signal significance score for a consolidation.

    Duck-typed over kernel: we read .scene, .predictions,
    .homeostasis.deviations, .self_state.active_commitments. None of
    these are required; missing pieces contribute 0.
    """
    w = weights or {
        "salience": _W_SALIENCE,
        "prediction_error": _W_PREDICTION_ERROR,
        "homeostatic": _W_HOMEOSTATIC,
        "commitment": _W_COMMITMENT,
    }
    s = 0.0

    # 1) scene salience — average focal salience
    try:
        focal = [
            float(getattr(i, "salience_score", getattr(i, "salience", 0.0)))
            for i in (getattr(kernel, "scene", []) or [])[:5]
        ]
        if focal:
            s += w["salience"] * (sum(focal) / len(focal))
    except Exception:
        pass

    # 2) prediction error — last resolved prediction's error magnitude
    try:
        resolved = [
            p for p in (getattr(kernel, "predictions", []) or [])
            if getattr(p, "resolved", False)
            and getattr(p, "prediction_error", None) is not None
        ]
        if resolved:
            s += w["prediction_error"] * abs(
                float(resolved[-1].prediction_error)
            )
    except Exception:
        pass

    # 3) homeostatic impact — mean absolute deviation
    try:
        dev = (getattr(kernel.homeostasis, "deviations", {}) or {})
        if dev:
            mags = [abs(float(v)) for v in dev.values()
                    if isinstance(v, (int, float))]
            if mags:
                s += w["homeostatic"] * (sum(mags) / len(mags))
    except Exception:
        pass

    # 4) commitment relevance
    try:
        active = [
            c for c in (
                getattr(kernel.self_state, "active_commitments", []) or []
            )
            if getattr(c, "status", "active") == "active"
        ]
        s += w["commitment"] * min(1.0, len(active) * 0.2)
    except Exception:
        pass

    # (failure / success isn't re-weighted; it's covered by scene
    # salience and homeostatic shifts from the post-task update)
    return max(0.0, min(1.0, s))


def build_lightweight_record(
    kernel, task_result: dict, agent_role: str,
    operation_type: str, significance: float,
) -> dict:
    """Per Amendment C.2 — ~200 tokens full-tier record.

    Cheap to write, cheap to store. Every experience gets one.
    """
    return {
        "type": "full_record",
        "loop_count": int(getattr(kernel, "loop_count", 0)),
        "agent": str(agent_role)[:40],
        "operation": str(operation_type)[:40],
        "significance": round(float(significance), 3),
        "timestamp": str(getattr(kernel, "last_loop_at", ""))
                     or datetime.now(timezone.utc).isoformat(),
        "scene_topics": [
            str(getattr(i, "content",
                        getattr(i, "summary", "")))[:40]
            for i in (getattr(kernel, "scene", []) or [])[:3]
        ],
        "homeostatic_snapshot": _homeostatic_digest(kernel),
        "prediction_error": _last_prediction_error(kernel),
        "result_summary": str(task_result.get("summary", ""))[:150],
        "promoted_to_curated": significance > SUBIA_CONFIG[
            "CONSOLIDATION_EPISODE_THRESHOLD"
        ],
    }


def build_enriched_episode(
    kernel, task_result: dict, agent_role: str,
    operation_type: str, significance: float,
) -> dict:
    """Per Amendment C.2 — ~500 tokens curated-tier record.

    Only significant experiences get this. Carries the full context
    needed for spontaneous surfacing and retrospective review.

    Phase 12 extension: each scene item in the snapshot carries its
    `processing_mode` (Boundary Sense) + its consolidator_route_for
    preference so retrospective inspection can tell introspective
    from perceptual from imaginative content apart. The per-item
    route comes from the six_proposals_bridges helper.
    """
    try:
        from app.subia.connections.six_proposals_bridges import (
            boundary_route_for_kernel,
        )
        routes = boundary_route_for_kernel(kernel) or {}
    except Exception:
        routes = {}

    scene_snapshot = []
    for i in (getattr(kernel, "scene", []) or [])[:8]:
        item_id = getattr(i, "id", "")
        scene_snapshot.append({
            "summary": str(getattr(i, "content",
                                   getattr(i, "summary", "")))[:80],
            "salience": round(float(
                getattr(i, "salience_score",
                        getattr(i, "salience", 0.0))
            ), 2),
            "affect": str(getattr(i, "dominant_affect", "neutral")),
            "ownership": str(getattr(i, "ownership", "self")),
            "processing_mode": getattr(i, "processing_mode", None),
            "route": routes.get(item_id),
        })

    return {
        "type": "curated_episode",
        "loop_count": int(getattr(kernel, "loop_count", 0)),
        "agent": str(agent_role)[:40],
        "operation": str(operation_type)[:40],
        "significance": round(float(significance), 3),
        "timestamp": str(getattr(kernel, "last_loop_at", ""))
                     or datetime.now(timezone.utc).isoformat(),
        "scene_snapshot": scene_snapshot,
        "homeostatic_state": _homeostatic_full(kernel),
        "prediction": _prediction_digest(kernel),
        "self_state_snapshot": _self_state_digest(kernel),
        "social_model_snapshot": _social_model_digest(kernel),
        "result_summary": str(task_result.get("summary", ""))[:300],
        "wiki_pages_affected": list(task_result.get("wiki_pages_affected", []))[:20],
    }


def extract_relations(kernel, task_result: dict) -> list[dict]:
    """Extract Neo4j-style relations from the task outcome."""
    relations: list[dict] = []

    # Ownership: new wiki pages → OWNED_BY self
    for page in (task_result.get("wiki_pages_created") or [])[:20]:
        relations.append({
            "type": "OWNED_BY",
            "from": str(page)[:200],
            "to": "self",
            "significance": 0.8,
            "properties": {
                "since": str(getattr(kernel, "last_loop_at", "")),
            },
        })

    # Causation: large prediction errors record what caused the surprise
    try:
        resolved = [
            p for p in (getattr(kernel, "predictions", []) or [])
            if getattr(p, "resolved", False)
            and getattr(p, "prediction_error", None) is not None
        ]
        if resolved:
            last = resolved[-1]
            pe = abs(float(last.prediction_error))
            if pe > 0.5:
                relations.append({
                    "type": "CAUSED_STATE_CHANGE",
                    "from": str(task_result.get("summary", ""))[:100],
                    "to": "novelty_balance",
                    "significance": pe,
                    "properties": {
                        "event_id": getattr(last, "id", ""),
                        "variable": "novelty_balance",
                        "magnitude": last.prediction_error,
                    },
                })
    except Exception:
        pass

    return relations


def consolidate(
    kernel,
    task_result: dict,
    agent_role: str,
    operation_type: str,
    *,
    mem0_curated: Any = None,
    mem0_full: Any = None,
    neo4j_client: Any = None,
    episode_threshold: float | None = None,
    relation_threshold: float | None = None,
) -> ConsolidationResult:
    """Amendment C.2 dual-tier consolidation.

    Always writes to mem0_full (when attached). Writes to mem0_curated
    only if significance > episode_threshold. Writes Neo4j relations
    above relation_threshold (if a neo4j_client is attached).

    Args:
        kernel:           SubjectivityKernel providing scene/pred/homeo
                          /self_state state for the digest.
        task_result:      dict from the agent task outcome; supports
                          summary, success, wiki_pages_created,
                          wiki_pages_affected.
        agent_role:       commander/researcher/coder/etc.
        operation_type:   ingest/task_execute/wiki_read/etc.
        mem0_curated:     memory client for the curated tier. None = skip.
        mem0_full:        memory client for the full tier. None = skip.
        neo4j_client:     relation client. None = skip.
        episode_threshold: override SUBIA_CONFIG default (0.5).
        relation_threshold: override SUBIA_CONFIG default (0.3).

    Returns ConsolidationResult with flags for what was written.
    Never raises — individual backend failures are logged and the
    rest of the consolidation still runs.
    """
    episode_threshold = float(
        episode_threshold if episode_threshold is not None
        else SUBIA_CONFIG["CONSOLIDATION_EPISODE_THRESHOLD"]
    )
    relation_threshold = float(
        relation_threshold if relation_threshold is not None
        else SUBIA_CONFIG["CONSOLIDATION_RELATION_THRESHOLD"]
    )

    result = ConsolidationResult()
    result.significance = compute_episode_significance(kernel, task_result)

    # ── Always: full-tier lightweight record ──────────────────
    if mem0_full is not None:
        try:
            full = build_lightweight_record(
                kernel, task_result, agent_role, operation_type,
                result.significance,
            )
            full_id = _safe_add(mem0_full, full)
        except Exception:
            logger.exception("consolidator: full-tier write failed")
            full_id = None
        # A None result from _safe_add means the backend's add raised
        # or returned nothing meaningful — treat as a failed write.
        if full_id is not None:
            result.wrote_full = True
            result.full_record_id = full_id

    # ── Selective: curated-tier enriched episode ──────────────
    if (
        mem0_curated is not None
        and result.significance > episode_threshold
    ):
        try:
            enriched = build_enriched_episode(
                kernel, task_result, agent_role, operation_type,
                result.significance,
            )
            cur_id = _safe_add(mem0_curated, enriched)
        except Exception:
            logger.exception("consolidator: curated-tier write failed")
            cur_id = None
        if cur_id is not None:
            result.wrote_curated = True
            result.curated_record_id = cur_id

    # ── Neo4j relations (curated-only) ────────────────────────
    if neo4j_client is not None and result.wrote_curated:
        try:
            for rel in extract_relations(kernel, task_result):
                if float(rel.get("significance", 0.0)) > relation_threshold:
                    _safe_add_relation(neo4j_client, rel)
                    result.relations_written += 1
        except Exception:
            logger.exception("consolidator: neo4j relations failed")

    return result


# ── Helpers ────────────────────────────────────────────────────

def _homeostatic_digest(kernel) -> dict:
    """Top-3 deviating variables — suitable for the full-tier record."""
    try:
        queue = list(
            getattr(kernel.homeostasis, "restoration_queue", []) or []
        )[:3]
        variables = getattr(kernel.homeostasis, "variables", {}) or {}
        return {
            v: round(float(variables.get(v, 0.5)), 2)
            for v in queue
            if isinstance(variables.get(v, 0.5), (int, float))
        }
    except Exception:
        return {}


def _homeostatic_full(kernel) -> dict:
    """Complete per-variable snapshot for the curated-tier record."""
    try:
        h = kernel.homeostasis
        return {
            v: {
                "value": round(float((h.variables or {}).get(v, 0.5)), 2),
                "setpoint": round(float((h.set_points or {}).get(v, 0.5)), 2),
                "deviation": round(float((h.deviations or {}).get(v, 0.0)), 2),
            }
            for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
        }
    except Exception:
        return {}


def _last_prediction_error(kernel) -> float | None:
    try:
        resolved = [
            p for p in (getattr(kernel, "predictions", []) or [])
            if getattr(p, "resolved", False)
            and getattr(p, "prediction_error", None) is not None
        ]
        if resolved:
            return float(resolved[-1].prediction_error)
    except Exception:
        pass
    return None


def _prediction_digest(kernel) -> dict:
    try:
        preds = list(getattr(kernel, "predictions", []) or [])
        if not preds:
            return {}
        last = preds[-1]
        return {
            "confidence": round(float(getattr(last, "confidence", 0.5)), 2),
            "error": (
                round(float(last.prediction_error), 2)
                if getattr(last, "resolved", False)
                and last.prediction_error is not None else None
            ),
            "predicted_outcome": dict(
                getattr(last, "predicted_outcome", {}) or {}
            ),
        }
    except Exception:
        return {}


def _self_state_digest(kernel) -> dict:
    try:
        ss = kernel.self_state
        return {
            "active_commitments": len(
                getattr(ss, "active_commitments", []) or []
            ),
            "goals": list(getattr(ss, "current_goals", []) or [])[:3],
            "recent_agency": list(
                getattr(ss, "agency_log", []) or []
            )[-3:],
        }
    except Exception:
        return {}


def _social_model_digest(kernel) -> dict:
    try:
        return {
            entity_id: {
                "focus": list(
                    getattr(model, "inferred_focus", []) or []
                )[:3],
                "trust": round(
                    float(getattr(model, "trust_level", 0.7)), 2,
                ),
            }
            for entity_id, model in (
                getattr(kernel, "social_models", {}) or {}
            ).items()
        }
    except Exception:
        return {}


def _safe_add(client: Any, record: dict) -> str | None:
    """Call client.add(record). Supports callables returning an id
    (Mem0-style) or a dict with {'id': ...}.
    """
    add = getattr(client, "add", None)
    if not callable(add):
        return None
    try:
        out = add(record)
    except TypeError:
        # Some clients expect keyword args or specific shapes.
        try:
            out = add(data=record)
        except Exception:
            return None
    except Exception:
        logger.debug("consolidator: client.add raised", exc_info=True)
        return None
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        for k in ("id", "record_id", "_id"):
            if k in out:
                return str(out[k])
    return None


def _safe_add_relation(neo4j_client: Any, relation: dict) -> None:
    fn = getattr(neo4j_client, "add_relation", None) \
         or getattr(neo4j_client, "create_relation", None)
    if not callable(fn):
        return
    try:
        fn(relation)
    except Exception:
        logger.debug("consolidator: neo4j add_relation raised",
                     exc_info=True)
