"""epistemic_claims — recent claims the system made in the last 24h.

Reads ``app.epistemic.claim_ledger`` (the runtime tracking of WHAT
the system claims, distinct from ``app.episteme`` which is research
RAG). Surfaces the 3 most-recent + their calibration status — so the
operator can spot drift between what the agents asserted and what
turned out to be true.

Soft fail when the ledger module is unavailable (e.g. on a slim
deploy that ships without the epistemic subsystem)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ID = "epistemic-claims"
DISPLAY_NAME = "🔍 Recent claims (24h)"
DESCRIPTION = (
    "The 3 most recent claims the system made plus their calibration "
    "(confirmed / pending / refuted). Spots drift between agent "
    "assertions and ground truth."
)

_MAX_LINES = 3


def gather() -> list[str]:
    try:
        from app.epistemic import claim_ledger
    except Exception:
        logger.debug("epistemic_claims: ledger import failed", exc_info=True)
        return []
    # Try a few common API shapes — the module's surface has evolved
    # across the phase changes documented in CLAUDE.md.
    rows: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        if hasattr(claim_ledger, "list_recent"):
            rows = claim_ledger.list_recent(hours=24) or []
        elif hasattr(claim_ledger, "recent_claims"):
            rows = claim_ledger.recent_claims(hours=24) or []
        elif hasattr(claim_ledger, "list_all"):
            for r in claim_ledger.list_all() or []:
                ts = r.get("ts") or r.get("created_at")
                if not ts:
                    continue
                try:
                    if datetime.fromisoformat(str(ts)) >= cutoff:
                        rows.append(r)
                except ValueError:
                    continue
    except Exception:
        logger.debug("epistemic_claims: read failed", exc_info=True)
        return []
    if not rows:
        return []
    out: list[str] = []
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    for r in rows[:_MAX_LINES]:
        text = (r.get("claim") or r.get("text") or r.get("summary") or "").strip()
        status = (r.get("calibration") or r.get("status") or "pending").strip()
        if not text:
            continue
        out.append(f"  • [{status}] {text[:120]}")
    return out
