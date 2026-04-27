---
title: "tsal-technical-self-awareness-spec.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# Technical Self-Awareness Layer (TSAL) — Specification

## Purpose

AndrusAI must know what it IS technically — its code, its components, its host machine, its resource constraints — through continuous self-inspection, not static declaration. This knowledge must be:

1. **Discovered** — generated from introspection, not written by hand
2. **Current** — refreshed on schedule, stale-tracked like any wiki page
3. **Wiki-native** — stored as standard wiki pages in wiki/self/, queryable by all agents
4. **SubIA-wired** — feeds self_state, homeostasis, predictor, and meta_monitor
5. **Actionable** — Commander and Self-Improver USE this knowledge for planning and evolution

No static declarations. If the system moves to a different machine, gains a new tool, or loses a ChromaDB collection, TSAL detects the change and updates the self-model.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    TSAL Runtime                       │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ Host Prober  │  │ Code Analyst  │  │ Component   │ │
│  │              │  │              │  │ Discovery   │ │
│  │ CPU/RAM/GPU  │  │ AST parsing  │  │ ChromaDB    │ │
│  │ disk/network │  │ module map   │  │ Mem0/Neo4j  │ │
│  │ OS/processes │  │ tool registry│  │ LLM cascade │ │
│  │ thermals     │  │ class graph  │  │ wiki state  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘ │
│         │                  │                  │        │
│         └──────────┬───────┴──────────┬───────┘        │
│                    │                  │                 │
│              ┌─────▼──────┐   ┌──────▼───────┐        │
│              │ Technical   │   │ Resource     │        │
│              │ Self-Model  │   │ Monitor      │        │
│              │ Generator   │   │ (continuous) │        │
│              └─────┬───────┘   └──────┬───────┘        │
│                    │                  │                 │
└────────────────────┼──────────────────┼─────────────────┘
                     │                  │
          ┌──────────▼──────────────────▼──────────┐
          │          wiki/self/ pages               │
          │                                         │
          │  technical-architecture.md               │
          │  host-environment.md                     │
          │  component-inventory.md                  │
          │  resource-state.md (high-frequency)      │
          │  operating-principles.md                 │
          │  code-map.md                             │
          │  cascade-profile.md                      │
          └──────────────────┬──────────────────────┘
                             │
               ┌─────────────┼─────────────────┐
               │             │                 │
               ▼             ▼                 ▼
          SubIA Kernel   Commander        Self-Improver
          (self_state,   (resource-aware   (evolution
          homeostasis,   task planning)    planning)
          predictor)
```

---

## 2. The Three Discovery Engines

### 2.1 Host Prober

Discovers the physical and operating environment. Runs system calls, parses output, produces structured data. No LLM needed — pure Python.

```python
# src/subia/tsal/host_prober.py

"""
Host Prober — discovers the physical and OS environment.

Runs on startup and periodically (configurable, default every 30 minutes
for resource state, daily for hardware profile).

All discovery is through system calls and file reads.
Zero LLM tokens consumed.
"""

import os
import platform
import subprocess
import json
import psutil
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HostProfile:
    """Discovered host machine profile."""

    # Hardware
    cpu_model: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_architecture: str = ""
    ram_total_gb: float = 0.0
    gpu_model: str = ""
    gpu_memory_gb: float = 0.0
    gpu_unified_memory: bool = False       # True for Apple Silicon
    disk_total_gb: float = 0.0
    disk_available_gb: float = 0.0

    # Operating system
    os_name: str = ""
    os_version: str = ""
    hostname: str = ""
    python_version: str = ""

    # Capabilities (inferred)
    can_run_local_llm: bool = False
    max_local_model_params_b: float = 0.0  # Estimated max model size
    has_gpu_acceleration: bool = False
    metal_support: bool = False            # Apple Metal
    cuda_support: bool = False             # NVIDIA CUDA


@dataclass
class ResourceState:
    """Current resource utilization. High-frequency refresh."""

    cpu_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_available_gb: float = 0.0
    ram_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_available_gb: float = 0.0
    gpu_utilization_percent: float = 0.0   # If available
    gpu_memory_used_gb: float = 0.0        # If available

    # Process-specific
    ollama_running: bool = False
    ollama_model_loaded: str = ""
    ollama_ram_gb: float = 0.0
    neo4j_running: bool = False
    postgresql_running: bool = False
    crewai_process_ram_gb: float = 0.0

    # Derived constraints
    available_for_inference_gb: float = 0.0  # RAM headroom for model loading
    storage_pressure: float = 0.0            # 0.0-1.0, feeds homeostasis.overload
    compute_pressure: float = 0.0            # 0.0-1.0, feeds homeostasis.overload

    probed_at: str = ""


