"""
app.healing — unified self-healing surface.

Three reactive surfaces, one package:

    error_diagnosis     per-exception remediation (was: app.self_heal)
    health_remediator   aggregate-health remediation (was: app.self_healer, IMMUTABLE)
    runbooks            anomaly-pattern remediation (operator-authored, IMMUTABLE)

Plus, since 2026-05-09:

    handlers            operational runbook handlers (eager-registered on import)
    monitors            proactive observability monitors (daemon thread)
    auditor_bridge      audit_journal → Signal (turns silent diagnoses into action)

Distinct from `app.self_improvement`, which is the proactive learning loop
(Gap Detector → Novelty → Learner → Integrator → Evaluator → Consolidator).
"""
import logging as _logging

from app.healing.error_diagnosis import (
    diagnose_and_fix,
    log_error,
    get_recent_errors,
    get_error_patterns,
)
from app.healing.health_remediator import SelfHealer
from app.healing.runbooks import (
    RunbookResult,
    maybe_run_runbook,
    register_runbook,
    runbooks_enabled,
    unregister_runbook,
)

# ── Eager wiring (2026-05-09) ─────────────────────────────────────────────
# Importing the submodules below triggers registration / daemon-start side
# effects. Each is wrapped so a single broken module never prevents the
# others from loading. The runbook framework already gates dispatch behind
# ``ERROR_RUNBOOKS_ENABLED``; monitors gate behind ``HEALING_MONITORS_ENABLED``;
# the auditor bridge gates behind ``HEALING_AUDITOR_BRIDGE_ENABLED``.

_log = _logging.getLogger(__name__)

# ── Serving/compute split — worker skips gateway daemon wiring (2026-06-07) ──
# Importing app.healing (directly via main.py, OR transitively via any
# `from app.healing.X import Y` — there are dozens) runs the eager-wiring
# below. In the idle-compute WORKER process (IDLE_SCHEDULER_ROLE=worker) that
# started ~12 DUPLICATE gateway daemons — double proposal_bridge promotion,
# double auto_revert, double governance + Signal alerts, a 2nd
# source_ledger_daemon, the upgrade-lifecycle trial/apply daemons, the vacation
# sweep, boot_diagnostics probing a gateway it isn't, etc. The worker must run
# ONLY the idle loop's worker-eligible jobs. Gate the wiring on role so the
# GATEWAY (role gateway/all) wires exactly as before and the worker skips. This
# is the worker.py-documented "Phase 2" cleanup + the prerequisite for moving
# more heavy jobs to the worker.
import os as _os

_IS_WORKER = _os.environ.get("IDLE_SCHEDULER_ROLE", "").strip().lower() == "worker"


