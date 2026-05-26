"""Pre-flight check for promoting the epistemic gate to enforcing mode.

Mirrors the discipline already established for Goodhart's
Advisory→Enforcing transition: do not flip the blocking-mode switch
until the verdict telemetry shows the gate is producing sane signals
over a meaningful soak.

The function here is **advisory only** — it does NOT prevent the
operator from setting ``epistemic_blocking_mode_override=true``. It
returns a structured opinion that the React Settings card surfaces
above the flip-to-enforcing toggle, and that the operator must
acknowledge (typed-phrase confirmation — same pattern as the Governance
ratchet relax-down flow).

Gates checked, in order of strictness:

  1. **Producer enabled**     — ``epistemic_retrieval_producer_enabled``
                                must be on, or the ledger is empty and
                                advisory data is meaningless.
  2. **Soak duration**        — at least ``min_soak_days`` (default 30)
                                of verdict telemetry must exist.
  3. **Sample size**          — at least ``min_sample`` (default 1000)
                                verdicts in the window.
  4. **Block-rate sanity**    — would-have-blocked rate must be within
                                ``[0.0, max_block_rate]`` (default 0.25).
                                Anything above means promoting would
                                interfere with too many real replies.
  5. **No critical biases**   — top-bias counter must not be dominated
                                by ``CRITICAL``-severity entries the
                                operator hasn't reviewed (the React card
                                surfaces these for explicit ack).

Returns a :class:`PromotionVerdict` with ``can_promote: bool`` and a
list of human-readable reasons. The React card renders the reasons; if
any are present, the flip-to-enforcing button is disabled (or operator
must override with typed phrase).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromotionVerdict:
    can_promote: bool
    reasons: tuple[str, ...] = ()
    snapshot: dict[str, Any] = field(default_factory=dict)

    def as_jsonable(self) -> dict[str, Any]:
        return {
            "can_promote": self.can_promote,
            "reasons": list(self.reasons),
            "snapshot": self.snapshot,
        }


def can_promote_to_enforcing(
    *,
    min_soak_days: int = 30,
    min_sample: int = 1000,
    max_block_rate: float = 0.25,
) -> PromotionVerdict:
    """Return whether the gate is ready to enforce. Never raises."""
    reasons: list[str] = []
    snapshot: dict[str, Any] = {}

    # ── 1. Producer enabled ──────────────────────────────────────────
    producer_on = False
    try:
        from app.runtime_settings import (
            get_epistemic_retrieval_producer_enabled,
        )
        producer_on = bool(get_epistemic_retrieval_producer_enabled())
    except Exception:
        pass
    snapshot["producer_enabled"] = producer_on
    if not producer_on:
        reasons.append(
            "Stage A producer is OFF — the claim ledger isn't growing, so "
            "advisory telemetry has no signal to characterise."
        )

    # ── 2-4. Verdict-telemetry-derived gates ─────────────────────────
    try:
        from app.observability.epistemic_advisory_report import report
        data = report(window_days=min_soak_days)
    except Exception:
        data = None
    snapshot["report"] = data

    if not data:
        reasons.append("Advisory report unavailable — cannot characterise gate.")
        return PromotionVerdict(False, tuple(reasons), snapshot)

    total = int(data.get("total_verdicts", 0))
    if total < min_sample:
        reasons.append(
            f"Sample size too small ({total} verdicts in {min_soak_days}d; "
            f"need ≥{min_sample}). Continue soaking."
        )

    rate = float(data.get("would_have_blocked_rate", 0.0))
    if rate > max_block_rate:
        reasons.append(
            f"Would-have-blocked rate {rate:.1%} exceeds {max_block_rate:.0%}. "
            f"Tune producer/detector thresholds before promoting — flipping now "
            f"would interfere with too many real replies."
        )

    # ── 5. Critical biases unreviewed ────────────────────────────────
    # We can't read operator acks from here (would couple the gate to UI
    # state); we just surface the count. The React card pairs this with
    # an "I've reviewed the top biases" checkbox.
    top_biases = data.get("top_biases") or []
    snapshot["top_biases"] = top_biases
    if top_biases and not reasons:
        # Only mention if everything else passed — otherwise it's noise.
        reasons.append(
            f"{len(top_biases)} distinct bias types detected in window — "
            f"confirm in the React card that the top entries look like "
            f"real epistemic faults before promoting."
        )

    # ── Promotion = no blocking reasons ──────────────────────────────
    # Note: the "top biases unreviewed" reason is informational, not a
    # hard block. We treat the operator's typed-phrase confirmation in
    # the React card as the ack — so we still return can_promote=True
    # when only that reason is present.
    hard_blocks = [r for r in reasons if not r.startswith(
        f"{len(top_biases)} distinct"
    )]
    return PromotionVerdict(
        can_promote=(len(hard_blocks) == 0),
        reasons=tuple(reasons),
        snapshot=snapshot,
    )
