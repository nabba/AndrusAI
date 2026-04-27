"""Technical topic — Phase 13 TSAL host + components + cascade.

Surfaces hardware (CPU/RAM/GPU/disk), running services
(Ollama/Neo4j/Postgres/ChromaDB), available LLM tiers, code structure
(modules/packages count). Ground-truth from running probes, not the
LLM's training memory.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gather() -> dict:
    out: dict = {
        "host": {}, "resources": {}, "components": {},
        "code_summary": {}, "available": False,
    }

    # ── HostProber + ResourceMonitor (no LLM, instant) ──────────────
    try:
        from app.subia.tsal.probers import HostProber, ResourceMonitor
        hp = HostProber().probe()
        rm = ResourceMonitor().probe()
        out["host"] = {
            "cpu_model": hp.cpu_model,
            "cpu_cores_physical": hp.cpu_cores_physical,
            "cpu_cores_logical": hp.cpu_cores_logical,
            "ram_total_gb": hp.ram_total_gb,
            "gpu_model": hp.gpu_model,
            "gpu_unified_memory": hp.gpu_unified_memory,
            "metal_support": hp.metal_support,
            "cuda_support": hp.cuda_support,
            "max_local_model_params_b": hp.max_local_model_params_b,
            "os_name": hp.os_name,
            "os_version": hp.os_version,
            "python_version": hp.python_version,
            "hostname": hp.hostname,
        }
        out["resources"] = {
            "cpu_percent": rm.cpu_percent,
            "ram_used_gb": rm.ram_used_gb,
            "ram_available_gb": rm.ram_available_gb,
            "ram_percent": rm.ram_percent,
            "disk_available_gb": rm.disk_available_gb,
            "compute_pressure": rm.compute_pressure,
            "storage_pressure": rm.storage_pressure,
            "ollama_running": rm.ollama_running,
            "ollama_model_loaded": rm.ollama_model_loaded,
            "neo4j_running": rm.neo4j_running,
            "postgresql_running": rm.postgresql_running,
        }
        out["available"] = True
    except Exception as exc:
        logger.debug("introspection.technical: prober gather failed: %s", exc)

    # ── ComponentDiscovery (knowledge stores + cascade) ─────────────
    try:
        from app.subia.tsal.inspectors import ComponentDiscovery
        ci = ComponentDiscovery().discover()
        out["components"] = {
            "chromadb_available": ci.chromadb.available,
            "chromadb_collections": len(ci.chromadb.collections),
            "chromadb_total_documents": ci.chromadb.total_documents,
            "neo4j_available": ci.neo4j.available,
            "neo4j_node_count": ci.neo4j.node_count,
            "neo4j_relation_count": ci.neo4j.relation_count,
            "mem0_curated_available": ci.mem0.curated_available,
            "ollama_models_installed": [
                m.get("name", "") for m in (ci.ollama.models_installed or [])
            ],
            "wiki_total_pages": ci.wiki.total_pages,
            "wiki_pages_by_section": dict(ci.wiki.pages_by_section or {}),
            "cascade_tiers": [
                {"name": t.get("name"), "model": t.get("model"),
                 "available": t.get("available")}
                for t in (ci.cascade.tiers or [])
            ],
        }
    except Exception as exc:
        logger.debug("introspection.technical: components gather failed: %s", exc)

    # ── Code summary via inspect_codebase (TSAL canonical) ──────────
    try:
        from app.subia.tsal.inspect_tools import inspect_codebase
        cb = inspect_codebase(scope="summary")
        out["code_summary"] = {
            "total_modules": cb.get("total_modules", 0),
            "total_lines": cb.get("total_lines", 0),
            "total_classes": cb.get("total_classes", 0),
            "total_functions": cb.get("total_functions", 0),
            "packages": cb.get("packages", []),
        }
    except Exception as exc:
        logger.debug("introspection.technical: code gather failed: %s", exc)

    return out


def format_section(data: dict) -> str:
    if not data or not data.get("available"):
        return ""
    lines = ["## Technical self-knowledge (Phase 13 TSAL — discovered, not declared)"]

    h = data.get("host", {}) or {}
    if h:
        lines.append("Host hardware:")
        lines.append(
            f"  - CPU: {h.get('cpu_model','?')} "
            f"({h.get('cpu_cores_physical','?')} physical / "
            f"{h.get('cpu_cores_logical','?')} logical)"
        )
        lines.append(
            f"  - RAM: {h.get('ram_total_gb','?')} GB total"
            + (" (unified with GPU)" if h.get("gpu_unified_memory") else "")
        )
        lines.append(
            f"  - GPU: {h.get('gpu_model','?')} "
            f"(metal={h.get('metal_support')}, cuda={h.get('cuda_support')})"
        )
        lines.append(
            f"  - Max local model: ~{h.get('max_local_model_params_b','?')}B parameters"
        )
        lines.append(
            f"  - OS: {h.get('os_name','?')} {h.get('os_version','?')}, "
            f"Python {h.get('python_version','?')}"
        )

    r = data.get("resources", {}) or {}
    if r:
        lines.append("Live resource state:")
        lines.append(
            f"  - CPU: {r.get('cpu_percent','?')}%, "
            f"RAM: {r.get('ram_used_gb','?')} GB used "
            f"({r.get('ram_percent','?')}% — "
            f"{r.get('ram_available_gb','?')} GB available)"
        )
        lines.append(
            f"  - Disk free: {r.get('disk_available_gb','?')} GB"
        )
        lines.append(
            f"  - Pressures: compute={r.get('compute_pressure',0):.2f}, "
            f"storage={r.get('storage_pressure',0):.2f}"
        )
        services = []
        for k, label in [("ollama_running", "Ollama"),
                          ("neo4j_running", "Neo4j"),
                          ("postgresql_running", "Postgres")]:
            services.append(f"{label}={'✓' if r.get(k) else '✗'}")
        lines.append("  - Services: " + ", ".join(services))
        if r.get("ollama_model_loaded"):
            lines.append(f"  - Ollama loaded: {r['ollama_model_loaded']}")

    c = data.get("components", {}) or {}
    if c:
        lines.append("Knowledge stores:")
        lines.append(
            f"  - ChromaDB: {c.get('chromadb_collections',0)} collections, "
            f"{c.get('chromadb_total_documents',0)} documents"
        )
        lines.append(
            f"  - Neo4j: {c.get('neo4j_node_count',0)} nodes, "
            f"{c.get('neo4j_relation_count',0)} relations"
        )
        lines.append(
            f"  - Wiki: {c.get('wiki_total_pages',0)} pages "
            f"across {len(c.get('wiki_pages_by_section', {}))} sections"
        )
        if c.get("ollama_models_installed"):
            lines.append(
                f"  - Local models installed: "
                f"{', '.join(c['ollama_models_installed'][:6])}"
            )
        if c.get("cascade_tiers"):
            avail = [t["name"] for t in c["cascade_tiers"] if t.get("available")]
            lines.append(f"  - Cascade tiers available: {', '.join(avail)}")

    code = data.get("code_summary", {}) or {}
    if code:
        lines.append("Codebase (AST inventory):")
        lines.append(
            f"  - {code.get('total_modules',0)} Python modules, "
            f"{code.get('total_lines',0):,} lines, "
            f"{code.get('total_classes',0)} classes"
        )
        if code.get("packages"):
            lines.append(
                f"  - Top-level packages: "
                f"{', '.join(code['packages'][:12])}"
            )
    return "\n".join(lines)