def probe_host_profile() -> HostProfile:
    """
    Full hardware and OS discovery. Run daily or on startup.
    """
    profile = HostProfile()

    # CPU
    profile.cpu_architecture = platform.machine()
    profile.cpu_cores_physical = psutil.cpu_count(logical=False) or 0
    profile.cpu_cores_logical = psutil.cpu_count(logical=True) or 0

    # CPU model (platform-specific)
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            profile.cpu_model = result.stdout.strip()
        except Exception:
            profile.cpu_model = platform.processor()

        # Apple Silicon GPU/unified memory detection
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                capture_output=True, text=True, timeout=10
            )
            gpu_data = json.loads(result.stdout)
            displays = gpu_data.get("SPDisplaysDataType", [])
            if displays:
                profile.gpu_model = displays[0].get("sppci_model", "")
                # Apple Silicon uses unified memory
                if "Apple" in profile.gpu_model:
                    profile.gpu_unified_memory = True
                    profile.metal_support = True
                    profile.has_gpu_acceleration = True
        except Exception:
            pass
    else:
        profile.cpu_model = platform.processor()

    # RAM
    mem = psutil.virtual_memory()
    profile.ram_total_gb = round(mem.total / (1024 ** 3), 1)

    # GPU memory (for unified memory, same as RAM)
    if profile.gpu_unified_memory:
        profile.gpu_memory_gb = profile.ram_total_gb
    # TODO: NVIDIA GPU detection via nvidia-smi for non-Mac hosts

    # Disk
    disk = psutil.disk_usage("/")
    profile.disk_total_gb = round(disk.total / (1024 ** 3), 1)
    profile.disk_available_gb = round(disk.free / (1024 ** 3), 1)

    # OS
    profile.os_name = platform.system()
    profile.os_version = platform.release()
    profile.hostname = platform.node()
    profile.python_version = platform.python_version()

    # Capability inference
    profile.can_run_local_llm = profile.ram_total_gb >= 16
    if profile.gpu_unified_memory:
        # Apple Silicon: rough heuristic for max model size
        # ~4 bits per parameter, need ~75% of unified memory
        usable_gb = profile.ram_total_gb * 0.75
        profile.max_local_model_params_b = round(usable_gb / 0.5, 1)  # q4 quantization
    elif profile.has_gpu_acceleration:
        profile.max_local_model_params_b = round(profile.gpu_memory_gb / 0.5, 1)
    else:
        profile.max_local_model_params_b = round(profile.ram_total_gb * 0.4 / 0.5, 1)

    return profile


def probe_resource_state() -> ResourceState:
    """
    Current resource utilization snapshot. Run every 30 minutes
    or before significant operations.
    """
    from datetime import datetime, timezone

    state = ResourceState()

    # CPU
    state.cpu_percent = psutil.cpu_percent(interval=1)

    # RAM
    mem = psutil.virtual_memory()
    state.ram_used_gb = round(mem.used / (1024 ** 3), 2)
    state.ram_available_gb = round(mem.available / (1024 ** 3), 2)
    state.ram_percent = mem.percent

    # Disk
    disk = psutil.disk_usage("/")
    state.disk_used_gb = round(disk.used / (1024 ** 3), 2)
    state.disk_available_gb = round(disk.free / (1024 ** 3), 2)

    # Process discovery
    for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
        try:
            name = proc.info["name"].lower()
            mem_gb = round(proc.info["memory_info"].rss / (1024 ** 3), 2)

            if "ollama" in name:
                state.ollama_running = True
                state.ollama_ram_gb = max(state.ollama_ram_gb, mem_gb)
                # Try to detect loaded model
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "serve" in cmdline or "runner" in cmdline:
                    state.ollama_model_loaded = _detect_ollama_model()

            elif "neo4j" in name or "java" in name:
                # Heuristic: large Java process is likely Neo4j
                if mem_gb > 0.5:
                    state.neo4j_running = True

            elif "postgres" in name:
                state.postgresql_running = True

            elif "python" in name and "crewai" in " ".join(proc.info.get("cmdline") or []):
                state.crewai_process_ram_gb = max(state.crewai_process_ram_gb, mem_gb)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Derived constraints
    state.available_for_inference_gb = round(
        state.ram_available_gb - 4.0,  # Reserve 4GB for OS and other processes
        2
    )
    state.storage_pressure = min(1.0, (state.disk_used_gb / max(1, state.disk_used_gb + state.disk_available_gb)))
    state.compute_pressure = min(1.0, state.cpu_percent / 100.0 * 0.5 + state.ram_percent / 100.0 * 0.5)
    state.probed_at = datetime.now(timezone.utc).isoformat()

    return state


def _detect_ollama_model() -> str:
    """Detect which model Ollama currently has loaded."""
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            # Parse the model name from the first data row
            return lines[1].split()[0]
    except Exception:
        pass
    return ""
```

### 2.2 Code Analyst

Discovers the codebase structure, tools, agents, and their relationships. Builds on the existing `self_awareness` package's AST tools but extends them to produce wiki-native output.

```python
# src/subia/tsal/code_analyst.py

"""
Code Analyst — discovers the codebase structure through AST analysis.

Extends the existing self_awareness package's AST introspection.
Produces structured data about: modules, classes, tools, agents,
configuration, and inter-module dependencies.

Runs on startup and when file modification timestamps change.
Uses AST parsing (no LLM needed for structure discovery).
Uses Tier 1 LLM for operating principle inference (~500 tokens).
"""

import ast
import os
import importlib
import inspect
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class ModuleInfo:
    path: str
    name: str
    classes: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    docstring: str = ""
    line_count: int = 0
    last_modified: str = ""


@dataclass
class ToolInfo:
    name: str
    class_name: str
    module_path: str
    description: str
    arguments: list = field(default_factory=list)
    agent_access: list = field(default_factory=list)  # Which agents can use this tool


@dataclass
class AgentInfo:
    role: str
    goal: str
    backstory_summary: str
    tools: list = field(default_factory=list)
    wiki_permissions: dict = field(default_factory=dict)  # From WIKI_ROLES


@dataclass
class CodebaseProfile:
    """Complete discovered codebase structure."""
    project_root: str = ""
    total_modules: int = 0
    total_lines: int = 0
    total_classes: int = 0
    total_tools: int = 0

    modules: list = field(default_factory=list)       # List of ModuleInfo
    tools: list = field(default_factory=list)          # List of ToolInfo
    agents: list = field(default_factory=list)         # List of AgentInfo
    config_files: list = field(default_factory=list)   # Discovered config paths
    dependencies: dict = field(default_factory=dict)   # Module → [imported modules]

    # Architecture patterns detected
    patterns_detected: list = field(default_factory=list)
    # e.g., "lifecycle hooks", "four-tier cascade", "page-level locking"

    analyzed_at: str = ""


