"""Phase 13 — Technical Self-Awareness Layer tests.

Covers the four discovery engines (HostProber, ResourceMonitor,
CodeAnalyst, ComponentDiscovery), the seven-page generator, the
operating-principles inferer, the evolution-feasibility gate, the
TSAL→SubIA bridges, and the IdleScheduler refresh registration.

Also asserts the Phase 13 consolidation: the shim at
`app.self_awareness.inspect_tools` is module-identical to the
canonical `app.subia.tsal.inspect_tools` (sys.modules aliasing).
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.subia.kernel import SubjectivityKernel
from app.subia.config import SUBIA_CONFIG


# ─────────────────────────────────────────────────────────────────────
# Consolidation: shim equivalence
# ─────────────────────────────────────────────────────────────────────

def test_shim_alias_module_identical_to_canonical():
    import app.self_awareness.inspect_tools as old
    import app.subia.tsal.inspect_tools as new
    assert old is new, "Phase 13 shim must alias via sys.modules"


def test_shim_exposes_all_legacy_functions():
    from app.self_awareness.inspect_tools import (
        inspect_codebase, inspect_agents, inspect_config, inspect_runtime,
        inspect_memory, inspect_self_model, inspect_beliefs,
        inspect_attention_state, ALL_INSPECT_TOOLS, run_all_inspections,
    )
    assert callable(inspect_codebase)
    assert len(ALL_INSPECT_TOOLS) == 8


def test_canonical_path_is_tsal():
    from app.subia.tsal import inspect_tools
    assert inspect_tools.__name__ == "app.subia.tsal.inspect_tools"


# ─────────────────────────────────────────────────────────────────────
# HostProber
# ─────────────────────────────────────────────────────────────────────

class _StubMem:
    total = 32 * (1024 ** 3)
    available = 16 * (1024 ** 3)
    used = 16 * (1024 ** 3)
    percent = 50.0


class _StubDisk:
    total = 1000 * (1024 ** 3)
    free = 500 * (1024 ** 3)
    used = 500 * (1024 ** 3)


class _StubPsutil:
    @staticmethod
    def cpu_count(logical=True): return 10 if logical else 8
    @staticmethod
    def cpu_percent(interval=None): return 25.0
    @staticmethod
    def virtual_memory(): return _StubMem()
    @staticmethod
    def disk_usage(path): return _StubDisk()
    @staticmethod
    def process_iter(attrs=None):
        return iter([])


def test_host_prober_populates_all_known_fields():
    from app.subia.tsal import HostProber
    host = HostProber(psutil_module=_StubPsutil()).probe()
    assert host.ram_total_gb == 32.0
    assert host.cpu_cores_physical == 8
    assert host.cpu_cores_logical == 10
    assert host.disk_total_gb == 1000.0
    assert host.can_run_local_llm is True
    assert host.max_local_model_params_b > 0
    assert host.probed_at  # ISO timestamp set


def test_host_prober_safe_without_psutil():
    from app.subia.tsal import HostProber
    host = HostProber(psutil_module=None).probe()
    assert host.probed_at  # still populates timestamp
    assert host.ram_total_gb == 0.0


# ─────────────────────────────────────────────────────────────────────
# ResourceMonitor
# ─────────────────────────────────────────────────────────────────────

def test_resource_monitor_derives_pressures():
    from app.subia.tsal import ResourceMonitor
    rs = ResourceMonitor(psutil_module=_StubPsutil()).probe()
    assert rs.cpu_percent == 25.0
    assert rs.ram_percent == 50.0
    assert 0.3 <= rs.compute_pressure <= 0.5
    assert 0.4 <= rs.storage_pressure <= 0.6
    assert rs.available_for_inference_gb == round(16.0 - 4.0, 2)


def test_derive_pressures_pure_helper():
    from app.subia.tsal.probers import derive_pressures
    compute, storage = derive_pressures(80.0, 60.0, 800.0, 200.0)
    assert compute == round(80 / 100 * 0.5 + 60 / 100 * 0.5, 4)
    assert storage == round(800 / 1000, 4)


# ─────────────────────────────────────────────────────────────────────
# CodeAnalyst (adapter over inspect_tools)
# ─────────────────────────────────────────────────────────────────────

def _stub_codebase_dict():
    return {
        "total_modules": 3,
        "total_lines": 1500,
        "total_classes": 12,
        "total_functions": 30,
        "packages": ["subia", "tools", "self_awareness"],
        "modules": [
            {"path": "app/subia/loop.py", "lines": 940, "classes": ["CILLoop"], "imports": ["app.subia.kernel"]},
            {"path": "app/subia/connections/pds_bridge.py", "lines": 200, "classes": ["WikiTool"], "imports": ["app.subia.kernel"]},
            {"path": "app/tools/wiki_tools.py", "lines": 360, "classes": ["WikiWriteTool", "WikiReadTool"], "imports": []},
        ],
    }


def test_code_analyst_uses_inspect_tools_and_extends():
    from app.subia.tsal import CodeAnalyst
    ca = CodeAnalyst(
        inspect_codebase_fn=lambda scope="full": _stub_codebase_dict(),
        inspect_agents_fn=lambda: {"agents": [{"role": "commander"}, {"role": "writer"}]},
    )
    prof = ca.analyze()
    assert prof.total_modules == 3
    assert prof.total_lines == 1500
    assert "subia" in prof.packages
    assert prof.dependencies  # dependency graph built
    assert any("SubIA kernel" in p for p in prof.patterns_detected)
    assert any("wiki" in p.lower() for p in prof.patterns_detected)
    assert any("inter-system bridges" in p for p in prof.patterns_detected)
    assert any(t["name"] == "WikiWriteTool" for t in prof.tools)
    assert prof.agents and prof.agents[0].get("role") == "commander"


def test_code_analyst_safe_when_inspector_fails():
    from app.subia.tsal import CodeAnalyst
    def _boom(**kw): raise RuntimeError("inspect down")
    ca = CodeAnalyst(inspect_codebase_fn=_boom, inspect_agents_fn=_boom)
    prof = ca.analyze()
    assert prof.total_modules == 0
    assert prof.analyzed_at  # timestamp still set


# ─────────────────────────────────────────────────────────────────────
# ComponentDiscovery
# ─────────────────────────────────────────────────────────────────────

def test_component_discovery_populates_inventory():
    from app.subia.tsal import ComponentDiscovery
    inspect_memory = lambda backend="all": {
        "chromadb": {"collections": 2, "details": {
            "andrusai_knowledge": {"count": 100},
            "fiction_inspiration": {"count": 50},
        }},
        "neo4j": {"nodes": 1234, "relationships": 5678},
        "postgresql": {"tables": 4, "table_list": ["public.memories", "public.metadata"]},
    }
    cd = ComponentDiscovery(
        inspect_memory_fn=inspect_memory,
        ollama_lister=lambda: [{"name": "qwen3.5:35b-a3b-q4_K_M", "size": "20GB"}],
        ollama_loaded_detector=lambda: "qwen3.5:35b-a3b-q4_K_M",
        wiki_root="/nonexistent/wiki",
        cascade_tier_config=[{"name": "tier_2", "provider": "deepseek", "model": "v3.2", "api_key": "k"}],
    )
    inv = cd.discover()
    assert inv.chromadb.available
    assert inv.chromadb.total_documents == 150
    assert inv.neo4j.available and inv.neo4j.node_count == 1234
    assert inv.mem0.curated_available
    assert inv.ollama.available
    assert inv.ollama.model_loaded == "qwen3.5:35b-a3b-q4_K_M"
    assert len(inv.cascade.tiers) == 2
    assert inv.cascade.current_default_tier == "tier_1_local"


def test_component_discovery_marks_unavailable_on_error():
    from app.subia.tsal import ComponentDiscovery
    cd = ComponentDiscovery(
        inspect_memory_fn=lambda backend="all": {"chromadb": {"error": "down"}},
        ollama_lister=lambda: [],
        wiki_root="/no/wiki",
    )
    inv = cd.discover()
    assert not inv.chromadb.available
    assert not inv.neo4j.available
    assert inv.discovered_at  # still populated


# ─────────────────────────────────────────────────────────────────────
# Page generators
# ─────────────────────────────────────────────────────────────────────

def _stub_writer_capture():
    captured: list = []
    def writer(slug, body, fm, author):
        captured.append({"slug": slug, "body": body, "fm": fm, "author": author})
        return f"wiki/self/{slug}.md"
    return writer, captured


def test_generators_write_all_seven_pages():
    from app.subia.tsal import PageGenerator, TechnicalSelfModel
    writer, captured = _stub_writer_capture()
    gen = PageGenerator(wiki_writer=writer)
    model = TechnicalSelfModel.assemble()
    paths = gen.generate_all(model)
    assert set(paths.keys()) == {
        "technical-architecture", "host-environment",
        "component-inventory", "resource-state",
        "operating-principles", "code-map", "cascade-profile",
    }
    assert len(captured) == 7
    # every page tagged section=self
    assert all(c["fm"]["section"] == "self" for c in captured)


def test_resource_state_page_warns_on_high_pressure():
    from app.subia.tsal import PageGenerator
    from app.subia.tsal.probers import ResourceState, HostProfile
    writer, captured = _stub_writer_capture()
    gen = PageGenerator(wiki_writer=writer)
    res = ResourceState(compute_pressure=0.9, storage_pressure=0.85,
                        ram_used_gb=10.0, ram_percent=80.0)
    gen.generate_resource_state(res, HostProfile(ram_total_gb=16.0))
    body = captured[0]["body"]
    assert "High compute pressure" in body
    assert "Low disk space" in body


# ─────────────────────────────────────────────────────────────────────
# Operating principles inference
# ─────────────────────────────────────────────────────────────────────

def test_operating_principles_returns_empty_without_predict_fn():
    from app.subia.tsal import infer_operating_principles, TechnicalSelfModel
    out = infer_operating_principles(TechnicalSelfModel.assemble())
    assert out == ""


def test_operating_principles_calls_predict_fn():
    from app.subia.tsal import infer_operating_principles, TechnicalSelfModel
    seen: list = []
    def predict(prompt: str) -> str:
        seen.append(prompt)
        return "Information flows from inputs to outputs through agents."
    out = infer_operating_principles(
        TechnicalSelfModel.assemble(), predict_fn=predict,
    )
    assert "Information flows" in out
    assert seen and "operates" in seen[0].lower()


# ─────────────────────────────────────────────────────────────────────
# Evolution feasibility
# ─────────────────────────────────────────────────────────────────────

def test_evolution_feasibility_passes_when_resources_ample():
    from app.subia.tsal import (
        EvolutionProposal, check_evolution_feasibility,
        TechnicalSelfModel,
    )
    from app.subia.tsal.probers import ResourceState
    model = TechnicalSelfModel.assemble(
        resources=ResourceState(
            available_for_inference_gb=20.0,
            disk_available_gb=500.0,
            compute_pressure=0.2,
        ),
    )
    prop = EvolutionProposal(
        description="add a new caching layer",
        modules_affected=["app/foo.py"],
        estimated_ram_impact_gb=1.0,
        estimated_disk_impact_gb=2.0,
    )
    rep = check_evolution_feasibility(prop, model)
    assert rep.passes


def test_evolution_feasibility_fails_when_compute_saturated():
    from app.subia.tsal import (
        EvolutionProposal, check_evolution_feasibility,
        TechnicalSelfModel,
    )
    from app.subia.tsal.probers import ResourceState
    model = TechnicalSelfModel.assemble(
        resources=ResourceState(
            available_for_inference_gb=20.0,
            disk_available_gb=500.0,
            compute_pressure=0.85,
        ),
    )
    prop = EvolutionProposal(
        description="x", modules_affected=[],
        estimated_ram_impact_gb=0.5, estimated_disk_impact_gb=0.5,
    )
    rep = check_evolution_feasibility(prop, model)
    assert not rep.passes
    assert any(c.name == "compute_headroom" and not c.passed for c in rep.checks)


def test_evolution_feasibility_blast_radius():
    from app.subia.tsal import (
        EvolutionProposal, check_evolution_feasibility,
        TechnicalSelfModel, CodebaseProfile,
    )
    from app.subia.tsal.probers import ResourceState
    cb = CodebaseProfile(dependencies={
        f"app/dep_{i}.py": ["app/foo.py"] for i in range(15)
    })
    model = TechnicalSelfModel.assemble(
        resources=ResourceState(available_for_inference_gb=20, disk_available_gb=500),
        codebase=cb,
    )
    prop = EvolutionProposal(
        description="touch foo", modules_affected=["app/foo.py"],
        estimated_ram_impact_gb=0.1, estimated_disk_impact_gb=0.1,
    )
    rep = check_evolution_feasibility(prop, model)
    assert not rep.passes
    assert len(rep.downstream_modules) == 15


def test_evolution_feasibility_recent_change_overlap():
    from app.subia.tsal import (
        EvolutionProposal, check_evolution_feasibility,
        TechnicalSelfModel,
    )
    from app.subia.tsal.probers import ResourceState
    model = TechnicalSelfModel.assemble(
        resources=ResourceState(available_for_inference_gb=20, disk_available_gb=500),
    )
    prop = EvolutionProposal(
        description="x", modules_affected=["app/risky.py"],
        estimated_ram_impact_gb=0.1, estimated_disk_impact_gb=0.1,
    )
    rep = check_evolution_feasibility(
        prop, model, recent_change_set={"app/risky.py"},
    )
    assert any(c.name == "module_stability" and not c.passed for c in rep.checks)


# ─────────────────────────────────────────────────────────────────────
# TSAL → SubIA bridges
# ─────────────────────────────────────────────────────────────────────

def test_enrich_self_state_populates_capabilities_and_limitations():
    from app.subia.connections.tsal_subia_bridge import enrich_self_state_from_tsal
    from app.subia.tsal import TechnicalSelfModel
    from app.subia.tsal.probers import HostProfile
    from app.subia.tsal.inspectors import ComponentInventory, OllamaState

    kernel = SubjectivityKernel()
    model = TechnicalSelfModel.assemble(
        host=HostProfile(ram_total_gb=8.0, can_run_local_llm=False,
                         max_local_model_params_b=0.0),
        components=ComponentInventory(
            ollama=OllamaState(available=False),
        ),
    )
    diff = enrich_self_state_from_tsal(kernel, model)
    caps = kernel.self_state.capabilities
    lims = kernel.self_state.limitations
    assert caps["local_inference"]["discovered"] is True
    assert caps["local_inference"]["available"] is False
    assert "no_local_inference" in lims
    assert "ollama_unavailable" in lims
    assert "neo4j_unavailable" in lims
    assert "no_local_inference" in diff["limitations"]


def test_enrich_self_state_clears_resolved_limitations():
    from app.subia.connections.tsal_subia_bridge import enrich_self_state_from_tsal
    from app.subia.tsal import TechnicalSelfModel
    from app.subia.tsal.probers import HostProfile
    from app.subia.tsal.inspectors import (
        ComponentInventory, OllamaState, Neo4jState,
    )

    kernel = SubjectivityKernel()
    kernel.self_state.limitations["no_local_inference"] = {"old": True}
    kernel.self_state.limitations["neo4j_unavailable"] = {"old": True}
    kernel.self_state.limitations["ollama_unavailable"] = {"old": True}
    model = TechnicalSelfModel.assemble(
        host=HostProfile(ram_total_gb=64.0, can_run_local_llm=True,
                         max_local_model_params_b=30.0),
        components=ComponentInventory(
            ollama=OllamaState(available=True, model_loaded="qwen3"),
            neo4j=Neo4jState(available=True, node_count=10),
        ),
    )
    enrich_self_state_from_tsal(kernel, model)
    assert "no_local_inference" not in kernel.self_state.limitations
    assert "neo4j_unavailable" not in kernel.self_state.limitations
    assert "ollama_unavailable" not in kernel.self_state.limitations


def test_homeostasis_overload_driven_by_resources():
    from app.subia.connections.tsal_subia_bridge import update_homeostasis_from_resources
    from app.subia.tsal.probers import ResourceState
    kernel = SubjectivityKernel()
    kernel.homeostasis.variables["overload"] = 0.0
    res = ResourceState(compute_pressure=0.8, storage_pressure=0.5,
                        ollama_running=True)
    overload = update_homeostasis_from_resources(kernel, res)
    expected = round(min(1.0, 0.8 * 0.6 + 0.5 * 0.4), 4)
    assert overload == expected
    assert kernel.homeostasis.variables["overload"] == expected


def test_homeostasis_overload_bumps_when_ollama_down():
    from app.subia.connections.tsal_subia_bridge import update_homeostasis_from_resources
    from app.subia.tsal.probers import ResourceState
    kernel = SubjectivityKernel()
    res_ok = ResourceState(compute_pressure=0.3, storage_pressure=0.3,
                           ollama_running=True)
    res_down = ResourceState(compute_pressure=0.3, storage_pressure=0.3,
                             ollama_running=False)
    a = update_homeostasis_from_resources(kernel, res_ok)
    b = update_homeostasis_from_resources(kernel, res_down)
    assert b > a


def test_predictor_prompt_enrichment_includes_constraints():
    from app.subia.connections.tsal_subia_bridge import enrich_prediction_with_technical_context
    from app.subia.tsal import TechnicalSelfModel
    from app.subia.tsal.probers import ResourceState
    model = TechnicalSelfModel.assemble(
        resources=ResourceState(available_for_inference_gb=12.0, compute_pressure=0.5),
    )
    out = enrich_prediction_with_technical_context("Predict X.", model)
    assert "Predict X." in out
    assert "Available inference RAM: 12.0GB" in out
    assert "Compute pressure: 0.50" in out


def test_is_tsal_page_recognises_seven_pages():
    from app.subia.connections.tsal_subia_bridge import is_tsal_page
    assert is_tsal_page("wiki/self/host-environment")
    assert is_tsal_page("wiki/self/resource-state.md")
    assert is_tsal_page("wiki/self/operating-principles")
    assert not is_tsal_page("wiki/self/identity.md")
    assert not is_tsal_page("")


# ─────────────────────────────────────────────────────────────────────
# Idle scheduler refresh registration
# ─────────────────────────────────────────────────────────────────────

def test_register_tsal_jobs_adds_five_jobs():
    from app.subia.idle import IdleScheduler
    from app.subia.tsal import register_tsal_jobs
    sched = IdleScheduler()
    names = register_tsal_jobs(sched)
    assert sorted(names) == sorted([
        "tsal_resources", "tsal_host", "tsal_code",
        "tsal_components", "tsal_principles",
    ])
    assert len(sched.jobs()) == 5


def test_register_tsal_jobs_invokes_callbacks_on_resource_update():
    from app.subia.idle import IdleScheduler
    from app.subia.tsal import register_tsal_jobs, ResourceMonitor
    sched = IdleScheduler()
    notified: list = []
    register_tsal_jobs(
        sched,
        resource_monitor=ResourceMonitor(psutil_module=_StubPsutil()),
        on_resources_updated=lambda rs: notified.append(rs.compute_pressure),
    )
    sched.tick(now=10_000.0)
    assert notified, "resource callback must fire"
    assert 0.0 <= notified[0] <= 1.0


def test_tsal_resources_job_safe_when_no_psutil():
    from app.subia.idle import IdleScheduler
    from app.subia.tsal import register_tsal_jobs, ResourceMonitor, PageGenerator
    sched = IdleScheduler()
    writer = lambda *a: ""
    register_tsal_jobs(
        sched,
        resource_monitor=ResourceMonitor(psutil_module=None),
        page_generator=PageGenerator(wiki_writer=writer),
    )
    rep = sched.tick(now=99_999.0)
    assert all(v.get("ok") is True for v in rep.values()), rep


# ─────────────────────────────────────────────────────────────────────
# Existing callers still see no behavioural change
# ─────────────────────────────────────────────────────────────────────

def test_pre_existing_inspect_callers_keep_working():
    """Phase 13 promise: nothing breaks. Each known existing caller
    can import inspect_tools through the legacy path."""
    # cogito.py callsite
    from app.self_awareness.inspect_tools import inspect_self_model
    # grounding.py callsite
    from app.self_awareness.inspect_tools import inspect_runtime
    # firebase publish.py callsite
    from app.self_awareness.inspect_tools import inspect_memory
    # auto_deployer.py callsite
    from app.self_awareness.inspect_tools import inspect_codebase
    assert all(callable(f) for f in
               (inspect_self_model, inspect_runtime, inspect_memory, inspect_codebase))
