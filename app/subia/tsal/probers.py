"""HostProber + ResourceMonitor — pure-Python physical-substrate discovery.

Zero LLM. All data comes from psutil, platform, subprocess, and file
reads. The module is import-safe even if psutil is missing (returns
empty profiles with `available=False` flags).
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Profiles ─────────────────────────────────────────────────────────

@dataclass
class HostProfile:
    """Discovered host machine profile (slow-changing, daily refresh)."""
    cpu_model: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_architecture: str = ""
    ram_total_gb: float = 0.0
    gpu_model: str = ""
    gpu_memory_gb: float = 0.0
    gpu_unified_memory: bool = False
    disk_total_gb: float = 0.0
    disk_available_gb: float = 0.0
    os_name: str = ""
    os_version: str = ""
    hostname: str = ""
    python_version: str = ""
    can_run_local_llm: bool = False
    max_local_model_params_b: float = 0.0
    has_gpu_acceleration: bool = False
    metal_support: bool = False
    cuda_support: bool = False
    probed_at: str = ""


@dataclass
class ResourceState:
    """Current resource utilisation (high-frequency refresh)."""
    cpu_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_available_gb: float = 0.0
    ram_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_available_gb: float = 0.0
    gpu_utilization_percent: float = 0.0
    gpu_memory_used_gb: float = 0.0
    ollama_running: bool = False
    ollama_model_loaded: str = ""
    ollama_ram_gb: float = 0.0
    neo4j_running: bool = False
    postgresql_running: bool = False
    crewai_process_ram_gb: float = 0.0
    available_for_inference_gb: float = 0.0
    storage_pressure: float = 0.0
    compute_pressure: float = 0.0
    probed_at: str = ""


# ── Capability inference (deterministic; Tier-3 weights) ────────────

def _infer_max_local_model_params_b(host: HostProfile) -> float:
    """Rough heuristic: ~0.5 GB per B parameters at q4 quantization."""
    if host.gpu_unified_memory and host.ram_total_gb:
        usable = host.ram_total_gb * 0.75
        return round(usable / 0.5, 1)
    if host.has_gpu_acceleration and host.gpu_memory_gb:
        return round(host.gpu_memory_gb / 0.5, 1)
    if host.ram_total_gb:
        return round(host.ram_total_gb * 0.4 / 0.5, 1)
    return 0.0


def _detect_apple_silicon_gpu(host: HostProfile) -> None:
    """Populate GPU fields on Darwin via system_profiler."""
    if platform.system() != "Darwin":
        return
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout or "{}")
        displays = data.get("SPDisplaysDataType", [])
        if displays:
            host.gpu_model = displays[0].get("sppci_model", "")
            if "Apple" in host.gpu_model:
                host.gpu_unified_memory = True
                host.metal_support = True
                host.has_gpu_acceleration = True
    except Exception:
        pass


def _detect_cuda(host: HostProfile) -> None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                host.gpu_model = parts[0]
                try:
                    host.gpu_memory_gb = round(float(parts[1]) / 1024, 1)
                except ValueError:
                    pass
            host.cuda_support = True
            host.has_gpu_acceleration = True
    except Exception:
        pass


# ── HostProber ───────────────────────────────────────────────────────

class HostProber:
    """Stateless wrapper. Inject a `psutil_module` to override for tests."""

    def __init__(self, psutil_module=None) -> None:
        if psutil_module is None:
            try:
                import psutil  # type: ignore
                psutil_module = psutil
            except ImportError:
                psutil_module = None
        self._ps = psutil_module

    def probe(self) -> HostProfile:
        host = HostProfile()
        host.probed_at = datetime.now(timezone.utc).isoformat()
        host.cpu_architecture = platform.machine()
        host.os_name = platform.system()
        host.os_version = platform.release()
        host.hostname = platform.node()
        host.python_version = platform.python_version()

        if self._ps:
            host.cpu_cores_physical = self._ps.cpu_count(logical=False) or 0
            host.cpu_cores_logical = self._ps.cpu_count(logical=True) or 0
            mem = self._ps.virtual_memory()
            host.ram_total_gb = round(mem.total / (1024 ** 3), 1)
            disk = self._ps.disk_usage("/")
            host.disk_total_gb = round(disk.total / (1024 ** 3), 1)
            host.disk_available_gb = round(disk.free / (1024 ** 3), 1)

        # CPU model (platform-specific)
        if platform.system() == "Darwin":
            try:
                r = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=5,
                )
                host.cpu_model = r.stdout.strip()
            except Exception:
                host.cpu_model = platform.processor()
        else:
            host.cpu_model = platform.processor()

        _detect_apple_silicon_gpu(host)
        if not host.has_gpu_acceleration:
            _detect_cuda(host)

        if host.gpu_unified_memory:
            host.gpu_memory_gb = host.ram_total_gb

        host.can_run_local_llm = host.ram_total_gb >= 16
        host.max_local_model_params_b = _infer_max_local_model_params_b(host)
        return host


# ── ResourceMonitor ──────────────────────────────────────────────────

class ResourceMonitor:
    """Stateless. Inject `psutil_module` and `ollama_detector` for tests."""

    def __init__(
        self,
        psutil_module=None,
        ollama_detector=None,
    ) -> None:
        if psutil_module is None:
            try:
                import psutil  # type: ignore
                psutil_module = psutil
            except ImportError:
                psutil_module = None
        self._ps = psutil_module
        self._ollama_detect = ollama_detector or self._default_ollama_detect

    def probe(self) -> ResourceState:
        s = ResourceState()
        s.probed_at = datetime.now(timezone.utc).isoformat()
        if not self._ps:
            return s

        s.cpu_percent = self._ps.cpu_percent(interval=0.1)
        mem = self._ps.virtual_memory()
        s.ram_used_gb = round(mem.used / (1024 ** 3), 2)
        s.ram_available_gb = round(mem.available / (1024 ** 3), 2)
        s.ram_percent = mem.percent
        disk = self._ps.disk_usage("/")
        s.disk_used_gb = round(disk.used / (1024 ** 3), 2)
        s.disk_available_gb = round(disk.free / (1024 ** 3), 2)

        # Process discovery
        try:
            for proc in self._ps.process_iter(["pid", "name", "memory_info", "cmdline"]):
                try:
                    info = proc.info
                    name = (info.get("name") or "").lower()
                    rss = (info.get("memory_info").rss
                           if info.get("memory_info") else 0)
                    mem_gb = round(rss / (1024 ** 3), 2)
                    cmdline = " ".join(info.get("cmdline") or [])
                    if "ollama" in name:
                        s.ollama_running = True
                        s.ollama_ram_gb = max(s.ollama_ram_gb, mem_gb)
                        if "serve" in cmdline or "runner" in cmdline:
                            s.ollama_model_loaded = self._ollama_detect()
                    elif "neo4j" in name:
                        if mem_gb > 0.5:
                            s.neo4j_running = True
                    elif "postgres" in name:
                        s.postgresql_running = True
                    elif "python" in name and "crewai" in cmdline:
                        s.crewai_process_ram_gb = max(s.crewai_process_ram_gb, mem_gb)
                except Exception:
                    continue
        except Exception:
            pass

        # Derived
        s.available_for_inference_gb = round(max(0.0, s.ram_available_gb - 4.0), 2)
        total = max(1.0, s.disk_used_gb + s.disk_available_gb)
        s.storage_pressure = round(min(1.0, s.disk_used_gb / total), 4)
        s.compute_pressure = round(
            min(1.0, s.cpu_percent / 100.0 * 0.5 + s.ram_percent / 100.0 * 0.5),
            4,
        )
        return s

    @staticmethod
    def _default_ollama_detect() -> str:
        try:
            r = subprocess.run(
                ["ollama", "ps"], capture_output=True, text=True, timeout=5,
            )
            lines = (r.stdout or "").strip().splitlines()
            if len(lines) > 1:
                return lines[1].split()[0]
        except Exception:
            pass
        return ""


def derive_pressures(
    cpu_percent: float,
    ram_percent: float,
    disk_used_gb: float,
    disk_available_gb: float,
) -> tuple[float, float]:
    """Pure helper for tests + reuse."""
    total = max(1.0, disk_used_gb + disk_available_gb)
    storage = round(min(1.0, disk_used_gb / total), 4)
    compute = round(min(1.0, cpu_percent / 100.0 * 0.5 + ram_percent / 100.0 * 0.5), 4)
    return compute, storage