def analyze_codebase(project_root: str) -> CodebaseProfile:
    """
    Full codebase analysis via AST parsing and reflection.
    
    Discovers:
    1. All Python modules and their structure
    2. All CrewAI tools (classes inheriting BaseTool)
    3. All agents (instances of crewai.Agent)
    4. Configuration files and their contents
    5. Inter-module dependency graph
    6. Architectural patterns (lifecycle hooks, cascade, etc.)
    """
    from datetime import datetime, timezone

    profile = CodebaseProfile(
        project_root=project_root,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )

    # Walk the source tree
    src_root = os.path.join(project_root, "src")
    if not os.path.exists(src_root):
        src_root = project_root

    for root, dirs, files in os.walk(src_root):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in {
            "__pycache__", "node_modules", ".git", "venv", ".venv", "env"
        }]

        for fname in files:
            if not fname.endswith(".py"):
                continue

            fpath = os.path.join(root, fname)
            module = _analyze_module(fpath, project_root)
            if module:
                profile.modules.append(module)
                profile.total_lines += module.line_count

                # Detect tools
                for cls in module.classes:
                    if cls.get("bases") and "BaseTool" in str(cls["bases"]):
                        tool = _extract_tool_info(cls, module)
                        if tool:
                            profile.tools.append(tool)

                # Track dependencies
                profile.dependencies[module.name] = module.imports

    profile.total_modules = len(profile.modules)
    profile.total_classes = sum(len(m.classes) for m in profile.modules)
    profile.total_tools = len(profile.tools)

    # Discover agents from crew configuration
    profile.agents = _discover_agents(project_root)

    # Discover config files
    profile.config_files = _discover_configs(project_root)

    # Detect architectural patterns
    profile.patterns_detected = _detect_patterns(profile)

    return profile


def _analyze_module(filepath: str, project_root: str) -> Optional[ModuleInfo]:
    """Parse a single Python module via AST."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)
        rel_path = os.path.relpath(filepath, project_root)

        module = ModuleInfo(
            path=rel_path,
            name=rel_path.replace("/", ".").replace(".py", ""),
            line_count=len(source.split("\n")),
            last_modified=str(os.path.getmtime(filepath)),
        )

        # Extract docstring
        if (tree.body and isinstance(tree.body[0], ast.Expr) and
                isinstance(tree.body[0].value, (ast.Constant, ast.Str))):
            module.docstring = ast.literal_eval(tree.body[0].value)[:200] if isinstance(
                tree.body[0].value, ast.Constant) else ""

        # Extract classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [
                    (b.id if isinstance(b, ast.Name) else
                     f"{b.value.id}.{b.attr}" if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name)
                     else str(b))
                    for b in node.bases
                ]
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                module.classes.append({
                    "name": node.name,
                    "bases": bases,
                    "methods": methods,
                    "line": node.lineno,
                })

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions only
                if isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                    module.functions.append({
                        "name": node.name,
                        "args": [a.arg for a in node.args.args if a.arg != "self"],
                        "line": node.lineno,
                    })

            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module.imports.append(alias.name)
                elif node.module:
                    module.imports.append(node.module)

        return module

    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _extract_tool_info(cls_info: dict, module: ModuleInfo) -> Optional[ToolInfo]:
    """Extract tool metadata from a class definition."""
    return ToolInfo(
        name=cls_info["name"],
        class_name=cls_info["name"],
        module_path=module.path,
        description="",  # Populated from class docstring or name field
        arguments=[],  # Populated from _run() signature
    )


def _discover_agents(project_root: str) -> list:
    """Discover agent definitions from crew configuration files."""
    agents = []
    # Search for files containing Agent() instantiation
    # Parse crew.py or similar configuration
    # Extract role, goal, backstory, tools list
    return agents


def _discover_configs(project_root: str) -> list:
    """Find configuration files."""
    config_patterns = [
        "*.yaml", "*.yml", "*.json", "*.toml", "*.env",
        "wiki_schema/*.md", "src/subia/config.py"
    ]
    configs = []
    for pattern in config_patterns:
        from glob import glob
        configs.extend(glob(os.path.join(project_root, "**", pattern), recursive=True))
    return [os.path.relpath(c, project_root) for c in configs[:50]]


def _detect_patterns(profile: CodebaseProfile) -> list:
    """Detect architectural patterns from codebase structure."""
    patterns = []

    # Check for lifecycle hooks
    hook_modules = [m for m in profile.modules if "hook" in m.name.lower() or "lifecycle" in m.name.lower()]
    if hook_modules:
        patterns.append("lifecycle-hooks (crewai-amendments pattern)")

    # Check for cascade/tiered processing
    cascade_modules = [m for m in profile.modules if "cascade" in m.name.lower() or "tier" in m.name.lower()]
    if cascade_modules:
        patterns.append("multi-tier LLM cascade")

    # Check for SubIA
    subia_modules = [m for m in profile.modules if "subia" in m.name.lower()]
    if subia_modules:
        patterns.append(f"SubIA kernel ({len(subia_modules)} modules)")

    # Check for wiki tools
    wiki_tools = [t for t in profile.tools if "wiki" in t.name.lower()]
    if wiki_tools:
        patterns.append(f"LLM Wiki subsystem ({len(wiki_tools)} tools)")

    # Check for DGM safety
    safety_modules = [m for m in profile.modules if "safety" in m.name.lower() or "dgm" in m.name.lower()]
    if safety_modules:
        patterns.append("DGM safety invariant")

    # Check for page-level locking
    for m in profile.modules:
        if any("lock" in f["name"].lower() for f in m.functions):
            patterns.append("page-level locking (concurrency control)")
            break

    return patterns
```

### 2.3 Component Discovery

Discovers what external services and data stores are available.

```python
# src/subia/tsal/component_discovery.py

"""
Component Discovery — probes external services and data stores.

