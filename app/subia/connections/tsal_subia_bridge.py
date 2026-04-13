"""TSAL → SubIA bridges (Phase 13).

Three bridges:
  1. enrich_self_state_from_tsal — capabilities + limitations from
     discovered (not declared) technical state.
  2. update_homeostasis_from_resources — resource_state.compute_pressure
     + storage_pressure drive the `overload` variable.
  3. enrich_prediction_with_technical_context — predictor prompt gets
     resource constraints injected.

Plus a Boundary Sense helper: TSAL-discovered scene items are tagged
INTROSPECTIVE (deepest introspection — knowledge about the system's
own physical substrate).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.kernel import SubjectivityKernel
from app.subia.tsal.self_model import TechnicalSelfModel
from app.subia.tsal.probers import ResourceState

logger = logging.getLogger(__name__)


# ── Self-state enrichment ────────────────────────────────────────────

def enrich_self_state_from_tsal(
    kernel: SubjectivityKernel,
    model: TechnicalSelfModel,
) -> dict:
    """Populate self_state.capabilities + self_state.limitations from
    TSAL discoveries. Returns the diff for logging.

    `discovered=True` flag distinguishes TSAL-populated entries from
    declared ones, mirroring the Phase 12 `discovered_limitations`
    pattern.
    """
    diff: dict = {"capabilities": [], "limitations": []}
    caps = kernel.self_state.capabilities
    lims = kernel.self_state.limitations

    # local_inference
    new = {
        "available": model.host.can_run_local_llm,
        "max_model_params_b": model.host.max_local_model_params_b,
        "current_model": model.components.ollama.model_loaded,
        "discovered": True,
    }
    if caps.get("local_inference") != new:
        caps["local_inference"] = new
        diff["capabilities"].append("local_inference")

    # knowledge_stores
    new = {
        "chromadb_collections": len(model.components.chromadb.collections),
        "total_indexed_documents": model.components.chromadb.total_documents,
        "wiki_pages": model.components.wiki.total_pages,
        "neo4j_nodes": model.components.neo4j.node_count,
        "neo4j_relations": model.components.neo4j.relation_count,
        "discovered": True,
    }
    if caps.get("knowledge_stores") != new:
        caps["knowledge_stores"] = new
        diff["capabilities"].append("knowledge_stores")

    # registered_tools
    new = {
        "count": len(model.codebase.tools),
        "names": [t.get("name") for t in model.codebase.tools][:50],
        "discovered": True,
    }
    if caps.get("registered_tools") != new:
        caps["registered_tools"] = new
        diff["capabilities"].append("registered_tools")

    # ── Limitations: discovered absences ─────────────────────────────
    if not model.host.can_run_local_llm:
        lim = {
            "description": "Host lacks sufficient RAM for local LLM inference",
            "constraint": f"RAM={model.host.ram_total_gb}GB; need ≥16GB",
            "discovered": True,
        }
        if lims.get("no_local_inference") != lim:
            lims["no_local_inference"] = lim
            diff["limitations"].append("no_local_inference")
    else:
        lims.pop("no_local_inference", None)

    if not model.components.neo4j.available:
        lim = {
            "description": "Neo4j is not running — graph memory disabled",
            "discovered": True,
        }
        if lims.get("neo4j_unavailable") != lim:
            lims["neo4j_unavailable"] = lim
            diff["limitations"].append("neo4j_unavailable")
    else:
        lims.pop("neo4j_unavailable", None)

    if not model.components.ollama.available:
        lim = {
            "description": "Ollama not detected — local Tier-1 inference unavailable",
            "discovered": True,
        }
        if lims.get("ollama_unavailable") != lim:
            lims["ollama_unavailable"] = lim
            diff["limitations"].append("ollama_unavailable")
    else:
        lims.pop("ollama_unavailable", None)
    return diff


# ── Homeostasis driver ──────────────────────────────────────────────

def update_homeostasis_from_resources(
    kernel: SubjectivityKernel,
    resources: ResourceState,
) -> float:
    """Closed-loop: drive the `overload` homeostatic variable from
    physical resource pressure. Returns the new overload value."""
    overload = resources.compute_pressure * 0.6 + resources.storage_pressure * 0.4
    if not resources.ollama_running:
        overload += 0.2
    overload = round(min(1.0, max(0.0, overload)), 4)
    kernel.homeostasis.variables["overload"] = overload
    return overload


# ── Predictor prompt enrichment ─────────────────────────────────────

def enrich_prediction_with_technical_context(
    base_prompt: str,
    model: TechnicalSelfModel,
) -> str:
    res = model.resources
    cmp = model.components
    avail_tiers = [t["name"] for t in cmp.cascade.tiers if t.get("available")]
    suffix = (
        "\n\nTechnical constraints for this prediction:\n"
        f"- Available inference RAM: {res.available_for_inference_gb}GB\n"
        f"- Compute pressure: {res.compute_pressure:.2f}\n"
        f"- Wiki pages: {cmp.wiki.total_pages}\n"
        f"- Cascade tier availability: {avail_tiers}\n"
        "If resource pressure is high, predict smaller operation scope.\n"
    )
    return base_prompt + suffix


# ── Boundary Sense: TSAL pages are deepest introspection ───────────

TSAL_PAGE_PATHS = (
    "wiki/self/host-environment",
    "wiki/self/resource-state",
    "wiki/self/component-inventory",
    "wiki/self/technical-architecture",
    "wiki/self/cascade-profile",
    "wiki/self/code-map",
    "wiki/self/operating-principles",
)


def is_tsal_page(path: str) -> bool:
    if not path:
        return False
    return any(path.startswith(p) for p in TSAL_PAGE_PATHS)