def _wire_eager_subsystems() -> None:
    """Eager-wire the gateway-side daemons + boot-time side effects. Called
    only on the gateway/all role (see ``_IS_WORKER``)."""
    try:
        from app.healing import handlers as _handlers  # noqa: F401  — side-effect import
    except Exception:
        _log.warning("app.healing: handlers wiring failed", exc_info=True)

    try:
        from app.healing import monitors as _monitors  # noqa: F401  — daemon start
    except Exception:
        _log.warning("app.healing: monitors wiring failed", exc_info=True)

    try:
        from app.healing import auditor_bridge as _auditor_bridge  # noqa: F401
    except Exception:
        _log.warning("app.healing: auditor_bridge wiring failed", exc_info=True)

    # Watchdog (Wave 2 #7, 2026-05-09) — re-spawns the daemons above when
    # they die. Started LAST so the daemons it watches are already running
    # at first reaper pass.
    try:
        from app.healing import watchdog as _watchdog  # noqa: F401
    except Exception:
        _log.warning("app.healing: watchdog wiring failed", exc_info=True)

    # ── Eager-start anchor for non-healing observational subsystems ──────────
    # Each of these packages eager-starts a daemon thread at module-import
    # time, but nothing in the boot chain (main.py:96 → app.healing) was
    # previously importing them — so their daemons never ran in production.
    # Anchoring here is the lightest-weight fix: ``app.healing`` is already
    # the canonical eager-wiring hub, already imported at boot, already has
    # the defensive try/except shell. Naming the section explicitly so
    # future contributors see this is the launch point for *any*
    # eager-start subsystem, not just healing-themed ones.

    try:
        from app.self_improvement import capability_gap_analyzer as _capability_gap_analyzer  # noqa: F401
    except Exception:
        _log.warning("app.healing: capability_gap_analyzer wiring failed", exc_info=True)

    try:
        from app import library_radar as _library_radar  # noqa: F401
    except Exception:
        _log.warning("app.healing: library_radar wiring failed", exc_info=True)

    try:
        from app import proposal_bridge as _proposal_bridge  # noqa: F401
    except Exception:
        _log.warning("app.healing: proposal_bridge wiring failed", exc_info=True)

    # PROGRAM §48 (Q13.2) — weekly inbound-dependency health scan. Same
    # anchor pattern as library_radar / proposal_bridge: import at boot
    # so the daemon's eager-start trigger fires exactly once on a known
    # entry point.
    try:
        from app import dependency_radar as _dependency_radar  # noqa: F401
    except Exception:
        _log.warning("app.healing: dependency_radar wiring failed", exc_info=True)

    try:
        from app.change_requests import auto_revert as _auto_revert  # noqa: F401
    except Exception:
        _log.warning("app.healing: auto_revert wiring failed", exc_info=True)

    try:
        from app import governance_notifier as _governance_notifier  # noqa: F401
    except Exception:
        _log.warning("app.healing: governance_notifier wiring failed", exc_info=True)

    # PROGRAM §45.3 (Q7.3) — recipe-consolidation daemon. Eager-start at
    # import time scans the meta-agent recipe ledger weekly and proposes
    # retirement of recipes that fail EITHER the health-score (4-term
    # composite < 0.30) OR the selection-rate (<5% over 90d) trigger. Both
    # triggers compose; both are advisory (never auto-retire).
    try:
        from app.self_improvement.meta_agent import consolidation as _recipe_consolidation  # noqa: F401
    except Exception:
        _log.warning("app.healing: recipe_consolidation wiring failed", exc_info=True)

    # PROGRAM §56 (2026-05-17) — source-ledger daemon. Eager-start at
    # import time so the bootstrap / drift / chain-verify branches kick
    # in 5 minutes after gateway boot. Same anchor pattern as the other
    # observational daemons above.
    try:
        from app.memory import source_ledger_daemon as _source_ledger_daemon  # noqa: F401
    except Exception:
        _log.warning("app.healing: source_ledger_daemon wiring failed", exc_info=True)

    # Boot-time stale-cooldown reset (Wave 0/1 #A7, 2026-05-09). Sweeps
    # already-expired ``skip:<jobname>`` keys from
    # workspace/memory/idle_job_state so a fresh boot doesn't carry
    # expired cooldowns from the previous process. Idempotent.
    try:
        from app.healing.boot_reset import reset_stale_cooldowns as _reset_cooldowns
        _reset_cooldowns()
    except Exception:
        _log.warning("app.healing: boot cooldown reset failed", exc_info=True)

    # PROGRAM §51 Q16 Theme 3 — vacation-mode sweep daemon. Anchored
    # here for the same reason as proposal_bridge / library_radar /
    # governance_notifier: eager-start at module-import time of a known
    # boot-chain entry point. The daemon itself is bounded by
    # ``vacation_mode.state.is_active()`` so the thread is harmless when
    # vacation mode isn't engaged.
    try:
        from app.vacation_mode.sweep import start_daemon as _vacation_start
        _vacation_start()
    except Exception:
        _log.warning("app.healing: vacation_mode wiring failed", exc_info=True)

    # PROGRAM §52 Q17 — multi-year resilience anchors.

    try:
        from app.creativity import synthesis_pass as _synthesis_pass  # noqa: F401
    except Exception:
        _log.warning("app.healing: synthesis_pass wiring failed", exc_info=True)

    # PROGRAM §63 (2026-05-23) — upgrade-lifecycle trial scheduler daemon.
    # Eager-start at module-import time consumes the orchestrator's pending
    # queue + runs at most one trial per hour. Bounded by the trial_enabled
    # runtime switch so disabling the trial subsystem stops the daemon too.
    try:
        from app.upgrade_lifecycle.trial_scheduler import start as _ul_scheduler_start
        _ul_scheduler_start()
    except Exception:
        _log.warning("app.healing: trial_scheduler wiring failed", exc_info=True)

    # PROGRAM §63 follow-up — apply-hook daemon. Polls change_requests
    # audit log every 10 min for newly-applied upgrade decision CRs at
    # docs/proposed_upgrades/, parses YAML front-matter, dispatches to
    # requirements_writer. Gated by ``upgrade_lifecycle_apply_hook_enabled``
    # (default OFF — operator opts in once they trust the loop end-to-end).
    try:
        from app.upgrade_lifecycle.apply_hook import start as _ul_apply_hook_start
        _ul_apply_hook_start()
    except Exception:
        _log.warning("app.healing: apply_hook wiring failed", exc_info=True)

    # P1#b — register upgrade-lifecycle daemons with the watchdog so a
    # silent thread death is detected + re-spawned within 60 s. Both
    # start functions are idempotent (they check is_running first), so
    # the watchdog calling them when the thread already exists is a
    # no-op.
    try:
        from app.healing.watchdog import register_daemon
        register_daemon(
            "ul-trial-scheduler",
            module="app.upgrade_lifecycle.trial_scheduler",
            start_fn="start",
        )
        register_daemon(
            "ul-apply-hook",
            module="app.upgrade_lifecycle.apply_hook",
            start_fn="start",
        )
    except Exception:
        _log.warning(
            "app.healing: upgrade_lifecycle watchdog registration failed",
            exc_info=True,
        )

    try:
        from app.resilience_drills.drills import local_only as _local_only_drill  # noqa: F401
    except Exception:
        _log.warning("app.healing: local_only drill wiring failed", exc_info=True)

    # 2026-05-23 — event-loop responsiveness probe. One-line ``async def``
    # at /health gives a clean asyncio-loop latency signal; this daemon
    # probes it on a 10 s cadence and records slow/failed observations to
    # workspace/observability/event_loop_latency.jsonl for post-hang
    # forensics. Composes with — does not replace — the host watchdog
    # (which restarts on the same stall) and signal_heartbeat. Anchored
    # here for the same reason as the other observational daemons:
    # app.healing is the canonical eager-wiring hub already imported at
    # boot via main.py.
    try:
        from app.observability import boot_diagnostics as _boot_diagnostics  # noqa: F401
    except Exception:
        _log.warning("app.healing: boot_diagnostics wiring failed", exc_info=True)


if _IS_WORKER:
    _log.info(
        "app.healing: worker mode (IDLE_SCHEDULER_ROLE=worker) — skipping "
        "gateway daemon eager-wiring; idle loop only"
    )
else:
    _wire_eager_subsystems()

__all__ = [
    "diagnose_and_fix",
    "log_error",
    "get_recent_errors",
    "get_error_patterns",
    "SelfHealer",
    "RunbookResult",
    "maybe_run_runbook",
    "register_runbook",
    "runbooks_enabled",
    "unregister_runbook",
]
