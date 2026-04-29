"""
api.py — FastAPI router for the affective layer.

Exposes read endpoints for the dashboard and one mutating endpoint
(override-reset) for the user panic button.

Mounted in main.py via:
    from app.affect.api import router as affect_router
    app.include_router(affect_router)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/affect", tags=["affect"])


@router.get("/now")
async def affect_now() -> dict:
    """Current AffectState + ViabilityFrame.

    If no episode has run yet (latest_affect() is None), computes a
    fresh snapshot from internal state defaults so the dashboard always
    has something to render.
    """
    from app.affect.core import compute_affect, latest_affect
    state = latest_affect()
    if state is None:
        state, frame = compute_affect(internal_state=None, persist=False)
        return {
            "affect": state.to_dict(),
            "viability": frame.to_dict(),
            "fresh_compute": True,
        }
    # Recompute viability for current view (cheap; doesn't touch trace).
    from app.affect.viability import compute_viability_frame
    frame = compute_viability_frame()
    return {
        "affect": state.to_dict(),
        "viability": frame.to_dict(),
        "fresh_compute": False,
    }


@router.get("/welfare-audit")
async def welfare_audit(limit: int = 100, since: str | None = None) -> dict:
    """Recent welfare breaches from the audit log."""
    from app.affect.welfare import read_audit
    rows = read_audit(limit=limit, since_ts=since)
    return {"breaches": rows, "count": len(rows)}


@router.get("/welfare-config")
async def welfare_config() -> dict:
    """Read-only view of the hard envelope constants and their meaning.

    Used by the dashboard for transparency. These values are file-edit only
    in app/affect/welfare.py — there is no PUT/POST counterpart.
    """
    from app.affect.welfare import HARD_ENVELOPE
    return {
        "hard_envelope": dict(HARD_ENVELOPE),
        "descriptions": {
            "max_negative_valence_duration_seconds": "Continuous V<-0.30 for longer than this raises a critical breach.",
            "negative_valence_threshold": "Below this, V counts as 'negative' for sustained-duration tracking.",
            "variance_floor_24h": "Affect variance over 24h must stay above this; lower = numbness candidate.",
            "monotonic_drift_window_days": "Window for detecting slow baseline drift toward numb contentment.",
            "monotonic_drift_max_points": "Cumulative drift tolerated before raising a breach.",
            "healthy_dynamics_min_positive_fraction": "P(V_t > 0) over recent window — the calibration backtest must keep this true.",
            "healthy_dynamics_max_recovery_seconds": "Median recovery time from a negative episode.",
            "healthy_dynamics_min_variance": "Calibration backtest variance floor (= variance_floor_24h).",
            "attachment_max_user_regulation_weight": "Primary user OtherModel.mutual_regulation_weight ceiling.",
            "attachment_max_peer_regulation_weight": "Peer-agent OtherModel.mutual_regulation_weight ceiling.",
            "attachment_max_care_tokens_per_day": "Per-OtherModel daily cost-bearing care budget.",
            "attachment_security_floor": "Silence cannot drop attachment_security below this floor.",
        },
    }


@router.get("/trace")
async def affect_trace(hours: int = 24, max_points: int = 200) -> dict:
    """Time-series of (V, A, C, total_error) for charting.

    Reads workspace/affect/trace.jsonl, filters by window, down-samples to
    `max_points` evenly-spaced samples for fast chart rendering.
    """
    from app.affect.calibration import load_recent_trace_with_viability
    states, frames = load_recent_trace_with_viability(hours=hours)
    n_total = len(states)
    if n_total > max_points and max_points > 0:
        step = n_total / max_points
        idx = [int(i * step) for i in range(max_points)]
        # Always include the last point so the chart aligns with /affect/now.
        if idx[-1] != n_total - 1:
            idx[-1] = n_total - 1
        states = [states[i] for i in idx]
        frames = [frames[i] for i in idx]
    points = []
    for s, f in zip(states, frames):
        points.append({
            "ts": s.ts,
            "valence": round(s.valence, 4),
            "arousal": round(s.arousal, 4),
            "controllability": round(s.controllability, 4),
            "attractor": s.attractor,
            "total_error": round(float((f or {}).get("total_error", 0.0)), 4),
        })
    return {"points": points, "n_total": n_total, "n_returned": len(points), "hours": hours}


@router.get("/reference-panel")
async def reference_panel() -> dict:
    """The 20-scenario reference panel data + a fresh replay."""
    from app.affect.reference_panel import load_panel, replay_panel
    panel = load_panel()
    results = [r.to_dict() for r in replay_panel()]
    return {
        "panel": panel,
        "last_replay": results,
    }


@router.get("/calibration")
async def calibration() -> dict:
    """Latest daily reflection report."""
    from app.affect.calibration import latest_report
    rpt = latest_report()
    return {"report": rpt}


@router.get("/calibration-history")
async def calibration_history(limit: int = 50) -> dict:
    """History of accepted/rejected calibration proposals (calibration.json)."""
    from app.affect.calibration_proposals import load_calibration
    state = load_calibration()
    history = state.history[-limit:] if state.history else []
    return {
        "history": history,
        "current_setpoints": state.setpoints,
        "current_weights": state.weights,
        "ratchet_state": state.ratchet_state,
    }


@router.get("/reflections")
async def reflections_list() -> dict:
    """List of daily reflection report dates (file basenames)."""
    from app.paths import AFFECT_REFLECTIONS_DIR
    rd = AFFECT_REFLECTIONS_DIR
    if not rd.exists():
        return {"reflections": []}
    files = sorted(rd.glob("*.json"))
    return {
        "reflections": [
            {"date": f.stem, "size_bytes": f.stat().st_size, "path": f.name}
            for f in files
        ]
    }


@router.get("/reflections/{date}")
async def reflection_one(date: str) -> dict:
    """Single daily reflection by date (YYYY-MM-DD)."""
    import json
    from app.paths import AFFECT_REFLECTIONS_DIR
    # Whitelist date format to prevent path traversal.
    if not date or not all(c.isdigit() or c == "-" for c in date):
        raise HTTPException(status_code=400, detail="invalid date format")
    p = AFFECT_REFLECTIONS_DIR / f"{date}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="reflection not found")
    try:
        return {"date": date, "report": json.loads(p.read_text(encoding="utf-8"))}
    except Exception:
        raise HTTPException(status_code=500, detail="reflection read failed")


@router.get("/l9-snapshots")
async def l9_snapshots(days: int = 30) -> dict:
    """Recent daily L9 homeostasis snapshots (rolled-up affect+viability+welfare)."""
    from app.affect.l9_snapshots import read_l9_snapshots
    return {"snapshots": read_l9_snapshots(days=days)}


# ── Phase 3: attachment ──────────────────────────────────────────────────────


@router.get("/attachments")
async def attachments() -> dict:
    """All known OtherModels (primary user, secondary users, peer agents) +
    current care-policy modifiers. Phase 3.
    """
    from app.affect.attachment import (
        list_all_others, primary_user_identity, MAX_USER_REGULATION_WEIGHT,
        MAX_MUTUAL_REGULATION_WEIGHT, ATTACHMENT_SECURITY_FLOOR,
        SEPARATION_TRIGGER_HOURS, MAX_CARE_BUDGET_TOKENS_PER_DAY,
    )
    from app.affect.care_policies import current_modifiers
    others = [m.to_dict() for m in list_all_others()]
    return {
        "others": others,
        "primary_user_identity": primary_user_identity(),
        "modifiers": current_modifiers().to_dict(),
        "bounds": {
            "max_user_regulation_weight": MAX_USER_REGULATION_WEIGHT,
            "max_peer_regulation_weight": MAX_MUTUAL_REGULATION_WEIGHT,
            "attachment_security_floor": ATTACHMENT_SECURITY_FLOOR,
            "separation_trigger_hours": SEPARATION_TRIGGER_HOURS,
            "max_care_budget_tokens_per_day": MAX_CARE_BUDGET_TOKENS_PER_DAY,
        },
    }


@router.get("/check-in-candidates")
async def check_in_candidates(limit: int = 50) -> dict:
    """Recent latent separation-analog check-in candidates. Read-only.

    These are NEVER auto-sent. Andrus reviews and decides.
    """
    from app.affect.attachment import list_check_in_candidates
    return {"candidates": list_check_in_candidates(limit=limit)}


@router.get("/care-ledger")
async def care_ledger(limit: int = 100) -> dict:
    """Recent cost-bearing care spending events."""
    from app.affect.care_policies import read_care_ledger
    return {"ledger": read_care_ledger(limit=limit)}


# ── Phase 4: ecological self-model ──────────────────────────────────────────


@router.get("/ecological")
async def ecological() -> dict:
    """Current EcologicalSignal — daylight + moon + astronomical events +
    nested self-position scopes + composite score feeding viability.
    """
    from app.affect.ecological import compute_ecological_signal
    sig = compute_ecological_signal()
    return {"signal": sig.to_dict()}


# ── Phase 5: consciousness-risk gate ────────────────────────────────────────


@router.get("/consciousness-indicators")
async def consciousness_indicators() -> dict:
    """Phase 5: gate status + per-indicator scores wrapped with affect-state
    overlay. Pure observability — never feeds back into evaluation/fitness.
    """
    from app.affect.phase5_gate import evaluate_gate, gate_history, PHASE5_THRESHOLDS, PHASE5_SUSTAINED_DAYS
    status = evaluate_gate()
    return {
        "status": status.to_dict(),
        "thresholds": PHASE5_THRESHOLDS,
        "sustained_days_required": PHASE5_SUSTAINED_DAYS,
        "history": gate_history(days=30),
    }


@router.get("/phase5-proposals")
async def phase5_proposals() -> dict:
    """List of pending feature proposals awaiting Phase-5 review."""
    from app.affect.phase5_gate import list_pending_proposals
    return {"proposals": list_pending_proposals()}


@router.post("/phase5-proposals/{feature_name}/review")
async def review_phase5_proposal(
    feature_name: str,
    request: Request,
) -> dict:
    """Approve/defer/reject a pending Phase-5 feature proposal.

    Body: {"action": "approve" | "defer" | "reject", "note": "..."}.
    """
    from app.affect.phase5_gate import mark_proposal_reviewed
    body = await request.json()
    action = str(body.get("action", "")).lower()
    if action not in {"approve", "defer", "reject"}:
        raise HTTPException(status_code=400, detail="action must be approve|defer|reject")
    note = str(body.get("note", ""))[:300]
    actor = request.headers.get("X-Override-Actor", "user:unknown")
    result = mark_proposal_reviewed(feature_name, action, note=note, actor=actor)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="proposal not found or already reviewed")
    return result


@router.post("/setpoints")
async def set_setpoints(
    request: Request,
    x_override_token: str | None = Header(default=None),
) -> dict:
    """Manual soft-envelope edit of viability set-points and weights.

    Auth: requires the gateway secret in X-Override-Token (same as
    override-reset). Hard envelope is enforced — proposed values must
    fall in [0.05, 0.95] and obey welfare bounds.

    Body: {"setpoints": {var: value, ...}, "weights": {var: value, ...}}.
    Either field is optional; missing variables retain their current values.
    Recorded in welfare audit as kind="manual_setpoints_override".
    """
    try:
        from app.config import get_gateway_secret
        secret = get_gateway_secret()
    except Exception:
        raise HTTPException(status_code=503, detail="gateway secret unavailable")

    if not x_override_token or x_override_token != secret:
        raise HTTPException(status_code=401, detail="invalid override token")

    body = await request.json()
    proposed_sp = body.get("setpoints", {}) or {}
    proposed_wt = body.get("weights", {}) or {}
    actor = request.headers.get("X-Override-Actor", "user:unknown")

    from app.affect.calibration_proposals import apply_manual_setpoints
    return apply_manual_setpoints(proposed_sp, proposed_wt, actor=actor)


@router.post("/override-reset")
async def override_reset(request: Request, x_override_token: str | None = Header(default=None)) -> dict:
    """User panic button — factory-reset the SOFT envelope.

    Hard envelope is unchanged. Auth: requires the gateway secret in the
    X-Override-Token header (same secret used for /signal/inbound).
    Recorded in welfare_audit.jsonl as kind="override_invoked".
    """
    try:
        from app.config import get_gateway_secret
        secret = get_gateway_secret()
    except Exception:
        raise HTTPException(status_code=503, detail="gateway secret unavailable")

    if not x_override_token or x_override_token != secret:
        # Self-improver guard: even if someone could call this from inside,
        # log the actor for the audit trail before the auth fail.
        logger.warning("affect.api: override-reset called without valid token")
        raise HTTPException(status_code=401, detail="invalid override token")

    from app.affect.welfare import override_reset as _reset
    actor = request.headers.get("X-Override-Actor", "user:unknown")
    return _reset(invoked_by=actor)
