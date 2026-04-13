"""Correction capture — detect "actually it's X" patterns + persist them.

Two responsibilities:
  1. Detect that a user's message corrects a prior bot response.
  2. Synchronously persist the correction:
        - upsert a verified belief (evidence_sources=['user_correction'])
        - supersede contradicting prior beliefs
        - register a source URL if the user named one
        - append to narrative audit (Phase 3) for an immutable record

Synchronous on purpose: the failure mode this fixes is "I'll remember
this" followed by silent forgetting. We block the next turn until the
write has either succeeded or hit the configured timeout.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .belief_adapter import BeliefAdapter
from .source_registry import SourceRegistry

logger = logging.getLogger(__name__)


# ── Detection patterns (deterministic; LLM disambiguation optional) ─

_CORRECTION_PATTERNS = [
    # Permissive marker-form: "actually <anything> <currency>"
    # Handles "actually it's €0.595", "in fact 0.595 EUR", "no, €0.595"
    re.compile(
        r"\b(?:actually|in\s+fact|no,?|nope|wrong)\b[^.\n]{0,60}?"
        r"([€$£¥]\s*\d{1,4}(?:[.,]\d+)?|\b\d{1,4}[.,]\d+\s*(?:EUR|USD|GBP)\b)",
        re.IGNORECASE,
    ),
    # "I see/think/know that the price is X" — explicit knowledge claim
    re.compile(
        r"\bI\s+(?:see|think|know|believe)\b[^.\n]{0,80}?"
        r"(?:price|value|amount|figure|number|cost)\s+(?:is|was)\s+"
        r"([€$£¥]?\s*\d{1,4}(?:[.,]\d+)?(?:\s*(?:EUR|USD|GBP))?)",
        re.IGNORECASE,
    ),
    # "it's actually 0.595" / "that's really €0.595"
    re.compile(
        r"(?:it'?s|that'?s)\s+(?:actually|really)\s+"
        r"([€$£¥]\s*\d{1,4}(?:[.,]\d+)?|\d{1,4}[.,]\d+\s*(?:EUR|USD|GBP))",
        re.IGNORECASE,
    ),
    # "the correct/actual figure is X"
    re.compile(
        r"\b(?:correct|actual|right|real)\s+(?:price|figure|value|number|amount)\s+"
        r"(?:is|was)\s+([€$£¥]?\s*\d{1,4}(?:[.,]\d+)?(?:\s*(?:EUR|USD|GBP))?)",
        re.IGNORECASE,
    ),
]


# Source-registration patterns: "you can get this from <source>"
_SOURCE_HINT_PATTERNS = [
    re.compile(
        r"\b(?:you\s+can\s+get|get\s+this|fetch|use|try)\b[^.]{0,80}?"
        r"\b(?:from|on|at)\s+([A-Z][\w\s]{2,40}(?:\s+(?:homepage|website|page|site|exchange))?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:source|reference)\s*[:=]\s*([A-Z][\w\s]{2,60})",
        re.IGNORECASE,
    ),
]


# Map well-known source phrases to canonical URLs. Extend as encountered.
_SOURCE_URL_MAP = {
    "tallinn stock exchange": "https://nasdaqbaltic.com/",
    "nasdaq baltic":          "https://nasdaqbaltic.com/",
    "nasdaqbaltic":           "https://nasdaqbaltic.com/",
    "yahoo finance":          "https://finance.yahoo.com/",
    "google finance":         "https://www.google.com/finance/",
    "bloomberg":              "https://www.bloomberg.com/",
    "reuters":                "https://www.reuters.com/",
    "stockopedia":            "https://www.stockopedia.com/",
}


def _resolve_source_to_url(name: str) -> Optional[str]:
    norm = re.sub(r"\s+homepage$|\s+website$|\s+page$|\s+site$",
                  "", name.strip().lower())
    if norm in _SOURCE_URL_MAP:
        return _SOURCE_URL_MAP[norm]
    # Partial match
    for key, url in _SOURCE_URL_MAP.items():
        if key in norm or norm in key:
            return url
    return None


# ── Data ─────────────────────────────────────────────────────────────

@dataclass
class DetectedCorrection:
    raw_value: str                       # what the regex captured
    normalized_value: str                # whitespace-stripped
    topic_hint: str = ""                 # e.g. "share_price"
    attributed_date: str = ""
    matched_pattern: str = ""
    suggested_source_phrase: str = ""    # e.g. "Tallinn Stock Exchange"
    suggested_source_url: str = ""       # e.g. "https://nasdaqbaltic.com/"


# ── Capture orchestrator ─────────────────────────────────────────────

class CorrectionCapture:
    """Detect + persist user corrections."""

    def __init__(
        self,
        *,
        belief_adapter: BeliefAdapter,
        source_registry: SourceRegistry,
        narrative_audit_fn=None,         # injected: append_audit
        on_persist=None,                 # optional callback for tests
    ) -> None:
        self._beliefs = belief_adapter
        self._registry = source_registry
        self._audit = narrative_audit_fn
        self._on_persist = on_persist

    # ── Detection ───────────────────────────────────────────────────
    def detect(
        self,
        user_message: str,
        *,
        prior_response: str = "",
    ) -> Optional[DetectedCorrection]:
        if not user_message:
            return None
        # Try each value pattern
        for pat in _CORRECTION_PATTERNS:
            m = pat.search(user_message)
            if m:
                raw = m.group(1).strip()
                # Topic hint comes from the prior response (more reliable
                # than the user's terse message).
                topic = self._infer_topic(user_message, prior_response)
                date = self._infer_date(prior_response)
                src_phrase, src_url = self._extract_source_hint(user_message)
                return DetectedCorrection(
                    raw_value=raw,
                    normalized_value=re.sub(r"\s+", "", raw),
                    topic_hint=topic,
                    attributed_date=date,
                    matched_pattern=pat.pattern[:60],
                    suggested_source_phrase=src_phrase,
                    suggested_source_url=src_url,
                )
        # Source-only hint without value (e.g. "use Tallinn Stock Exchange")
        src_phrase, src_url = self._extract_source_hint(user_message)
        if src_url:
            topic = self._infer_topic(user_message, prior_response)
            return DetectedCorrection(
                raw_value="",
                normalized_value="",
                topic_hint=topic,
                attributed_date=self._infer_date(prior_response),
                matched_pattern="source_hint_only",
                suggested_source_phrase=src_phrase,
                suggested_source_url=src_url,
            )
        return None

    @staticmethod
    def _infer_topic(user_message: str, prior_response: str) -> str:
        from .claims import _topic_hint_for     # internal reuse
        text = (user_message or "") + " " + (prior_response or "")
        return _topic_hint_for(text)

    @staticmethod
    def _infer_date(prior_response: str) -> str:
        from .claims import _DATE_PHRASE
        if not prior_response:
            return ""
        m = _DATE_PHRASE.search(prior_response)
        return m.group(0) if m else ""

    @staticmethod
    def _extract_source_hint(user_message: str) -> tuple:
        for pat in _SOURCE_HINT_PATTERNS:
            m = pat.search(user_message or "")
            if m:
                phrase = m.group(1).strip()
                url = _resolve_source_to_url(phrase)
                if url:
                    return phrase, url
        return "", ""

    # ── Persistence (synchronous, idempotent) ───────────────────────
    def persist(
        self,
        correction: DetectedCorrection,
        *,
        loop_count: int = 0,
    ) -> dict:
        """Write the correction into the world. Returns a report dict."""
        report: dict = {
            "belief_upserted": False,
            "beliefs_superseded": 0,
            "source_registered": False,
            "audit_appended": False,
        }
        # Source registration first (cheap, useful even if value missing)
        if correction.suggested_source_url and correction.topic_hint:
            try:
                self._registry.register(
                    topic=correction.topic_hint,
                    key="default",
                    url=correction.suggested_source_url,
                    learned_from="user_correction",
                    notes=f"learned from phrase: '{correction.suggested_source_phrase}'",
                )
                report["source_registered"] = True
            except Exception as exc:
                logger.warning("correction.persist: source register failed: %s", exc)

        # Belief upsert if a value was captured
        if correction.normalized_value:
            from .evidence import topic_key_for
            from .claims import FactualClaim, ClaimKind
            fake_claim = FactualClaim(
                text=correction.normalized_value,
                kind=ClaimKind.NUMERIC_PRICE,
                span=(0, len(correction.normalized_value)),
                normalized_value=correction.normalized_value,
                attributed_date=correction.attributed_date,
                topic_hint=correction.topic_hint,
            )
            key = topic_key_for(fake_claim)
            try:
                evidence = [{
                    "source": "user_correction",
                    "phrase": correction.suggested_source_phrase or "",
                    "url": correction.suggested_source_url or "",
                }]
                surviving = self._beliefs.upsert(
                    key, correction.normalized_value, evidence,
                    confidence=0.9,
                )
                report["belief_upserted"] = True
                # Asymmetric confirmation: supersede prior beliefs on same topic
                report["beliefs_superseded"] = self._beliefs.supersede_others(
                    key, surviving.belief_id,
                    reason="user_correction",
                )
            except Exception as exc:
                logger.warning("correction.persist: belief upsert failed: %s", exc)

        # Narrative audit — immutable record of the correction event.
        # Wrap defensively; audit-log I/O failures (e.g. read-only fs in
        # tests) must never break correction capture.
        if self._audit:
            try:
                self._audit(
                    finding=(
                        f"User correction captured: topic={correction.topic_hint or '(unknown)'}"
                        f" value={correction.normalized_value or '(no value)'}"
                        f" source={correction.suggested_source_url or '(none)'}"
                    ),
                    loop_count=loop_count,
                    sources=["correction_capture"],
                    severity="info",
                )
                report["audit_appended"] = True
            except Exception as exc:
                logger.debug("correction.persist: audit append swallowed: %s", exc)

        if self._on_persist:
            try:
                self._on_persist(correction, report)
            except Exception:
                pass

        return report

    def detect_and_persist(
        self,
        user_message: str,
        *,
        prior_response: str = "",
        loop_count: int = 0,
    ) -> Optional[DetectedCorrection]:
        c = self.detect(user_message, prior_response=prior_response)
        if c is None:
            return None
        self.persist(c, loop_count=loop_count)
        return c
