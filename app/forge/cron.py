"""Forge scheduled jobs — registered into APScheduler at app startup.

Three jobs:
  - weekly periodic re-audit (Sundays 04:30)
  - hourly anomaly check
  - hourly hash-chain integrity verification (logs WARN on break)

All cron expressions are configurable via env. Defaults are conservative —
periodic re-audit is the heaviest job (LLM-free, but iterates every active
tool), so weekly is appropriate for a < 50-tool registry.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


def _safe_periodic_reaudit() -> None:
    try:
        from app.forge.audit.periodic import run_periodic_reaudit
        run_periodic_reaudit()
    except Exception:
        logger.exception("forge.cron: periodic re-audit failed")


def _safe_anomaly_check() -> None:
    try:
        from app.forge.anomaly import run_anomaly_check
        run_anomaly_check()
    except Exception:
        logger.exception("forge.cron: anomaly check failed")


def _safe_integrity_check() -> None:
    try:
        from app.forge.integrity import verify_audit_chain
        result = verify_audit_chain()
        if not result["ok"]:
            logger.warning(
                "forge.cron: AUDIT-LOG INTEGRITY BREAK detected — "
                "rows_checked=%d breaks=%d first=%s",
                result["rows_checked"], len(result["breaks"]),
                result["breaks"][0] if result["breaks"] else None,
            )
    except Exception:
        logger.exception("forge.cron: integrity check failed")


def register_periodic_jobs(scheduler: "AsyncIOScheduler") -> None:
    """Add the three forge maintenance jobs to the scheduler.

    Idempotent against re-runs (replaces existing job ids).
    """
    from apscheduler.triggers.cron import CronTrigger

    periodic_cron = _env("TOOL_FORGE_PERIODIC_CRON", "30 4 * * 0")  # Sunday 04:30
    anomaly_cron = _env("TOOL_FORGE_ANOMALY_CRON", "*/30 * * * *")   # every 30 min
    integrity_cron = _env("TOOL_FORGE_INTEGRITY_CRON", "0 * * * *")  # hourly

    try:
        scheduler.add_job(
            _safe_periodic_reaudit,
            CronTrigger.from_crontab(periodic_cron),
            id="forge.periodic_reaudit",
            replace_existing=True,
        )
        scheduler.add_job(
            _safe_anomaly_check,
            CronTrigger.from_crontab(anomaly_cron),
            id="forge.anomaly_check",
            replace_existing=True,
        )
        scheduler.add_job(
            _safe_integrity_check,
            CronTrigger.from_crontab(integrity_cron),
            id="forge.integrity_check",
            replace_existing=True,
        )
        logger.info(
            "forge.cron: registered jobs — periodic=%s anomaly=%s integrity=%s",
            periodic_cron, anomaly_cron, integrity_cron,
        )
    except Exception:
        logger.exception("forge.cron: failed to register periodic jobs")
