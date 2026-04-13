"""
subia.prediction.accuracy_tracker — per-domain rolling prediction
accuracy, serialized to wiki/self/prediction-accuracy.md.

Phase 6: the Phase 4 CIL loop already records prediction errors via
the predictive_layer. What it didn't do was aggregate those errors
into a per-domain view that (a) lets the system know where its
predictions are reliable and where they're not, and (b) feeds a
sustained-error signal back into cascade modulation and cache
eviction.

This module is the aggregation layer. A "domain" is the
(agent_role, operation_type) pair — fine-grained enough to distinguish
"researcher doing ingest" from "researcher doing lint", coarse enough
that samples accumulate usefully.

Operations:

  record_outcome(domain, error) -> None
  domain_accuracy(domain) -> float          # rolling mean
  domain_stats(domain) -> DomainStats
  has_sustained_error(domain, window=10, threshold=0.5) -> bool
  all_domains_summary() -> dict
  serialize_to_wiki() -> str                # markdown for wiki page

The tracker keeps the last WINDOW_SIZE errors per domain in-memory
and writes a compact markdown digest on demand. It never raises.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 6.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable

from app.paths import PREDICTION_ACCURACY
from app.safe_io import safe_write
from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# How many recent errors to keep per domain. Smaller = noisier but
# responsive; larger = smoother but slower to detect new regressions.
_WINDOW_SIZE = 50

# Threshold above which an individual error is classified as "bad"
# for the sustained-error detection.
_BAD_ERROR_THRESHOLD = 0.5

# Minimum samples before accuracy is considered meaningful.
_MIN_SAMPLES_FOR_SIGNAL = 3


@dataclass
class DomainStats:
    """Rolling accuracy stats for one (agent_role, operation_type) domain."""
    domain: str
    n_samples: int = 0
    mean_accuracy: float = 0.0
    recent_bad_count: int = 0
    last_error: float = 0.0
    last_updated: str = ""

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "n_samples": self.n_samples,
            "mean_accuracy": round(self.mean_accuracy, 4),
            "recent_bad_count": self.recent_bad_count,
            "last_error": round(self.last_error, 4),
            "last_updated": self.last_updated,
        }


class AccuracyTracker:
    """Per-domain rolling accuracy tracker.

    Thread-safe via an internal Lock. Instances can be owned by
    individual loops for test isolation; production code uses the
    module-level singleton via `get_tracker()`.
    """

    def __init__(
        self,
        *,
        window_size: int = _WINDOW_SIZE,
        bad_error_threshold: float = _BAD_ERROR_THRESHOLD,
    ) -> None:
        self.window_size = int(window_size)
        self.bad_error_threshold = float(bad_error_threshold)
        # domain -> deque of error floats
        self._errors: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.window_size)
        )
        # domain -> last_updated ISO
        self._last_updated: dict[str, str] = {}
        self._lock = Lock()

    # ── Mutation ─────────────────────────────────────────────────

    def record_outcome(
        self,
        domain: str,
        error: float,
        *,
        now_iso: str | None = None,
    ) -> None:
        """Record one prediction-error observation for a domain.

        Args:
            domain:   any string identifier; convention is
                      "{agent_role}:{operation_type}".
            error:    in [0.0, 1.0]; 0 = perfect, 1 = maximally wrong.
            now_iso:  override timestamp (tests only).
        """
        if not domain:
            return
        try:
            err = max(0.0, min(1.0, float(error)))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._errors[domain].append(err)
            self._last_updated[domain] = (
                now_iso or datetime.now(timezone.utc).isoformat()
            )

    # ── Queries ──────────────────────────────────────────────────

    def domain_stats(self, domain: str) -> DomainStats:
        with self._lock:
            errs = list(self._errors.get(domain, ()))
        if not errs:
            return DomainStats(domain=domain)
        mean_err = sum(errs) / len(errs)
        return DomainStats(
            domain=domain,
            n_samples=len(errs),
            mean_accuracy=max(0.0, min(1.0, 1.0 - mean_err)),
            recent_bad_count=sum(
                1 for e in errs if e >= self.bad_error_threshold
            ),
            last_error=errs[-1],
            last_updated=self._last_updated.get(domain, ""),
        )

    def domain_accuracy(self, domain: str) -> float:
        return self.domain_stats(domain).mean_accuracy

    def has_sustained_error(
        self,
        domain: str,
        *,
        window: int = 10,
        threshold: int = 3,
    ) -> bool:
        """True if at least `threshold` of the last `window` errors
        exceed bad_error_threshold — the sustained-error signal that
        cascade escalation and cache eviction key off.
        """
        with self._lock:
            errs = list(self._errors.get(domain, ()))
        if len(errs) < _MIN_SAMPLES_FOR_SIGNAL:
            return False
        recent = errs[-window:]
        bad = sum(1 for e in recent if e >= self.bad_error_threshold)
        return bad >= threshold

    def all_domains_summary(self) -> dict:
        """Structured digest of all domains — for dashboards and the
        wiki markdown serializer.
        """
        with self._lock:
            domains = list(self._errors.keys())
        return {
            "domains": [self.domain_stats(d).to_dict() for d in sorted(domains)],
            "global_mean_accuracy": self._global_mean(),
            "n_domains": len(domains),
            "window_size": self.window_size,
            "bad_error_threshold": self.bad_error_threshold,
        }

    def _global_mean(self) -> float:
        with self._lock:
            all_errors = []
            for errs in self._errors.values():
                all_errors.extend(errs)
        if not all_errors:
            return 0.0
        return round(1.0 - sum(all_errors) / len(all_errors), 4)

    # ── Serialization ───────────────────────────────────────────

    def serialize_to_wiki_markdown(self) -> str:
        """Render the tracker state as a wiki-compatible markdown page."""
        summary = self.all_domains_summary()
        now = datetime.now(timezone.utc).isoformat()
        lines = [
            "---",
            'title: "Prediction Accuracy (rolling)"',
            'slug: prediction-accuracy',
            'section: self',
            'page_type: log-entry',
            f'updated_at: "{now}"',
            f'n_domains: {summary["n_domains"]}',
            f'window_size: {summary["window_size"]}',
            "---",
            "",
            "# Prediction Accuracy",
            "",
            "Rolling per-domain prediction accuracy over the last "
            f"{summary['window_size']} samples per domain.",
            "",
            f"**Global mean accuracy:** {summary['global_mean_accuracy']:.3f}",
            f"**Domains tracked:** {summary['n_domains']}",
            "",
            "## Per-Domain Stats",
            "",
        ]
        if not summary["domains"]:
            lines.append("_No predictions recorded yet._")
        else:
            lines.append("| Domain | N | Mean Accuracy | Recent Bad | Last Error | Last Updated |")
            lines.append("|---|---|---|---|---|---|")
            for d in summary["domains"]:
                lines.append(
                    f"| {d['domain'][:60]} | {d['n_samples']} | "
                    f"{d['mean_accuracy']:.3f} | {d['recent_bad_count']} | "
                    f"{d['last_error']:.3f} | {d['last_updated'][:19]} |"
                )
        lines.extend([
            "",
            "## How this is used",
            "",
            "- `cascade` escalates to a higher LLM tier when a domain "
            "has sustained bad accuracy",
            "- `prediction_cache` evicts cached templates whose accuracy "
            "has decayed below threshold",
            "- `dispatch_gate` can refuse high-risk crews in "
            "low-accuracy domains (Phase 8 wiring)",
        ])
        return "\n".join(lines) + "\n"

    def save_to_wiki(self, path=None) -> None:
        """Atomically persist the markdown digest. Never raises."""
        try:
            target = path or PREDICTION_ACCURACY
            safe_write(target, self.serialize_to_wiki_markdown())
        except Exception:
            logger.exception("accuracy_tracker: save_to_wiki failed")

    # ── Convenience ─────────────────────────────────────────────

    def reset(self) -> None:
        """Wipe all state. Tests only."""
        with self._lock:
            self._errors.clear()
            self._last_updated.clear()


# ── Module-level singleton (lazy) ────────────────────────────────

_instance: AccuracyTracker | None = None
_instance_lock = Lock()


def get_tracker() -> AccuracyTracker:
    """Return the module-level singleton. Created on first call."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AccuracyTracker()
        return _instance


def reset_singleton() -> None:
    """Tests only: reset the module-level singleton."""
    global _instance
    with _instance_lock:
        _instance = None


# ── Helpers ──────────────────────────────────────────────────────

def domain_key(agent_role: str, operation_type: str) -> str:
    """Canonical domain key. Lowercased, stripped, colon-joined."""
    return f"{str(agent_role).strip().lower()}:{str(operation_type).strip().lower()}"


def record_prediction_error(
    agent_role: str,
    operation_type: str,
    error_magnitude: float,
    *,
    tracker: AccuracyTracker | None = None,
) -> None:
    """Convenience wrapper that builds the canonical domain key and
    records the error via the given tracker (or the singleton).
    """
    tracker = tracker or get_tracker()
    tracker.record_outcome(
        domain_key(agent_role, operation_type),
        float(error_magnitude),
    )
