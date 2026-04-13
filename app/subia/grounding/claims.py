"""Factual-claim extractor (deterministic, hot-path-safe).

Identifies fragments in a draft response that REQUIRE backing evidence
before being sent to a user. The bar is intentionally narrow — we
only flag high-stakes claims (numeric + date OR numeric + source) so
chitchat and paraphrase aren't downgraded to ESCALATE.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ClaimKind(str, Enum):
    NUMERIC_PRICE         = "numeric_price"          # currency amount
    NUMERIC_QUANTITY      = "numeric_quantity"       # bare large number with unit
    DATE_ATTRIBUTED_FACT  = "date_attributed_fact"   # "on April 14, 2022, X was Y"
    SOURCE_ATTRIBUTED     = "source_attributed"      # "(Source: X)" / "according to X"


@dataclass
class FactualClaim:
    text: str                    # the matched fragment (≤120 chars)
    kind: ClaimKind
    span: tuple                  # (start, end) byte offsets in the draft
    normalized_value: str = ""   # e.g. "0.595" or "EUR 0.595"
    attributed_source: str = ""  # e.g. "Nasdaq Baltic" if mentioned
    attributed_date: str = ""    # e.g. "April 14, 2022" if mentioned
    topic_hint: str = ""         # heuristic — e.g. "share_price"

    def is_high_stakes(self) -> bool:
        """High-stakes = numeric + (date OR source) → must be backed."""
        if self.kind in (ClaimKind.NUMERIC_PRICE, ClaimKind.NUMERIC_QUANTITY):
            return bool(self.attributed_date or self.attributed_source)
        return False


# ── Regex patterns (Tier-3 — do not loosen without phase review) ────

# Currency amounts: €0.65, EUR 0.65, $1,234.56, £42, 0.595 EUR, etc.
_CURRENCY_NUM = re.compile(
    r"""
    (?:
        # Symbol-prefix:  €0.65   $50   £42
        [€$£¥]\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?
        |
        # Code-prefix:    EUR 0.65   USD 1,234.56
        \b(?:EUR|USD|GBP|JPY|CHF|SEK|NOK)\s+\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\b
        |
        # Code-suffix:    0.595 EUR   1,234.56 USD
        \b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\s+(?:EUR|USD|GBP|JPY|CHF|SEK|NOK)\b
    )
    """,
    re.VERBOSE,
)

# Date attribution near a fact: "on April 14, 2022", "in Q3 2024", "as of 2024-12-31"
_DATE_PHRASE = re.compile(
    r"""
    \b(?:
        on\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,)?\s+\d{4}
        |
        in\s+(?:Q[1-4]|H[12])\s+\d{4}
        |
        as\s+of\s+\d{4}-\d{2}-\d{2}
        |
        \d{4}-\d{2}-\d{2}
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Source attribution: "(Source: X)", "(source: X)", "according to X", "per X data"
_SOURCE_PHRASE = re.compile(
    r"""
    (?:
        \((?:[Ss]ource|[Vv]ia|[Pp]er):\s*([^)]{1,80})\)
        |
        according\s+to\s+([A-Z][\w\s&-]{1,40})
        |
        per\s+([A-Z][\w\s&-]{1,40})\s+data
    )
    """,
    re.VERBOSE,
)

# Topic hints — extend as the system encounters more domains.
_TOPIC_HINTS = (
    (re.compile(r"\b(?:share\s+price|stock\s+price|TAL1T|ticker)\b", re.I), "share_price"),
    (re.compile(r"\b(?:revenue|earnings|EPS|profit|EBITDA)\b", re.I),       "company_financials"),
    (re.compile(r"\b(?:market\s+cap|valuation|raised\s+series)\b", re.I),   "company_valuation"),
    (re.compile(r"\b(?:GDP|unemployment|CPI|inflation)\b", re.I),           "economic_indicator"),
)


def _topic_hint_for(text: str) -> str:
    for pat, topic in _TOPIC_HINTS:
        if pat.search(text):
            return topic
    return ""


def _nearby_window(text: str, span: tuple, radius: int = 80) -> str:
    s, e = span
    return text[max(0, s - radius): min(len(text), e + radius)]


def extract_claims(draft: str) -> list:
    """Return a list of FactualClaim found in the draft response.

    A draft with NO matches is presumed safe (chitchat, plan, etc.).
    A draft with low-stakes matches (e.g. a numeric value with no date
    or source) is also passed through — only HIGH-STAKES claims gate
    egress. Callers filter via FactualClaim.is_high_stakes().
    """
    if not draft:
        return []
    out: list[FactualClaim] = []

    # Currency / numeric matches anchor the claim
    for m in _CURRENCY_NUM.finditer(draft):
        window = _nearby_window(draft, m.span(), radius=120)
        date_match = _DATE_PHRASE.search(window)
        src_match = _SOURCE_PHRASE.search(window)
        attributed_source = ""
        if src_match:
            attributed_source = next(
                (g for g in src_match.groups() if g), ""
            ).strip()
        attributed_date = date_match.group(0) if date_match else ""
        topic = _topic_hint_for(window)
        out.append(FactualClaim(
            text=m.group(0),
            kind=ClaimKind.NUMERIC_PRICE,
            span=m.span(),
            normalized_value=re.sub(r"\s+", "", m.group(0)),
            attributed_source=attributed_source,
            attributed_date=attributed_date,
            topic_hint=topic,
        ))

    return out


def has_high_stakes_claims(draft: str) -> bool:
    """Quick predicate: any claim in the draft requires backing?"""
    return any(c.is_high_stakes() for c in extract_claims(draft))
