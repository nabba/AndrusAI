"""
publishers.py — Registry of cadence-aware Firebase publishers.

Motivation
----------
The 60s scheduler tick in ``app.main`` used to contain an inline block
that called eight different ``report_*`` functions in sequence under a
single shared ``try/except``:

    if _hb_counter[0] % 5 == 0:
        try:
            report_anomalies(); report_variants(); report_tech_radar();
            report_deploys(); report_proposals(); report_proposal_actions();
            report_philosophy_kb(); report_evolution_stats()
            report_subia_state()
        except Exception:
            pass

Three problems:

1. **One failure drops the rest** — an exception in ``report_tech_radar``
   skips reports 4–9 for the next 5 min.
2. **No per-publisher diagnostics** — the blanket ``except: pass`` hides
   which publisher failed.
3. **Edits require touching main.py** — every new publisher is another
   hand-written call + import.

The registry replaces this with a small datastructure + a single
``run_all(tick)`` entry point.  Each publisher runs under its own
``try/except`` with a named log line, and the set of publishers is
data, not a hand-unrolled loop.

Usage
-----
At startup (see ``install_defaults``)::

    from app.firebase import publishers
    publishers.register("anomalies",       report_anomalies,      every_ticks=5)
    publishers.register("tech_radar",      report_tech_radar,     every_ticks=5)
    ...

In the heartbeat job::

    publishers.run_all(tick=_hb_counter[0])

Adding a new publisher is a single ``register(...)`` call at startup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class _Publisher:
    name: str
    fn: Callable[[], None]
    every_ticks: int


# Registered in insertion order; run_all walks the list every tick.
# The list is small (tens of entries) so there's no need for a dict
# keyed by name — name is only used for logging.
_registry: list[_Publisher] = []


def register(
    name: str,
    fn: Callable[[], None],
    *,
    every_ticks: int = 1,
) -> None:
    """Add a publisher.  ``every_ticks=1`` fires every heartbeat
    (60s); ``every_ticks=5`` fires every 5 heartbeats (5 min).

    Idempotent: registering the same ``name`` twice replaces the
    earlier entry so a hot-reloaded module doesn't stack duplicates.
    """
    for i, existing in enumerate(_registry):
        if existing.name == name:
            _registry[i] = _Publisher(name=name, fn=fn, every_ticks=every_ticks)
            logger.debug("firebase.publishers: replaced '%s'", name)
            return
    _registry.append(_Publisher(name=name, fn=fn, every_ticks=every_ticks))
    logger.info(
        "firebase.publishers: registered '%s' (every=%d ticks, total=%d)",
        name, every_ticks, len(_registry),
    )


def run_all(tick: int) -> dict[str, str]:
    """Invoke every publisher whose cadence is due at this tick.

    Each publisher runs under its own try/except so one failure doesn't
    block the rest.  Returns a ``{name: "ok"|"fail"}`` map of actions
    taken this tick, useful for observability / dashboard / tests.
    """
    results: dict[str, str] = {}
    for p in _registry:
        if tick == 0 or tick % p.every_ticks != 0:
            # tick==0 is the bootstrap pulse; skip expensive publishers
            # on it (agents haven't had a chance to emit anything yet).
            continue
        try:
            p.fn()
            results[p.name] = "ok"
        except Exception:
            results[p.name] = "fail"
            logger.debug(
                "firebase.publishers: '%s' failed this tick (non-fatal)",
                p.name,
                exc_info=True,
            )
    return results


def registered_names() -> list[str]:
    """Observability helper: names of all registered publishers, in
    execution order.  Useful for startup diagnostics and tests."""
    return [p.name for p in _registry]


# ── Default wiring ──────────────────────────────────────────────────
#
# This is the single source of truth for WHICH publishers the gateway
# runs and HOW OFTEN.  Adding a new dashboard report means a single
# ``register(...)`` line here, not a fresh edit to the heartbeat body
# in main.py.


def install_defaults() -> None:
    """Register every built-in publisher at its standard cadence.

    Call once at gateway startup.  Safe to call again — registrations
    are idempotent by name.
    """
    # Fast cadence (every tick / 60s): liveness signals that the
    # dashboard uses to decide whether the gateway is alive at all.
    from app.firebase_reporter import heartbeat
    register("heartbeat", heartbeat, every_ticks=1)

    # Anomaly detector runs hot so alerts fire quickly on regressions.
    register("anomaly_detector", _anomaly_tick, every_ticks=1)

    # Slow cadence (every 5 ticks / 5 min): bulk dashboard syncs.  These
    # pages don't need sub-minute freshness and each is a Firestore
    # round-trip that costs real money in high volumes.
    from app.firebase_reporter import (
        report_anomalies, report_variants, report_tech_radar,
        report_deploys, report_proposals, report_proposal_actions,
        report_philosophy_kb, report_evolution_stats,
    )
    register("anomalies",         report_anomalies,         every_ticks=5)
    register("variants",          report_variants,          every_ticks=5)
    register("tech_radar",        report_tech_radar,        every_ticks=5)
    register("deploys",           report_deploys,           every_ticks=5)
    register("proposals",         report_proposals,         every_ticks=5)
    register("proposal_actions",  report_proposal_actions,  every_ticks=5)
    register("philosophy_kb",     report_philosophy_kb,     every_ticks=5)
    register("evolution_stats",   report_evolution_stats,   every_ticks=5)

    # Optional — only if SubIA publish helper imports cleanly
    try:
        from app.firebase.publish import report_subia_state
        register("subia_state", report_subia_state, every_ticks=5)
    except ImportError:
        logger.debug("firebase.publishers: subia_state not available", exc_info=True)


def _anomaly_tick() -> None:
    """Run the anomaly-detector sweep.  Kept as a tiny internal
    wrapper so the registry sees a single zero-arg callable and we
    have a stable name to log under."""
    from app.anomaly_detector import collect_and_check, handle_alerts
    alerts = collect_and_check()
    if alerts:
        handle_alerts(alerts)
