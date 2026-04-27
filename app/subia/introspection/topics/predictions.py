"""Predictions topic — Phase 6 accuracy tracker + kernel predictions list.

Surfaces what predictions have been made, accuracy by domain, recent
prediction errors. Lets the user audit "how good are your predictions
on coding tasks?" or "what did you predict would happen?".
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gather(*, max_predictions: int = 10) -> dict:
    out: dict = {
        "rolling_accuracy": {},
        "recent_predictions": [],
        "kernel_active": False,
    }

    # ── Rolling accuracy by domain (Phase 6) ───────────────────────
    try:
        from app.subia.prediction.accuracy_tracker import (
            get_default_tracker,
        )
        tracker = get_default_tracker()
        for domain in ("coding", "research", "writing", "media",
                       "ingest", "general"):
            try:
                acc = tracker.rolling_accuracy(domain)
                if acc is not None:
                    out["rolling_accuracy"][domain] = round(float(acc), 3)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("introspection.predictions: tracker failed: %s", exc)

    # ── Recent predictions from kernel ─────────────────────────────
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is None:
            return out
        out["kernel_active"] = True
        for p in (kernel.predictions or [])[-max_predictions:]:
            out["recent_predictions"].append({
                "operation": getattr(p, "operation", ""),
                "confidence": round(float(getattr(p, "confidence", 0.5) or 0.5), 3),
                "resolved": bool(getattr(p, "resolved", False)),
                "prediction_error": (
                    round(float(p.prediction_error), 3)
                    if getattr(p, "prediction_error", None) is not None
                    else None
                ),
                "predicted_outcome": (
                    str(getattr(p, "predicted_outcome", ""))[:120]
                ),
                "cached": bool(getattr(p, "cached", False)),
                "created_at": getattr(p, "created_at", ""),
            })
    except Exception as exc:
        logger.debug("introspection.predictions: kernel gather failed: %s", exc)
    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Prediction state + accuracy (Phase 6)"]

    if data.get("rolling_accuracy"):
        lines.append("Rolling accuracy by domain (recent N predictions):")
        for domain, acc in data["rolling_accuracy"].items():
            lines.append(f"  - {domain}: {acc:.3f}")
    else:
        lines.append(
            "Rolling accuracy: (no resolved predictions in tracker yet)"
        )

    if data.get("recent_predictions"):
        lines.append("Recent kernel predictions:")
        for p in data["recent_predictions"][-5:]:
            err = p.get("prediction_error")
            err_str = f" error={err}" if err is not None else " (unresolved)"
            cache = " [cached]" if p.get("cached") else ""
            lines.append(
                f"  - {p.get('operation','?')[:40]} "
                f"conf={p.get('confidence')}"
                f"{err_str}{cache}"
            )
    elif data.get("kernel_active"):
        lines.append(
            "Recent predictions: (kernel running but predictions list empty)"
        )
    return "\n".join(lines)
