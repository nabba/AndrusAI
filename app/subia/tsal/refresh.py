"""TSAL refresh — registers TSAL discovery jobs with the SubIA IdleScheduler.

One entry point: `register_tsal_jobs(scheduler, ...)`. Honours the
TSAL spec §5 cadence (host daily, resources every 30 min, code daily,
components every 2h, principles weekly).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from app.subia.idle import IdleJob, IdleScheduler

from .probers import HostProber, ResourceMonitor
from .inspectors import CodeAnalyst, ComponentDiscovery
from .self_model import TechnicalSelfModel
from .generators import PageGenerator
from .operating_principles import infer_operating_principles

logger = logging.getLogger(__name__)


# Cadences (seconds) — TSAL §5
_HOST_INTERVAL        = 24 * 60 * 60          # daily
_RESOURCE_INTERVAL    = 30 * 60               # 30 min
_CODE_INTERVAL        = 24 * 60 * 60          # daily
_COMPONENT_INTERVAL   = 2 * 60 * 60           # 2 hours
_PRINCIPLES_INTERVAL  = 7 * 24 * 60 * 60      # weekly


# Singleton model — last-known TSAL state. Never None after first run.
_LAST_MODEL: TechnicalSelfModel = TechnicalSelfModel.assemble()


def get_last_model() -> TechnicalSelfModel:
    return _LAST_MODEL


def register_tsal_jobs(
    scheduler: IdleScheduler,
    *,
    host_prober: Optional[HostProber] = None,
    resource_monitor: Optional[ResourceMonitor] = None,
    code_analyst: Optional[CodeAnalyst] = None,
    component_discovery: Optional[ComponentDiscovery] = None,
    page_generator: Optional[PageGenerator] = None,
    principles_predict_fn: Optional[Callable[[str], str]] = None,
    on_resources_updated: Optional[Callable[[object], None]] = None,
    on_model_updated: Optional[Callable[[TechnicalSelfModel], None]] = None,
) -> list:
    """Register the five TSAL jobs. Returns the list of registered names.

    All probers/analysts/generators are injectable so the scheduler is
    unit-testable. Production wires defaults via the TSAL package.
    """
    host_prober = host_prober or HostProber()
    resource_monitor = resource_monitor or ResourceMonitor()
    code_analyst = code_analyst or CodeAnalyst()
    component_discovery = component_discovery or ComponentDiscovery()
    page_generator = page_generator or PageGenerator()

    def _resources_job() -> dict:
        rs = resource_monitor.probe()
        _LAST_MODEL.resources = rs
        if on_resources_updated:
            try:
                on_resources_updated(rs)
            except Exception:
                logger.debug("tsal: on_resources_updated failed", exc_info=True)
        # Refresh the live page only
        page_generator.generate_resource_state(rs, _LAST_MODEL.host)
        return {"tokens_spent": 0, "compute_pressure": rs.compute_pressure}

    def _host_job() -> dict:
        hp = host_prober.probe()
        _LAST_MODEL.host = hp
        page_generator.generate_host_environment(hp)
        if on_model_updated:
            try:
                on_model_updated(_LAST_MODEL)
            except Exception:
                pass
        return {"tokens_spent": 0, "ram_total_gb": hp.ram_total_gb}

    def _code_job() -> dict:
        cb = code_analyst.analyze()
        _LAST_MODEL.codebase = cb
        page_generator.generate_technical_architecture(cb)
        page_generator.generate_code_map(cb)
        return {"tokens_spent": 0, "modules": cb.total_modules}

    def _components_job() -> dict:
        ci = component_discovery.discover()
        _LAST_MODEL.components = ci
        page_generator.generate_component_inventory(ci)
        page_generator.generate_cascade_profile(ci.cascade)
        return {"tokens_spent": 0, "chromadb": ci.chromadb.available}

    def _principles_job() -> dict:
        principles = infer_operating_principles(
            _LAST_MODEL, predict_fn=principles_predict_fn,
        )
        _LAST_MODEL.operating_principles = principles
        page_generator.generate_operating_principles(principles)
        return {"tokens_spent": 500 if principles else 0,
                "inferred": bool(principles)}

    jobs = [
        IdleJob(name="tsal_resources",  fn=_resources_job,
                min_interval_seconds=_RESOURCE_INTERVAL,  priority=10,
                token_budget=0),
        IdleJob(name="tsal_host",       fn=_host_job,
                min_interval_seconds=_HOST_INTERVAL,      priority=40,
                token_budget=0),
        IdleJob(name="tsal_code",       fn=_code_job,
                min_interval_seconds=_CODE_INTERVAL,      priority=50,
                token_budget=0),
        IdleJob(name="tsal_components", fn=_components_job,
                min_interval_seconds=_COMPONENT_INTERVAL, priority=30,
                token_budget=0),
        IdleJob(name="tsal_principles", fn=_principles_job,
                min_interval_seconds=_PRINCIPLES_INTERVAL, priority=80,
                token_budget=500),
    ]
    for j in jobs:
        scheduler.register(j)
    return [j.name for j in jobs]