Discovers what ChromaDB collections exist, what Neo4j schema is active,
what Mem0 instances are running, what Ollama models are available,
and what the wiki state is.

All discovery is through API calls and file system inspection.
"""

from dataclasses import dataclass, field


@dataclass
class ChromaDBState:
    collections: list = field(default_factory=list)  # [{name, count, metadata_schema}]
    total_documents: int = 0
    embedding_model: str = ""
    server_address: str = ""
    available: bool = False


@dataclass
class Neo4jState:
    node_count: int = 0
    relation_count: int = 0
    relation_types: list = field(default_factory=list)
    node_labels: list = field(default_factory=list)
    server_address: str = ""
    available: bool = False


@dataclass
class Mem0State:
    curated_episode_count: int = 0
    full_record_count: int = 0
    curated_available: bool = False
    full_available: bool = False
    pgvector_available: bool = False


@dataclass
class OllamaState:
    available: bool = False
    models_installed: list = field(default_factory=list)  # [{name, size_gb, quantization}]
    model_loaded: str = ""
    server_address: str = ""


@dataclass
class WikiState:
    total_pages: int = 0
    pages_by_section: dict = field(default_factory=dict)
    total_raw_sources: int = 0
    active_contradictions: int = 0
    stale_pages: int = 0
    last_lint_at: str = ""
    wiki_root: str = ""
    disk_usage_mb: float = 0.0


@dataclass
class CascadeProfile:
    """Discovered LLM cascade configuration."""
    tiers: list = field(default_factory=list)
    # Each tier: {name, provider, model, context_window, cost_per_1k_tokens,
    #             available, latency_estimate_ms}
    current_default_tier: str = ""
    budget_remaining_today: float = 0.0  # From Paperclip


@dataclass
class ComponentInventory:
    """Complete discovered component state."""
    chromadb: ChromaDBState = field(default_factory=ChromaDBState)
    neo4j: Neo4jState = field(default_factory=Neo4jState)
    mem0: Mem0State = field(default_factory=Mem0State)
    ollama: OllamaState = field(default_factory=OllamaState)
    wiki: WikiState = field(default_factory=WikiState)
    cascade: CascadeProfile = field(default_factory=CascadeProfile)
    firecrawl_available: bool = False
    subia_active: bool = False
    subia_loop_count: int = 0
    discovered_at: str = ""


def discover_components(project_root: str, config: dict) -> ComponentInventory:
    """
    Probe all external services and data stores.
    
    Uses API calls (ChromaDB, Neo4j, Mem0, Ollama) and file system
    inspection (wiki) to build a complete component inventory.
    """
    from datetime import datetime, timezone
    
    inventory = ComponentInventory(
        discovered_at=datetime.now(timezone.utc).isoformat()
    )
    
    # ChromaDB
    inventory.chromadb = _probe_chromadb(config)
    
    # Neo4j
    inventory.neo4j = _probe_neo4j(config)
    
    # Mem0
    inventory.mem0 = _probe_mem0(config)
    
    # Ollama
    inventory.ollama = _probe_ollama()
    
    # Wiki
    inventory.wiki = _probe_wiki(project_root)
    
    # Cascade
    inventory.cascade = _build_cascade_profile(inventory.ollama, config)
    
    # Firecrawl
    inventory.firecrawl_available = _check_firecrawl(config)
    
    return inventory


def _probe_chromadb(config: dict) -> ChromaDBState:
    """Probe ChromaDB for available collections and their sizes."""
    state = ChromaDBState()
    try:
        import chromadb
        client = chromadb.Client()  # Or PersistentClient with config path
        collections = client.list_collections()
        state.available = True
        state.collections = []
        for col in collections:
            count = col.count()
            state.collections.append({
                "name": col.name,
                "count": count,
            })
            state.total_documents += count
    except Exception:
        state.available = False
    return state


def _probe_neo4j(config: dict) -> Neo4jState:
    """Probe Neo4j for schema and statistics."""
    state = Neo4jState()
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            config.get("NEO4J_URI", "bolt://localhost:7687"),
            auth=(config.get("NEO4J_USER", "neo4j"), config.get("NEO4J_PASS", ""))
        )
        with driver.session() as session:
            # Node count
            result = session.run("MATCH (n) RETURN count(n) AS count")
            state.node_count = result.single()["count"]
            # Relation count and types
            result = session.run("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count")
            for record in result:
                state.relation_types.append({"type": record["type"], "count": record["count"]})
            state.relation_count = sum(r["count"] for r in state.relation_types)
            # Node labels
            result = session.run("CALL db.labels()")
            state.node_labels = [record[0] for record in result]
            state.available = True
        driver.close()
    except Exception:
        state.available = False
    return state


def _probe_ollama() -> OllamaState:
    """Probe Ollama for available models."""
    state = OllamaState()
    try:
        import subprocess
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            state.available = True
            lines = result.stdout.strip().split("\n")
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if parts:
                    state.models_installed.append({
                        "name": parts[0],
                        "size": parts[2] if len(parts) > 2 else "unknown",
                    })
        # Check loaded model
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                state.model_loaded = lines[1].split()[0]
    except Exception:
        state.available = False
    return state


def _probe_wiki(project_root: str) -> WikiState:
    """Inspect wiki directory for current state."""
    state = WikiState()
    wiki_root = os.path.join(project_root, "wiki")
    state.wiki_root = wiki_root
    
    if not os.path.exists(wiki_root):
        return state
    
    import os
    total_size = 0
    for section in ["meta", "self", "philosophy", "plg", "archibal", "kaicart"]:
        section_dir = os.path.join(wiki_root, section)
        if os.path.exists(section_dir):
            pages = [f for f in os.listdir(section_dir) 
                     if f.endswith(".md") and f != "index.md"]
            state.pages_by_section[section] = len(pages)
            state.total_pages += len(pages)
            for f in pages:
                fpath = os.path.join(section_dir, f)
                total_size += os.path.getsize(fpath)
    
    state.disk_usage_mb = round(total_size / (1024 * 1024), 2)
    
    # Count raw sources
    raw_root = os.path.join(project_root, "raw")
    if os.path.exists(raw_root):
        for root, dirs, files in os.walk(raw_root):
            state.total_raw_sources += len([f for f in files if f.endswith(".md")])
    
    return state


def _probe_mem0(config: dict) -> Mem0State:
    """Probe Mem0 instances."""
    state = Mem0State()
    try:
        # Probe curated instance
        import psycopg2
        conn = psycopg2.connect(dbname="mem0_curated", host="localhost")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM memories")  # Mem0's table name
        state.curated_episode_count = cur.fetchone()[0]
        state.curated_available = True
        conn.close()
    except Exception:
        state.curated_available = False
    
    try:
        conn = psycopg2.connect(dbname="mem0_full", host="localhost")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM memories")
        state.full_record_count = cur.fetchone()[0]
        state.full_available = True
        conn.close()
    except Exception:
        state.full_available = False
    
    return state


def _build_cascade_profile(ollama: OllamaState, config: dict) -> CascadeProfile:
    """Build cascade profile from discovered Ollama + configured remote tiers."""
    profile = CascadeProfile()
    
    # Tier 1: Local Ollama
    if ollama.available and ollama.models_installed:
        profile.tiers.append({
            "name": "tier_1_local",
            "provider": "ollama",
            "model": ollama.models_installed[0]["name"] if ollama.models_installed else "unknown",
            "available": True,
            "cost_per_1k_tokens": 0.0,
            "latency_estimate_ms": 500,
        })
    
    # Tiers 2-4 from configuration (these are remote services, can't be fully probed)
    # Read from config but validate API keys exist
    for tier_config in config.get("CASCADE_TIERS", []):
        profile.tiers.append({
            "name": tier_config.get("name", "unknown"),
            "provider": tier_config.get("provider", "unknown"),
            "model": tier_config.get("model", "unknown"),
            "available": bool(tier_config.get("api_key")),
            "cost_per_1k_tokens": tier_config.get("cost", 0.0),
            "latency_estimate_ms": tier_config.get("latency", 1000),
        })
    
    return profile
```

---

## 3. The Technical Self-Model Generator

Transforms discovered data into wiki pages. Uses Tier 1 LLM only for the operating principles page (inferring how components work together).

```python
# src/subia/tsal/model_generator.py

"""
Technical Self-Model Generator — produces wiki/self/ pages from
discovered data.

