"""Record what a length clamp actually dropped, instead of dropping it silently.

The answer path clips strings at many hops — ``investigation[:4000]`` into a
draft prompt, ``draft[:8000]`` into the critique prompt, ``[:6000]`` at the
multi-crew merge. Every one is a silent lossy operation, so "the report lost its
source list" could only ever be *inferred*, and the inference reached for the
wrong mechanism: `reports/GATE_DIAGNOSIS_2026-07-25.md` blamed `max_tokens`
truncation, but the token ledger shows **zero** completions pinned at the
research caps (3000 / 3500) across 62,675 calls in 14 days. A character clamp
would not appear there at all.

So the fix is the same one that worked for the search failure chain: record the
event rather than deduce it later. ``clamp`` is a drop-in for ``text[:limit]``
that logs the overflow and counts it, and changes no behaviour otherwise.

Deliberately not a cap change. Whether these limits are too tight is a separate
question that should be answered with the numbers this produces, per
`feedback_verify_before_recommending`.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
#: ``what`` -> (times clamped, total characters dropped, largest single drop).
_stats: dict[str, tuple[int, int, int]] = {}


def clamp(text: str | None, limit: int, *, what: str) -> str:
    """Return ``text`` cut to ``limit`` characters, recording any loss.

    ``what`` names the hop ("draft->critique"), because the useful question is
    never "was something dropped" but "which stage dropped it".
    """
    value = text or ""
    if limit < 0:
        limit = 0
    if len(value) <= limit:
        return value

    dropped = len(value) - limit
    with _lock:
        times, total, worst = _stats.get(what, (0, 0, 0))
        _stats[what] = (times + 1, total + dropped, max(worst, dropped))
    logger.warning(
        "content clamp: %s cut %d -> %d chars, dropped %d (%.0f%% of the input)",
        what, len(value), limit, dropped, 100.0 * dropped / len(value),
    )
    return value[:limit]


def stats() -> dict[str, dict[str, int]]:
    """Per-hop clamp counters, for telemetry and tests."""
    with _lock:
        return {
            what: {
                "times_clamped": times,
                "chars_dropped": total,
                "largest_drop": worst,
            }
            for what, (times, total, worst) in _stats.items()
        }


def reset_stats() -> None:
    """Clear the counters (tests, and after an intentional limit change)."""
    with _lock:
        _stats.clear()


__all__ = ["clamp", "stats", "reset_stats"]
