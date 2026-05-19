"""cross_monitor_pattern — meta-detector over the identity continuity ledger.

Phase 4 of the elegance plan. The system has ~40 monitors as of 2026-05;
each fires on its own threshold and emits its own ledger landmark. None
of them notice when *several different monitors* fire on the *same path*
within a short window — that's the signature of a deeper architectural
problem the per-monitor alerts miss.

This monitor reads recent continuity-ledger events, groups by
``detail.path`` (most monitors emit one), and alerts when ≥3 distinct
event KINDS converge on the same path. The alert says "look here —
multiple subsystems are unhappy with this file" without trying to
diagnose the cause.

Why the ledger?
---------------

The identity continuity ledger is the canonical "monitor landmark"
surface. Almost every monitor that emits at all uses ``record_event``.
Reading the ledger gives us a uniform view of "what monitors said this
week" without scraping a dozen per-monitor JSONL files with diverging
schemas.

Discipline
----------

* **No new event kind.** Reuses ``architectural_debt_drift`` for its
  own emission, since a convergent cluster IS an architectural debt
  signal.
* **Dedup via persisted fingerprints.** A cluster fingerprint is
  ``(path, sorted_kinds)``; re-detecting the same fingerprint inside
  the dedup window is silent until the cluster's composition changes.
* **Default ON.** Observational only — no CR-creation, no destructive
  actions, no auto-revert. Failure-isolated.

Cadence
-------

Daily probe with an internal 7-day cadence gate inside ``run``.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


NAME = "cross_monitor_pattern"
CADENCE_SECONDS = 24 * 3600
MASTER_SWITCH_KEY = "cross_monitor_pattern_monitor_enabled"

# Internal cadence — once per week. The probe is cheap (reads a JSONL),
# but we don't want noise from re-alerting on the same cluster daily.
_INTERNAL_CADENCE_S = 7 * 24 * 3600

# How far back to look at the ledger. 14 days is short enough that a
# cluster fading without recurrence won't keep showing up, long enough
# to catch slow convergence patterns.
_LOOKBACK_DAYS = 14

# At least this many distinct event KINDS must hit the same path before
# we call it a convergent cluster.
_MIN_KIND_DIVERSITY = 3

# Per-cluster dedup: once we've alerted on a (path, kinds) fingerprint,
# stay silent for this many days unless the composition changes.
_DEDUP_DAYS = 30

# Cap how many clusters surface in one alert. Beyond this the alert
# turns into a wall and the operator stops reading.
_MAX_REPORTED_CLUSTERS = 5

# Skip ledger entries that don't have a usable ``detail.path`` —
# without a path the convergence claim has no anchor.
_PATH_KEYS_IN_DETAIL: tuple[str, ...] = ("path", "filepath", "file")


def _workspace_root() -> Path:
    env = os.environ.get("WORKSPACE_ROOT")
    if env:
        return Path(env)
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _state_path() -> Path:
    return _workspace_root() / "healing" / "cross_monitor_pattern_state.json"


# ── enable / cadence ────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_cross_monitor_pattern_monitor_enabled
        return get_cross_monitor_pattern_monitor_enabled()
    except Exception:
        return True


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"last_run": 0.0, "fingerprints": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"last_run": 0.0, "fingerprints": {}}
        data.setdefault("fingerprints", {})
        return data
    except Exception:
        return {"last_run": 0.0, "fingerprints": {}}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _cadence_due(state: dict[str, Any]) -> bool:
    return (time.time() - float(state.get("last_run") or 0)) >= _INTERNAL_CADENCE_S


# ── cluster detection ──────────────────────────────────────────────────


def _extract_path(detail: dict[str, Any]) -> str:
    for key in _PATH_KEYS_IN_DETAIL:
        v = detail.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _cluster_events(
    events: list[Any],
) -> dict[str, dict[str, list[str]]]:
    """Group events by path → kind → list-of-actors.

    Returns ``{path: {kind: [actors...]}}``. Paths with empty string
    (no path in detail) are skipped — without a path the convergence
    claim has no anchor.
    """
    clusters: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        detail = getattr(ev, "detail", None) or {}
        if not isinstance(detail, dict):
            continue
        path = _extract_path(detail)
        if not path:
            continue
        kind = getattr(ev, "kind", "")
        actor = getattr(ev, "actor", "")
        if not kind:
            continue
        clusters[path][kind].append(actor or "unknown")
    return {p: dict(kinds) for p, kinds in clusters.items()}


def _convergent_clusters(
    raw: dict[str, dict[str, list[str]]],
) -> list[dict[str, Any]]:
    """Pick clusters that meet the diversity threshold."""
    out: list[dict[str, Any]] = []
    for path, kinds in raw.items():
        if len(kinds) < _MIN_KIND_DIVERSITY:
            continue
        out.append({
            "path": path,
            "kinds": sorted(kinds.keys()),
            "event_count": sum(len(actors) for actors in kinds.values()),
            "actors": sorted({a for actors in kinds.values() for a in actors}),
        })
    out.sort(key=lambda c: (-c["event_count"], c["path"]))
    return out


def _fingerprint(cluster: dict[str, Any]) -> str:
    return f"{cluster['path']}|{','.join(cluster['kinds'])}"


def _is_fresh(fingerprint: str, dedup_state: dict[str, Any], now: datetime) -> bool:
    """True iff this fingerprint isn't in the dedup window."""
    entry = dedup_state.get(fingerprint)
    if not entry:
        return True
    last_iso = entry.get("last_alerted_at", "")
    if not isinstance(last_iso, str):
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    return (now - last) >= timedelta(days=_DEDUP_DAYS)