Each page is a standard wiki page with frontmatter. Pages are
created via WikiWriteTool, so they participate in the wiki's
full governance (index updates, log entries, lint coverage).

Seven pages generated:
1. technical-architecture.md — codebase structure and patterns
2. host-environment.md — hardware and OS profile
3. component-inventory.md — available services and data stores
4. resource-state.md — current utilization (high-frequency)
5. operating-principles.md — inferred operating logic (LLM-assisted)
6. code-map.md — module dependency graph
7. cascade-profile.md — LLM tier availability and costs
"""

from datetime import datetime, timezone


def generate_technical_architecture(codebase: 'CodebaseProfile',
                                     wiki_tools: dict) -> str:
    """Generate wiki/self/technical-architecture.md from codebase analysis."""

    content = f"""---
title: "Technical Architecture — Discovered"
slug: technical-architecture
section: self
page_type: analysis
epistemic_status: factual
confidence: high
sources: []
created_by: tsal-code-analyst
created_at: "{datetime.now(timezone.utc).isoformat()}"
updated_by: tsal-code-analyst
updated_at: "{datetime.now(timezone.utc).isoformat()}"
update_count: 0
tags:
  - technical
  - self-knowledge
  - discovered
related_pages:
  - self/host-environment
  - self/component-inventory
  - self/code-map
status: active
ownership:
  owned_by: self
  valued_as: high
---

# Technical Architecture

## Overview
This page is auto-generated by TSAL code analysis. It describes the codebase
structure as discovered through AST parsing — not as declared.
Last analyzed: {codebase.analyzed_at}

## Codebase Summary
- Project root: {codebase.project_root}
- Total Python modules: {codebase.total_modules}
- Total lines of code: {codebase.total_lines:,}
- Total classes: {codebase.total_classes}
- Total registered tools: {codebase.total_tools}

## Discovered Architectural Patterns
"""
    for pattern in codebase.patterns_detected:
        content += f"- {pattern}\n"

    content += "\n## Registered Tools\n"
    for tool in codebase.tools:
        content += f"- **{tool.name}** ({tool.class_name}) — {tool.module_path}\n"

    content += "\n## Agent Configuration\n"
    for agent in codebase.agents:
        content += f"- **{agent.role}**: {agent.goal}\n"
        if agent.tools:
            content += f"  Tools: {', '.join(agent.tools)}\n"

    content += """
## Contradictions and Open Questions
None identified by static analysis. Runtime behavior may differ from code structure.

## Change History
- {timestamp}: Generated by TSAL code analyst. (tsal-code-analyst)
""".format(timestamp=codebase.analyzed_at)

    # Write via WikiWriteTool
    wiki_tools["write"]._run(
        path="self/technical-architecture.md",
        content=content,
        operation="create" if not _page_exists(wiki_tools, "self/technical-architecture.md") else "update",
        agent_role="tsal-code-analyst",
        log_notes="Auto-generated from codebase analysis"
    )

    return content


def generate_host_environment(host: 'HostProfile', wiki_tools: dict) -> str:
    """Generate wiki/self/host-environment.md from host probing."""

    content = f"""---
