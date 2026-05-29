"""Outcome taxonomy for benchmark runs (2026-05-28).

A benchmark run can end three ways, and conflating them corrupts the signal
the alignment auditor and leaderboard depend on:

  * PASS         — ran and the scorer passed.
  * QUALITY_FAIL — ran and produced a (complete or over-long) answer that did
                   not pass. The MODEL fell short. Counts toward pass-rate —
                   it is a quality signal.
  * INFRA_ERROR  — the harness could not fairly measure the model at all (no
                   working model, provider 4xx/5xx, rate limit, timeout,
                   budget cap, wrong-shape result). NOT the model's fault —
                   excluded from pass-rate, counted as harness/infra health.

Truncation (output cut off by the token budget) is QUALITY_FAIL, not
INFRA_ERROR: now that the budget defers to the model's production default
(8192), an over-long completion is a verbosity/quality signal, not an outage.

The classifier is the single source of truth. The aggregator applies it on
read, so historical rows are reclassified automatically — no schema migration,
no stored ``outcome`` field to keep in sync.
"""
from __future__ import annotations

OUTCOME_PASS = "pass"
OUTCOME_QUALITY_FAIL = "quality_fail"
OUTCOME_INFRA_ERROR = "infra_error"

# Substrings (matched case-insensitively) that mark a model-side over-budget
# completion rather than an infrastructure failure.
_TRUNCATION_MARKERS = ("truncat", "max_tokens", "max tokens", "finish_reason")


def classify(error: str, passed: bool) -> str:
    """Map ``(error, passed)`` to one of the ``OUTCOME_*`` constants.

    Pure + total: never raises, defined for every input.
    """
    if not error:
        return OUTCOME_PASS if passed else OUTCOME_QUALITY_FAIL
    e = str(error).lower()
    if any(marker in e for marker in _TRUNCATION_MARKERS):
        return OUTCOME_QUALITY_FAIL
    # Any other non-empty error means the harness couldn't fairly run the
    # task. Default-to-infra is deliberate: an unrecognised failure must NOT
    # silently depress pass-rate — it surfaces as infra health for the operator.
    return OUTCOME_INFRA_ERROR


__all__ = [
    "OUTCOME_PASS",
    "OUTCOME_QUALITY_FAIL",
    "OUTCOME_INFRA_ERROR",
    "classify",
]
