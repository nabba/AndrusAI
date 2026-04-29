"""
app/recovery — Capability Recovery Loop.

When a crew's final synthesis looks like a refusal ("I cannot…", "no
access to…", "<unavailable>") despite the existing per-failure-mode
fixes (vetting wrong-crew detection, credit failover, etc.), this
module gives the system one more chance to deliver a real answer
before we bother the user.

The loop runs AFTER vetting + critic, BEFORE outbound delivery:

    final_result
        ↓
    refusal_detector.detect(text)
        → RefusalSignal { category, confidence } | None
        ↓
    capability_librarian.find_alternatives(task, signal, used_crew)
        → [Alternative(strategy, crew, est_cost), ...]
        ↓
    strategy_runner.execute(alternatives, budget=2_attempts, ceiling=90s)
        → RecoveryResult(success, text, route_changed, note)
        ↓
    if success:
        deliver recovered text (with note if route_changed)
    else:
        compose diagnostic answer (forge-queued if frequency ≥ 3/week)

User decisions baked in (2026-04-28):
  * Hybrid sync/async — cheap strategies run sync; forge stays async
  * Conservative detection — only fires on confidence ≥ 0.8 + ≥40%
    refusal-text dominance
  * Frequency-driven forge: same gap 3+ times/week → auto-queue;
    user can also force-trigger via Signal command
  * Notes shown only when answer's source materially changes
  * English-only patterns (matches actual user-visible traffic)

Off by default until verified. Enable with RECOVERY_LOOP_ENABLED=true.
"""

from app.recovery.refusal_detector import (
    RefusalSignal,
    detect_refusal,
)
from app.recovery.librarian import (
    Alternative,
    find_alternatives,
)
from app.recovery.loop import (
    RecoveryResult,
    maybe_recover,
    is_enabled,
)

__all__ = [
    "RefusalSignal", "detect_refusal",
    "Alternative", "find_alternatives",
    "RecoveryResult", "maybe_recover",
    "is_enabled",
]