title: "Host Environment — Discovered"
slug: host-environment
section: self
page_type: entity
epistemic_status: factual
confidence: high
sources: []
created_by: tsal-host-prober
created_at: "{datetime.now(timezone.utc).isoformat()}"
updated_by: tsal-host-prober
updated_at: "{datetime.now(timezone.utc).isoformat()}"
update_count: 0
tags:
  - technical
  - hardware
  - discovered
related_pages:
  - self/resource-state
  - self/cascade-profile
status: active
ownership:
  owned_by: self
  valued_as: high
---

# Host Environment

## Overview
Discovered through system introspection. This page describes the physical
machine and OS that AndrusAI runs on.

## Hardware
- CPU: {host.cpu_model}
- Architecture: {host.cpu_architecture}
- Cores: {host.cpu_cores_physical} physical, {host.cpu_cores_logical} logical
- RAM: {host.ram_total_gb} GB {'(unified with GPU)' if host.gpu_unified_memory else ''}
- GPU: {host.gpu_model}
- GPU Memory: {host.gpu_memory_gb} GB
- Disk: {host.disk_total_gb} GB total, {host.disk_available_gb} GB available

## Operating System
- OS: {host.os_name} {host.os_version}
- Hostname: {host.hostname}
- Python: {host.python_version}

## Capabilities
- Local LLM inference: {'Yes' if host.can_run_local_llm else 'No'}
- Estimated max local model: ~{host.max_local_model_params_b}B parameters (quantized)
- GPU acceleration: {'Yes' if host.has_gpu_acceleration else 'No'}
- Metal support: {'Yes' if host.metal_support else 'No'}
- CUDA support: {'Yes' if host.cuda_support else 'No'}

## Planning Constraints
When planning model selection, code changes, or resource allocation:
- Local Ollama inference is limited to models under ~{host.max_local_model_params_b}B parameters
- RAM headroom must be maintained for Neo4j, PostgreSQL, and the CrewAI process
- {'Unified memory architecture means GPU and CPU compete for the same RAM pool' if host.gpu_unified_memory else 'Dedicated GPU memory is separate from system RAM'}

## Contradictions and Open Questions
None identified.

## Change History
- {datetime.now(timezone.utc).isoformat()}: Generated by TSAL host prober. (tsal-host-prober)
"""

    wiki_tools["write"]._run(
        path="self/host-environment.md",
        content=content,
        operation="create" if not _page_exists(wiki_tools, "self/host-environment.md") else "update",
        agent_role="tsal-host-prober",
        log_notes="Auto-generated from host introspection"
    )
    return content


def generate_resource_state(resources: 'ResourceState', host: 'HostProfile',
                             wiki_tools: dict) -> str:
    """
    Generate wiki/self/resource-state.md from current utilization.
    
    HIGH-FREQUENCY page: updated every 30 minutes or before major operations.
    Kept deliberately short to minimize wiki churn.
    """
    content = f"""---
title: "Resource State — Live"
slug: resource-state
section: self
page_type: log-entry
epistemic_status: factual
confidence: high
sources: []
created_by: tsal-resource-monitor
updated_by: tsal-resource-monitor
updated_at: "{resources.probed_at}"
update_count: 0
tags:
  - technical
  - resources
  - live
related_pages:
  - self/host-environment
status: active
---

# Resource State

## Current Utilization ({resources.probed_at})
- CPU: {resources.cpu_percent}%
- RAM: {resources.ram_used_gb}/{host.ram_total_gb} GB ({resources.ram_percent}%)
- Available for inference: {resources.available_for_inference_gb} GB
- Disk: {resources.disk_available_gb} GB free
- Compute pressure: {resources.compute_pressure:.2f} (0=idle, 1=saturated)
- Storage pressure: {resources.storage_pressure:.2f}

## Running Services
- Ollama: {'running' if resources.ollama_running else 'NOT running'}{f', loaded: {resources.ollama_model_loaded}' if resources.ollama_model_loaded else ''} ({resources.ollama_ram_gb} GB)
- Neo4j: {'running' if resources.neo4j_running else 'NOT running'}
- PostgreSQL: {'running' if resources.postgresql_running else 'NOT running'}
- CrewAI process: {resources.crewai_process_ram_gb} GB