# ── alert ──────────────────────────────────────────────────────────────


def _emit_alert(clusters: list[dict[str, Any]]) -> None:
    body_lines: list[str] = [
        f"{len(clusters)} path(s) with ≥{_MIN_KIND_DIVERSITY} "
        f"distinct monitor kinds firing in the last {_LOOKBACK_DAYS} days:"
    ]
    for c in clusters[:_MAX_REPORTED_CLUSTERS]:
        kinds = ", ".join(c["kinds"])
        body_lines.append(
            f"• `{c['path']}` — {c['event_count']} events across {len(c['kinds'])} "
            f"kinds: {kinds}"
        )
    try:
        from app.notify import notify
        notify(
            title="🔀 Convergent monitor pattern",
            body="\n".join(body_lines),
            url="/cp/code-health",
            topic=f"cross_monitor:{len(clusters)}",
            arbitrate=True,
        )
    except Exception:
        logger.debug("cross_monitor_pattern: notify failed", exc_info=True)
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="architectural_debt_drift",
            actor="cross_monitor_pattern",
            summary=(
                f"{len(clusters)} convergent monitor cluster(s) detected; "
                f"top path: {clusters[0]['path']}"
            ),
            detail={
                "convergent_clusters": clusters[:_MAX_REPORTED_CLUSTERS],
            },
        )
    except Exception:
        logger.debug("cross_monitor_pattern: ledger emit failed", exc_info=True)


# ── public entry ────────────────────────────────────────────────────────


def detect_convergent_clusters(
    *, lookback_days: int = _LOOKBACK_DAYS, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read recent ledger events and return convergent clusters.

    Pure read-only — safe to call from tests + diagnostics. The daemon
    ``run`` function adds cadence + dedup + Signal emission on top.
    """
    try:
        from app.identity.continuity_ledger import list_events
    except Exception:
        return []
    cur = now or datetime.now(timezone.utc)
    since = (cur - timedelta(days=lookback_days)).isoformat()
    try:
        events = list_events(since_iso=since)
    except Exception:
        logger.debug("cross_monitor_pattern: list_events raised", exc_info=True)
        return []
    raw = _cluster_events(events)
    return _convergent_clusters(raw)


def run() -> dict[str, Any]:
    """One probe: detect convergent clusters, alert on novel ones."""
    summary: dict[str, Any] = {
        "checked": False, "n_clusters": 0, "n_alerted": 0, "errors": 0,
    }
    if not _enabled():
        summary["disabled"] = True
        return summary
    state = _read_state()
    if not _cadence_due(state):
        summary["skipped_cadence"] = True
        return summary

    try:
        clusters = detect_convergent_clusters()
        summary["n_clusters"] = len(clusters)
        now = datetime.now(timezone.utc)
        dedup = state.get("fingerprints", {}) or {}
        fresh = [c for c in clusters if _is_fresh(_fingerprint(c), dedup, now)]
        if fresh:
            _emit_alert(fresh)
            summary["n_alerted"] = len(fresh)
            for c in fresh:
                dedup[_fingerprint(c)] = {"last_alerted_at": now.isoformat()}
        # Prune dedup entries older than 2× the window — bounded growth.
        cutoff = (now - timedelta(days=2 * _DEDUP_DAYS)).isoformat()
        dedup = {
            fp: entry for fp, entry in dedup.items()
            if isinstance(entry, dict) and entry.get("last_alerted_at", "") >= cutoff
        }
        state["fingerprints"] = dedup
        state["last_run"] = time.time()
        _write_state(state)
        summary["checked"] = True
    except Exception:
        logger.debug("cross_monitor_pattern: probe failed", exc_info=True)
        summary["errors"] += 1
    return summary
