"""
observability.publishers — Registry of cadence-aware observability publishers.

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

Storage
-------
Publishers write typed ``Snapshot`` rows via
:mod:`app.observability.snapshots` (Postgres-backed
``observability_snapshots`` table).  The React dashboard reads them
via the three ``/api/cp/observability/snapshots/*`` endpoints — the
Firestore tees that once sat beside these writes have been retired.

Usage
-----
At startup (see ``install_defaults``)::

    from app.observability import publishers
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
            logger.debug("observability.publishers: replaced '%s'", name)
            return
    _registry.append(_Publisher(name=name, fn=fn, every_ticks=every_ticks))
    logger.info(
        "observability.publishers: registered '%s' (every=%d ticks, total=%d)",
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
                "observability.publishers: '%s' failed this tick (non-fatal)",
                p.name,
                exc_info=True,
            )
    return results


def registered_names() -> list[str]:
    """Observability helper: names of all registered publishers, in
    execution order.  Useful for startup diagnostics and tests."""
    return [p.name for p in _registry]


def register_snapshot(
    name: str,
    computer: Callable[[], "Snapshot | None"],
    *,
    every_ticks: int = 1,
) -> None:
    """Register a *pure-compute* publisher that returns a ``Snapshot``.

    The registry handles storage — callers never touch Postgres
    directly.  Computers are expected to be zero-I/O beyond reading
    local state (Postgres queries, in-memory caches, etc.) and to
    return a ``Snapshot`` or ``None`` (no-op this tick).

    Parameters
    ----------
    name : Publisher name — same namespace as :func:`register`.  Also
        used as the ``Snapshot.kind`` if the computer doesn't set one.
    computer : zero-arg callable returning a ``Snapshot | None``.
    every_ticks : cadence (see ``register``).

    Isolation properties:
      * the snapshot write runs under try/except; a failure is logged
        and the next tick retries.
      * a ``None`` return from the computer is a silent no-op (useful
        for publishers that should occasionally skip — e.g. "only emit
        when the subsystem is enabled").
    """
    def _wrapped() -> None:
        try:
            snap = computer()
            if snap is not None:
                from app.observability.snapshots import (
                    Snapshot, get_default_store,
                )
                # Accept either a full Snapshot or a raw dict (convenience
                # for publishers that don't want to import the dataclass).
                if not isinstance(snap, Snapshot):
                    snap = Snapshot(kind=name, payload=dict(snap))
                get_default_store().put(snap)
        except Exception:
            logger.debug(
                "observability.publishers: snapshot computer for '%s' raised "
                "(non-fatal)", name, exc_info=True,
            )

    # Reuse the standard register path so snapshot-backed publishers
    # appear alongside everything else in ``registered_names()`` + the
    # heartbeat dispatch loop.
    register(name, _wrapped, every_ticks=every_ticks)


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
    # Registered as the tee'd wrapper (Firestore + Postgres snapshot),
    # not the bare Firestore function, so the new
    # ``/api/cp/observability/snapshots/heartbeat/...`` endpoints have
    # fresh data to serve.  Dashboard can migrate to the Postgres
    # source whenever it's ready; until then both sinks run in parallel.
    register("heartbeat", _heartbeat_tee, every_ticks=1)

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
    # proposal_actions was formerly a Firestore polling loop — the
    # legacy HTML dashboard queued approve/reject clicks there and
    # this publisher drained the queue every 5 min.  That loop is
    # retired now that the POST endpoint
    # ``/api/cp/proposals/{id}/action`` exists; any new client applies
    # actions synchronously and the 0–5 min latency is gone.  Callers
    # wanting the old async-queue behaviour can POST from a script.

    # ── Snapshot publishers (Postgres-only) ─────────────────────────
    # Pure compute fns return a Snapshot that lands in
    # ``observability_snapshots``.  The previous ``firestore_fn=``
    # tee kwargs are gone — the React dashboard reads entirely from
    # ``/api/cp/*`` endpoints backed by local state + the new
    # ``/api/cp/observability/snapshots/*`` surface, so no consumer
    # needs the Firestore writes.  Re-adding the tee is a one-line
    # change per publisher if a future external consumer appears.
    register_snapshot("evolution_stats", _compute_evolution_stats_snapshot, every_ticks=5)
    register_snapshot("philosophy_kb",   _compute_philosophy_kb_snapshot,   every_ticks=5)
    register_snapshot("anomalies",       _compute_anomalies_snapshot,       every_ticks=5)
    register_snapshot("variants",        _compute_variants_snapshot,        every_ticks=5)
    register_snapshot("tech_radar",      _compute_tech_radar_snapshot,      every_ticks=5)
    register_snapshot("deploys",         _compute_deploys_snapshot,         every_ticks=5)
    register_snapshot("proposals",       _compute_proposals_snapshot,       every_ticks=5)

    # Optional — only if SubIA publish helper imports cleanly
    try:
        from app.firebase.publish import report_subia_state  # noqa: F401
        register("subia_state", _subia_state_tee, every_ticks=5)
    except ImportError:
        logger.debug("observability.publishers: subia_state not available", exc_info=True)

    # Stale-belief janitor — every 5 min, delete "working" beliefs whose
    # last_updated is older than 6 hours.  See
    # app/memory/belief_state.py::cleanup_stale_working_beliefs for the
    # HOT-3-preserving rationale.  Uses the publisher registry rather
    # than its own scheduler job so it shares the heartbeat cadence +
    # per-failure log + observability naming.
    register("stale_belief_cleanup", _stale_belief_cleanup, every_ticks=5)


def _stale_belief_cleanup() -> None:
    """Thin wrapper so the publisher registry sees a zero-arg callable."""
    from app.memory.belief_state import cleanup_stale_working_beliefs
    cleanup_stale_working_beliefs(max_age_hours=6)


def _anomaly_tick() -> None:
    """Run the anomaly-detector sweep.  Kept as a tiny internal
    wrapper so the registry sees a single zero-arg callable and we
    have a stable name to log under."""
    from app.anomaly_detector import collect_and_check, handle_alerts
    alerts = collect_and_check()
    if alerts:
        handle_alerts(alerts)


# ── Tee'd publishers (Firestore + Postgres snapshot) ─────────────────
#
# Transitional wrappers around the existing Firestore writers.  Each
# fires the legacy Firestore publish as before AND records a snapshot
# to the Postgres-backed ``observability_snapshots`` table so the new
# ``/api/cp/observability/snapshots/<kind>/...`` endpoints can serve
# the same data.  Once the dashboard pages for a given concern have
# migrated to read from the Postgres endpoints, the Firestore call can
# be removed.
#
# The two sinks are independent: a Firestore outage or quota failure
# doesn't drop the Postgres write, and vice versa.  Snapshot writes
# never raise back to the scheduler (the snapshot store swallows its
# own errors at debug level).


def _heartbeat_tee() -> None:
    """Fire the existing Firestore heartbeat write AND record a
    ``heartbeat`` snapshot to Postgres.

    Snapshot payload is intentionally minimal — just the liveness bit
    plus the ISO timestamp.  Everything else the old heartbeat cycle
    did (fleet status, benchmarks, token stats, circuit breakers,
    etc.) remains inside the Firestore writer for now; those each get
    their own snapshot kinds as their respective dashboard pages are
    migrated.
    """
    from app.firebase_reporter import heartbeat as _firestore_heartbeat
    from datetime import datetime, timezone
    from app.observability.snapshots import put as _put_snapshot
    # Firestore side (existing behavior)
    _firestore_heartbeat()
    # Postgres snapshot side (new)
    _put_snapshot("heartbeat", {
        "alive": True,
        "ts_iso": datetime.now(timezone.utc).isoformat(),
    })


def _subia_state_tee() -> None:
    """Fire the existing Firestore SubIA-state publish AND record a
    ``subia_state`` snapshot to Postgres.

    The snapshot payload reuses the same data the Firestore writer
    computes — we just capture it once, fan it out to both sinks.
    This is the first concern where Firestore and Postgres carry
    identical JSON; the shape is stable enough that the dashboard can
    point at either source without reformatting.
    """
    from app.observability.snapshots import put as _put_snapshot
    # Firestore side (existing behavior — fire and forget)
    try:
        from app.firebase.publish import report_subia_state as _fs_pub
        _fs_pub()
    except Exception:
        logger.debug("subia_state: firestore publish failed (non-fatal)",
                     exc_info=True)
    # Postgres snapshot side (new) — compute the payload locally.
    payload: dict = {}
    try:
        from app.config import get_settings
        payload["enabled"] = bool(get_settings().subia_live_enabled)
    except Exception:
        payload["enabled"] = False
    if payload["enabled"]:
        try:
            from app.subia.live_integration import get_last_state
            live_state = get_last_state()
            if live_state:
                # Mirror the fields the Firestore writer publishes so
                # dashboard consumers see a consistent shape.
                kernel = getattr(live_state, "kernel", None)
                payload["loop_count"] = getattr(live_state, "loop_count", 0)
                payload["last_loop_at"] = getattr(live_state, "last_loop_at", None)
                payload["circadian_mode"] = getattr(live_state, "circadian_mode", "")
                payload["homeostasis"] = getattr(live_state, "homeostasis", {}) or {}
                scene = getattr(live_state, "scene", None)
                if scene is not None:
                    payload["scene_focal_n"] = len(getattr(scene, "focal", []) or [])
                    payload["scene_peripheral_n"] = len(
                        getattr(scene, "peripheral", []) or []
                    )
                    payload["wonder_intensity"] = max(
                        (getattr(i, "wonder", 0.0) for i in getattr(scene, "focal", []) or []),
                        default=0.0,
                    )
        except Exception:
            logger.debug("subia_state: live-state read failed (non-fatal)",
                         exc_info=True)
    _put_snapshot("subia_state", payload)


# ── Pure-compute snapshot publishers ────────────────────────────────
#
# These are the Phase-2 migrations: a ``compute_..._snapshot`` function
# that reads local state and returns a ``Snapshot``.  Zero I/O beyond
# the local Postgres / ChromaDB reads the business logic needs.  The
# registry handles storage (Postgres via ``register_snapshot``).


def _compute_evolution_stats_snapshot():
    """Evolution DGM-DB stats snapshot.

    Mirrors the payload the legacy ``report_evolution_stats`` wrote to
    Firestore collection ``status/evolution``.  Returns ``None`` (skip
    this tick) when the DGM-DB feature flag is off — same behaviour as
    the old function did by early-return.
    """
    from app.observability.snapshots import Snapshot
    import os
    if os.environ.get("EVOLUTION_USE_DGM_DB", "false").lower() != "true":
        return None
    try:
        from app.evolution_db.archive_db import get_evolution_stats
        stats = get_evolution_stats()
        recent = []
        for v in stats.get("recent", []):
            recent.append({
                "id": str(v.get("id", "")),
                "agent_name": v.get("agent_name", ""),
                "generation": v.get("generation", 0),
                "composite_score": v.get("composite_score") or 0.0,
                "passed": v.get("passed_threshold", False),
                "reasoning": (v.get("modification_reasoning") or "")[:100],
            })
        return Snapshot(kind="evolution_stats", payload={
            "total_variants":  stats.get("total_variants", 0),
            "passed_variants": stats.get("passed_variants", 0),
            "best_score":      stats.get("best_score", 0.0),
            "active_runs":     stats.get("active_runs", 0),
            "recent":          recent,
        })
    except Exception:
        logger.debug("evolution_stats snapshot compute failed",
                     exc_info=True)
        return None


def _compute_philosophy_kb_snapshot():
    """Philosophy KB stats snapshot.

    Mirrors the payload the legacy ``report_philosophy_kb`` wrote to
    Firestore collection ``status/philosophy_kb`` — total chunks /
    texts, traditions / authors / titles lists, and the full ``texts``
    listing (used by the dashboard's "browse texts" panel).
    """
    from app.observability.snapshots import Snapshot
    try:
        from app.philosophy.vectorstore import get_store
        store = get_store()
        stats = store.get_stats() or {}
        texts_list = store.list_texts()
        return Snapshot(kind="philosophy_kb", payload={
            "total_chunks": stats.get("total_chunks", 0),
            "total_texts":  stats.get("total_texts", 0),
            "traditions":   stats.get("traditions", []),
            "authors":      stats.get("authors", []),
            "titles":       stats.get("titles", []),
            "texts":        texts_list,
        })
    except Exception:
        logger.debug("philosophy_kb snapshot compute failed",
                     exc_info=True)
        return None


def _compute_anomalies_snapshot():
    """Recent anomaly alerts.  Payload mirrors the legacy
    ``status/anomalies`` Firestore doc: ``{"recent_alerts": [...]}``."""
    from app.observability.snapshots import Snapshot
    try:
        from app.anomaly_detector import get_recent_alerts
        alerts = get_recent_alerts(20)
        return Snapshot(kind="anomalies", payload={
            "recent_alerts": alerts or [],
        })
    except Exception:
        logger.debug("anomalies snapshot compute failed", exc_info=True)
        return None


def _compute_variants_snapshot():
    """Variant archive summary.  Payload mirrors the legacy
    ``status/variants`` doc: recent variants + drift score + max
    generation observed so far."""
    from app.observability.snapshots import Snapshot
    try:
        from app.variant_archive import get_recent_variants, get_drift_score
        recent = get_recent_variants(20) or []
        drift = get_drift_score()
        max_gen = max((v.get("generation", 0) for v in recent), default=0) if recent else 0
        return Snapshot(kind="variants", payload={
            "recent": recent,
            "drift_score": drift,
            "max_generation": max_gen,
        })
    except Exception:
        logger.debug("variants snapshot compute failed", exc_info=True)
        return None


def _compute_tech_radar_snapshot():
    """Tech-radar discoveries snapshot.

    Parses the ChromaDB-stored format
    ``[category] title: summary. Action: ...`` into structured rows.
    Payload mirrors legacy ``status/tech_radar`` doc.
    """
    from app.observability.snapshots import Snapshot
    try:
        import re as _re
        from app.memory.scoped_memory import retrieve_operational
        items = retrieve_operational(
            "scope_tech_radar", "technology discovery", n=20,
        ) or []
        pattern = _re.compile(
            r'\[(\w+)\]\s*(.+?):\s*(.+?)(?:\.\s*Action:\s*(.+))?$',
            _re.DOTALL,
        )
        discoveries: list[dict] = []
        for item in items:
            m = pattern.match(item)
            if m:
                discoveries.append({
                    "category": m.group(1),
                    "title":    m.group(2).strip(),
                    "summary":  m.group(3).strip(),
                    "action":   (m.group(4) or "").strip(),
                })
            else:
                discoveries.append({
                    "category": "unknown",
                    "title":    item[:80],
                    "summary":  item[:200],
                    "action":   "",
                })
        return Snapshot(kind="tech_radar", payload={
            "discoveries": discoveries[:15],
        })
    except Exception:
        logger.debug("tech_radar snapshot compute failed", exc_info=True)
        return None


def _compute_deploys_snapshot():
    """Recent deploy history snapshot — reads the local JSONL at
    ``/app/workspace/deploy_log.json`` (same source the old
    ``report_deploys`` used)."""
    from app.observability.snapshots import Snapshot
    try:
        import json as _json
        from pathlib import Path as _Path
        deploy_log = _Path("/app/workspace/deploy_log.json")
        if deploy_log.exists():
            entries = _json.loads(deploy_log.read_text())[-10:]
        else:
            entries = []
        return Snapshot(kind="deploys", payload={
            "recent": entries,
        })
    except Exception:
        logger.debug("deploys snapshot compute failed", exc_info=True)
        return None


def _compute_proposals_snapshot():
    """Pending evolution-proposal list snapshot.

    The legacy ``report_proposals(proposals)`` required its caller to
    pass the list; the registered-publisher path never supplied it, so
    the Firestore write has been silently erroring for a while.  This
    replacement sources the list directly from
    ``app.proposals.list_proposals("pending")`` so there's no hidden
    upstream dependency.
    """
    from app.observability.snapshots import Snapshot
    try:
        from app.proposals import list_proposals
        proposals = list_proposals("pending") or []
        return Snapshot(kind="proposals", payload={
            "proposals": proposals[:20],
        })
    except Exception:
        logger.debug("proposals snapshot compute failed", exc_info=True)
        return None