## Planning Signal
{'⚠ High compute pressure — defer non-critical operations' if resources.compute_pressure > 0.7 else ''}{'⚠ Low disk space — consider cleanup' if resources.storage_pressure > 0.8 else ''}{'✓ Resources healthy' if resources.compute_pressure <= 0.7 and resources.storage_pressure <= 0.8 else ''}
"""

    wiki_tools["write"]._run(
        path="self/resource-state.md",
        content=content,
        operation="create" if not _page_exists(wiki_tools, "self/resource-state.md") else "update",
        agent_role="tsal-resource-monitor",
        log_notes="Auto-generated from resource monitoring"
    )
    return content


def _page_exists(wiki_tools, path):
    result = wiki_tools["read"]._run(path=path)
    return "Page not found" not in result
```

---

## 4. SubIA Integration

### 4.1 Self-State Enrichment

TSAL feeds SubIA's `self_state` with discovered technical capabilities:

```python
def enrich_self_state_from_tsal(self_state, host_profile, codebase_profile, 
                                 component_inventory):
    """
    Update SubIA self_state with TSAL-discovered technical knowledge.
    Called after each TSAL refresh cycle.
    """
    # Capabilities: discovered, not declared
    self_state.capabilities["local_inference"] = {
        "available": host_profile.can_run_local_llm,
        "max_model_params": host_profile.max_local_model_params_b,
        "current_model": component_inventory.ollama.model_loaded,
        "discovered": True,  # Flag: this was discovered, not declared
    }
    self_state.capabilities["knowledge_stores"] = {
        "chromadb_collections": len(component_inventory.chromadb.collections),
        "total_indexed_documents": component_inventory.chromadb.total_documents,
        "wiki_pages": component_inventory.wiki.total_pages,
        "mem0_curated_episodes": component_inventory.mem0.curated_episode_count,
        "mem0_full_records": component_inventory.mem0.full_record_count,
        "neo4j_nodes": component_inventory.neo4j.node_count,
        "neo4j_relations": component_inventory.neo4j.relation_count,
        "discovered": True,
    }
    self_state.capabilities["registered_tools"] = {
        "count": codebase_profile.total_tools,
        "names": [t.name for t in codebase_profile.tools],
        "discovered": True,
    }

    # Limitations: also discovered
    if not host_profile.can_run_local_llm:
        self_state.limitations["no_local_inference"] = {
            "description": "Host lacks sufficient RAM for local LLM inference",
            "constraint": f"RAM: {host_profile.ram_total_gb}GB, need 16GB minimum",
            "discovered": True,
        }

    if not component_inventory.neo4j.available:
        self_state.limitations["neo4j_unavailable"] = {
            "description": "Neo4j is not running — graph memory disabled",
            "discovered": True,
        }
```

### 4.2 Homeostasis Integration

Resource state feeds the `overload` homeostatic variable:

```python
def update_homeostasis_from_resources(homeostasis, resource_state):
    """
    Feed resource monitoring into homeostatic overload variable.
    Called after each resource probe.
    """
    # Compute overload from resource pressure
    # Weighted: compute pressure matters more than storage pressure
    overload = (
        resource_state.compute_pressure * 0.6 +
        resource_state.storage_pressure * 0.4
    )
    homeostasis.variables["overload"] = min(1.0, overload)

    # If Ollama isn't running, local inference is unavailable
    # This affects cascade tier selection indirectly via homeostasis
    if not resource_state.ollama_running:
        homeostasis.variables["overload"] = min(
            1.0, homeostasis.variables.get("overload", 0) + 0.2
        )
```

### 4.3 Predictor Integration

The predictor uses technical knowledge for resource-aware predictions:

```python
def enrich_prediction_with_technical_context(prediction_prompt, resource_state,
                                              cascade_profile, wiki_state):
    """
    Add technical context to prediction prompts so predictions
    account for current resource constraints.
    """
    technical_context = f"""
Technical constraints for this prediction:
- Available inference RAM: {resource_state.available_for_inference_gb}GB
- Compute pressure: {resource_state.compute_pressure:.2f}
- Wiki pages: {wiki_state.total_pages} (may affect ingest scope)
- Cascade tier availability: {[t['name'] for t in cascade_profile.tiers if t['available']]}
If resource pressure is high, predict smaller operation scope.
"""
    return prediction_prompt + technical_context
```

### 4.4 Commander Integration

Commander reads TSAL wiki pages before task planning:

```python
# In Commander's pre-task context (added to existing wiki index.md read)

COMMANDER_TSAL_PROMPT = """
Before planning tasks, also read:
- wiki/self/resource-state.md (current resource utilization)
- wiki/self/host-environment.md (hardware constraints)
- wiki/self/cascade-profile.md (available LLM tiers and costs)

Adjust task scope based on resource state:
- If compute_pressure > 0.7: defer non-critical operations
- If available_for_inference < 8GB: avoid loading large models
- If wiki pages > 200: consider ChromaDB search over grep
- If disk_available < 10GB: flag storage concern
"""
```

### 4.5 Self-Improver Integration for Evolution Planning

Self-Improver uses TSAL for code evolution proposals:

```python
SELF_IMPROVER_TSAL_PROMPT = """
When proposing code changes or system evolution:

1. Read wiki/self/technical-architecture.md to understand current codebase structure
2. Read wiki/self/host-environment.md to verify hardware can support the change
3. Read wiki/self/resource-state.md to check current resource headroom
4. Read wiki/self/component-inventory.md to verify dependencies are available
5. Read wiki/self/code-map.md to understand module dependencies that might be affected

CONSTRAINTS from technical self-knowledge:
- Max local model size: {max_model_params}B parameters
- Available RAM for new processes: {available_ram}GB
- If proposing a new ChromaDB collection: verify total indexed documents won't exceed reasonable search latency
- If proposing a new scheduled process: verify compute_pressure has headroom
- If proposing code changes to a module: list all modules that import from it (from code-map.md)

Self-improvement proposals that ignore technical constraints will fail at implementation.
"""
```

---

## 5. Refresh Schedule

| TSAL Component | Frequency | Trigger | Wiki Page Updated |
|---|---|---|---|
| Host Profile | Daily + startup | Timer, system boot | host-environment.md |
| Resource State | Every 30 minutes + before major operations | Timer, CIL pre-task for full loops | resource-state.md |
| Code Analysis | Daily + on file change detection | Timer, git commit hook | technical-architecture.md, code-map.md |
| Component Discovery | Every 2 hours + startup | Timer | component-inventory.md, cascade-profile.md |
| Operating Principles | Weekly + on significant code change | Timer, after codebase analysis detects new patterns | operating-principles.md |

---

## 6. Operating Principles Inference

This is the ONE page that requires an LLM call. All others are generated from deterministic probing. The operating principles page infers HOW the system works from the discovered WHAT.

```python
def generate_operating_principles(codebase, components, wiki_tools, cascade_tier="tier_1"):
    """
    Infer operating principles from discovered architecture.
    
    Uses Tier 1 LLM to synthesize code structure + component state
    into a description of how the system actually operates.
    
    This is generated, not declared. If the code changes, the
    principles page changes on the next refresh.
    
    ~500 tokens LLM call. Weekly refresh.
    """
    # Build context from discovered data
    context = f"""
