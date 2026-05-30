"""Producer-approval health monitor (Gate C visibility, 2026-05-30).

Daily probe (internal weekly cadence) over the observational proposal
producers. Computes each producer's rolling explicit-operator-approval rate
via :mod:`app.change_requests.producer_health` and Signal-alerts on the
transitions the operator cares about:

  * **paused**   — a producer just crossed below the approval-rate floor and
    is now auto-paused (its CRs are recorded REJECTED at the gate instead of
    queued). The operator should look: fix the producer, approve some of its
    output by hand, or turn it off entirely.
  * **recovered** — a previously-paused producer is back above the floor (its
    rejections aged out of the window, or thresholds changed). Informational.

The monitor never mutates anything — the pause itself is computed live at the
CR gate; this surface is pure observability + dedup so the operator isn't
re-alerted every day a producer stays paused.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INTERNAL_CADENCE_S = 7 * 86400
_STATE_FILE = "workspace/healing/producer_approval_health_state.json"


def _enabled() -> bool:
    try:
        from app.change_requests.producer_health import config

        return bool(config()[0])
    except Exception:
        return True


def _load_state() -> dict:
    p = Path(_STATE_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = Path(_STATE_FILE)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, default=str, indent=2))
    except Exception:
        logger.debug("producer_approval_health: state save failed", exc_info=True)


def _send_alert(transition: str, body: str) -> None:
    try:
        from app.firebase.publish import publish_alert

        publish_alert(
            topic=f"producer_approval_health/{transition}",
            body=body,
            critical=False,
        )
    except Exception:
        logger.debug("producer_approval_health: publish_alert failed", exc_info=True)


def run() -> dict[str, Any]:
    """One monitor pass. Internal-cadence-gated; failure-isolated."""
    if not _enabled():
        return {"status": "disabled"}

    state = _load_state()
    now = time.time()
    if now - float(state.get("_last_run_at", 0)) < _INTERNAL_CADENCE_S:
        return {"status": "skipped_cadence"}

    try:
        from app.change_requests import producer_health

        _, floor, _min_samples, window = producer_health.config()
        producers = producer_health.known_observational_producers(window_days=window)
    except Exception:
        logger.debug("producer_approval_health: producer_health unavailable", exc_info=True)
        return {"status": "subsystem_unavailable"}

    prior = dict(state.get("paused", {}))  # requestor -> bool
    current: dict[str, bool] = {}
    transitions = 0

    for requestor in producers:
        try:
            verdict = producer_health.evaluate(requestor)
        except Exception:
            continue
        current[requestor] = verdict.paused
        was_paused = bool(prior.get(requestor, False))
        if verdict.paused and not was_paused:
            transitions += 1
            _send_alert(
                "paused",
                f"⏸️ Producer auto-paused: `{requestor}` — {verdict.reason}. "
                f"Its CRs are now recorded REJECTED at the gate instead of "
                f"queued. Review the producer, approve good output by hand, or "
                f"flip `producer_autopause_enabled` off in /cp/settings.",
            )
        elif was_paused and not verdict.paused:
            transitions += 1
            stats = verdict.stats
            detail = (
                f"{stats.approved}/{stats.n} approved over {window}d"
                if stats else "below sample floor"
            )
            _send_alert(
                "recovered",
                f"▶️ Producer un-paused: `{requestor}` is back above the "
                f"{floor:.0%} approval floor ({detail}).",
            )

    state["paused"] = current
    state["_last_run_at"] = now
    _save_state(state)
    return {
        "status": "ok",
        "producers_seen": len(producers),
        "paused_now": sum(1 for v in current.values() if v),
        "transitions": transitions,
    }
