"""
subia.connections.firecrawl_predictor — Firecrawl → Predictor closed
perception loop (SIA #6).

Per SubIA Part II §18:
    "PE generates predictions about expected Firecrawl content;
     actual content generates prediction errors.
     Example: PE predicts 'Truepic will announce Series C terms
     this week' → Firecrawl finds the announcement → low prediction
     error → confidence boost. OR Firecrawl finds Truepic pivoting
     strategy → high prediction error → surprise."

This module is the bridge. Call `record_firecrawl_outcome` whenever
a Firecrawl fetch completes. If a corresponding pre-recorded
prediction exists for the fetched source, the bridge computes the
error and routes it to the Phase-2 PP-1 surprise path.

No embedding required at this layer — the caller supplies the
actual-content summary and (optionally) its embedding. Prediction
errors are computed via the PredictiveLayer's compute_error so all
existing Phase 2/6 machinery (surprise routing, accuracy tracking,
cache invalidation) fires automatically.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 10.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FirecrawlOutcome:
    """Structured outcome of record_firecrawl_outcome()."""
    source_url: str = ""
    channel: str = ""
    prediction_error_recorded: bool = False
    surprise_level: str = "UNKNOWN"
    effective_surprise: float = 0.0
    routed_to_workspace: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "channel": self.channel,
            "prediction_error_recorded": self.prediction_error_recorded,
            "surprise_level": self.surprise_level,
            "effective_surprise": round(self.effective_surprise, 4),
            "routed_to_workspace": self.routed_to_workspace,
            "reason": self.reason,
        }


def record_firecrawl_outcome(
    *,
    source_url: str,
    channel: str,
    actual_content: str,
    actual_embedding: list[float] | None = None,
    predictive_layer: Any,
    context: str = "",
) -> FirecrawlOutcome:
    """Close the Firecrawl → Predictor loop.

    Args:
        source_url:        the URL Firecrawl fetched; used for
                           provenance + dedup.
        channel:           prediction channel, e.g.
                           'firecrawl:archibal:truepic' — a stable
                           name so PredictiveLayer can aggregate.
        actual_content:    summary of the fetched content (already
                           text-extracted).
        actual_embedding:  optional precomputed embedding. When None,
                           PredictiveLayer will embed internally.
        predictive_layer:  a PredictiveLayer instance. If None, the
                           call is a no-op (logged).
        context:           the a-priori context under which the
                           prediction was made (usually the prompt or
                           reason for the fetch).

    Returns a FirecrawlOutcome describing what fired. Never raises.
    """
    outcome = FirecrawlOutcome(
        source_url=str(source_url)[:500],
        channel=str(channel)[:100],
    )
    if predictive_layer is None:
        outcome.reason = "predictive_layer not attached"
        return outcome

    predict_fn = getattr(predictive_layer, "predict_and_compare", None)
    if not callable(predict_fn):
        outcome.reason = "predictive_layer has no predict_and_compare"
        return outcome

    try:
        error = predict_fn(
            channel=channel,
            context=context or f"Firecrawl fetch: {source_url}",
            actual_content=actual_content,
            actual_embedding=actual_embedding,
        )
    except Exception:
        logger.exception(
            "firecrawl_predictor: predict_and_compare failed",
        )
        outcome.reason = "predict_and_compare raised"
        return outcome

    outcome.prediction_error_recorded = True
    outcome.surprise_level = str(getattr(error, "surprise_level", "UNKNOWN"))
    outcome.effective_surprise = float(
        getattr(error, "effective_surprise", 0.0)
    )
    outcome.routed_to_workspace = bool(
        getattr(error, "routed_to_workspace", False)
    )
    outcome.reason = (
        "routed to workspace" if outcome.routed_to_workspace
        else "recorded without routing (below threshold)"
    )
    return outcome


def build_channel_key(venture: str, topic: str) -> str:
    """Canonical channel key for Firecrawl predictions.

    Using a stable scheme lets PredictiveLayer's per-channel
    confidence tracking accumulate predictable context.
    """
    venture = str(venture or "unknown").strip().lower()[:40]
    topic = str(topic or "").strip().lower()[:40]
    return f"firecrawl:{venture}:{topic}"