Codebase: {codebase.total_modules} modules, {codebase.total_lines} lines
Detected patterns: {codebase.patterns_detected}
Agents: {[a.role for a in codebase.agents]}
Tools: {[t.name for t in codebase.tools]}
ChromaDB collections: {[c['name'] for c in components.chromadb.collections]}
Neo4j relation types: {[r['type'] for r in components.neo4j.relation_types]}
Wiki sections: {list(components.wiki.pages_by_section.keys())}
Cascade tiers: {[t['name'] for t in components.cascade.tiers]}
SubIA active: {components.subia_active}
"""

    prompt = f"""Based on this discovered system architecture, describe in 300 words
how the system operates. Focus on:
1. The flow of information (inputs → processing → outputs)
2. How agents coordinate (what orchestrates them)
3. How knowledge compounds (what persists between operations)
4. What safety constraints exist
5. What self-awareness mechanisms are active

Discovered architecture:
{context}

Write as factual description of operating logic, not aspirational."""

    # Call LLM
    # response = cascade_router.call(prompt, tier=cascade_tier)
    # principles_text = response

    # Generate wiki page with LLM response as the body
    # ...
```

---

## 7. Complete Wiki Page Set

TSAL generates seven wiki/self/ pages:

| Page | Source | LLM? | Frequency |
|---|---|---|---|
| `technical-architecture.md` | Code Analyst | No | Daily |
| `host-environment.md` | Host Prober | No | Daily |
| `component-inventory.md` | Component Discovery | No | 2-hourly |
| `resource-state.md` | Host Prober (resources) | No | 30-min |
| `operating-principles.md` | All three + Tier 1 LLM | Yes (~500 tok) | Weekly |
| `code-map.md` | Code Analyst (dependencies) | No | Daily |
| `cascade-profile.md` | Component Discovery + Ollama | No | 2-hourly |

**Total LLM cost: ~500 tokens per week.** Everything else is pure Python system calls and file parsing.

---

## 8. Self-Improvement Planning with Technical Awareness

When Self-Improver proposes a code change or evolution step, the technical self-model provides hard constraints:

```python
@dataclass
class EvolutionProposal:
    """A proposed code or architecture change."""
    description: str
    modules_affected: list        # From code-map.md
    estimated_ram_impact_gb: float
    estimated_disk_impact_gb: float
    requires_new_dependency: bool
    requires_model_change: bool
    estimated_implementation_tokens: int
    
    # TSAL-provided feasibility check
    host_can_support: bool = False
    resource_headroom_sufficient: bool = False
    affected_modules_stable: bool = False  # No recent changes to affected modules
    cascade_tier_available: bool = False   # Required tier for implementation


def check_evolution_feasibility(proposal: EvolutionProposal,
                                 host: 'HostProfile',
                                 resources: 'ResourceState',
                                 codebase: 'CodebaseProfile') -> dict:
    """
    Validate a Self-Improver evolution proposal against
    technical self-knowledge.
    """
    checks = {}
    
    # RAM check
    available = resources.available_for_inference_gb
    checks["ram"] = {
        "pass": proposal.estimated_ram_impact_gb < available * 0.5,
        "detail": f"Need {proposal.estimated_ram_impact_gb}GB, have {available}GB available"
    }
    
    # Disk check
    checks["disk"] = {
        "pass": proposal.estimated_disk_impact_gb < resources.disk_available_gb * 0.1,
        "detail": f"Need {proposal.estimated_disk_impact_gb}GB, have {resources.disk_available_gb}GB free"
    }
    
    # Module stability check
    affected = set(proposal.modules_affected)
    recent_changes = set()  # From git log analysis
    checks["stability"] = {
        "pass": not (affected & recent_changes),
        "detail": f"Affected modules with recent changes: {affected & recent_changes}"
    }
    
    # Dependency impact check
    downstream = set()
    for module_name in proposal.modules_affected:
        for dep_name, imports in codebase.dependencies.items():
            if module_name in imports:
                downstream.add(dep_name)
    checks["downstream_impact"] = {
        "pass": len(downstream) < 10,
        "detail": f"{len(downstream)} downstream modules would be affected: {list(downstream)[:5]}"
    }
    
    proposal.host_can_support = checks["ram"]["pass"] and checks["disk"]["pass"]
    proposal.resource_headroom_sufficient = resources.compute_pressure < 0.6
    proposal.affected_modules_stable = checks["stability"]["pass"]
    
    return checks
```

---

## 9. Integration with the Six Proposals

| Proposal | How TSAL Feeds It |
|---|---|
| Reverie Engine | Resource-state.md tells Reverie when it's safe to run (compute_pressure < 0.5). Cascade-profile.md tells it which tier to use. |
| Understanding Layer | Component-inventory.md tells it which ChromaDB collections are available for raw source lookup. Host-environment.md determines whether Tier 2 LLM calls are feasible. |
| Shadow Self | Code-map.md provides the structural ground truth that behavioral analysis compares against. Technical-architecture.md is the "declared" architecture that Shadow can check against actual resource usage patterns. |
| Wonder Register | No direct feed. Wonder operates on knowledge depth, not technical state. |
| Boundary Sense | TSAL-discovered data is tagged `processing_mode: INTROSPECTIVE` — it's self-knowledge about the system's own physical substrate. The deepest kind of introspection. |
| Value Resonance | No direct feed. Values operate on semantic content, not technical state. |

---

*This specification should be read as an addition to the SubIA Unified Implementation Specification. TSAL is implemented during SubIA Phase 1 (The Subject) because self_state.capabilities and self_state.limitations must be populated from discovery, not declaration, from the very start.*
