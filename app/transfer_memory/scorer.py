"""Deterministic scoring for compiled transfer-memory drafts.

Scores are computed without an LLM — cheap heuristics over the content
text. Two signals:

  abstract_hits  — phrases that mark procedural / general guidance
                   ("verify", "validate", "before", "interface", ...)
  concrete_hits  — patterns that mark implementation specifics
                   (file paths, function names, fenced code, magic numbers)

Score = sigmoid((abstract_density − concrete_density) × k) — clamped to
0..1 so retrieval can blend it with semantic similarity.

Higher abstraction = transfers better across domains.
Lower abstraction = anchored to specifics; prefer same-domain only.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


# Phrases that signal abstract procedural guidance. Whole-word matched
# against lowercased content; case-insensitive and substring-safe.
_ABSTRACT_PHRASES: tuple[str, ...] = (
    "verify", "verification", "validate", "validation",
    "before", "after", "until", "when",
    "interface", "boundary", "contract", "invariant",
    "escalate", "rollback", "fall back", "fallback",
    "authoritative", "ground truth", "source of truth",
    "do not", "must not", "always", "never",
    "evidence", "policy", "principle", "pattern",
    "retry", "timeout", "deadline",
    "general", "generic", "abstract",
)


# Patterns that signal concrete implementation details.
_CONCRETE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"/(?:app|usr|etc|var|home|opt)/\S+"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]+\([^)]{0,40}\)"),  # foo() / foo(args)
    re.compile(r"`[^`\n]{1,60}`"),                           # inline code
    re.compile(r"```[\s\S]*?```"),                           # fenced code blocks
    re.compile(r"\b\d{3,}\b"),                               # 3+ digit numbers
    re.compile(r"[€$£¥]\s*\d"),
)


@dataclass
class AbstractionScore:
    score: float        # 0..1
    abstract_hits: int
    concrete_hits: int
    word_count: int


def score_abstraction(content: str) -> AbstractionScore:
    """Compute a deterministic abstraction score for content.

    Empty content scores 0.0 (treated as fully concrete for scoring
    purposes; the sanitiser hard-rejects empty content separately).

    The sigmoid keeps output in 0..1 with smooth gradients across the
    realistic range of densities. ``k=0.4`` was picked so typical
    procedural insights (~3-8 abstract per 100 words, 0-3 concrete) land
    in the 0.45-0.75 band where retrieval-rank can discriminate.
    """
    if not content:
        return AbstractionScore(
            score=0.0, abstract_hits=0, concrete_hits=0, word_count=0,
        )

    text_lower = content.lower()
    word_count = max(1, len(content.split()))

    abstract_hits = 0
    for phrase in _ABSTRACT_PHRASES:
        # Whole-word match avoids false positives like "valid" inside
        # "invalid" or "verify" inside "verifying" (which we'd want to
        # match — ``\b`` handles that, since the suffix is also word-class).
        abstract_hits += len(re.findall(rf"\b{re.escape(phrase)}\b", text_lower))

    concrete_hits = 0
    for pat in _CONCRETE_PATTERNS:
        concrete_hits += len(pat.findall(content))

    abstract_density = abstract_hits / word_count * 100
    concrete_density = concrete_hits / word_count * 100

    k = 0.4
    raw = (abstract_density - concrete_density) * k
    # Clamp before exp() to keep the sigmoid numerically stable on
    # pathological inputs (no whitespace + many regex hits → very
    # negative raw → math.exp overflow without this guard).
    raw = max(-50.0, min(50.0, raw))
    score = 1.0 / (1.0 + math.exp(-raw))

    return AbstractionScore(
        score=round(score, 3),
        abstract_hits=abstract_hits,
        concrete_hits=concrete_hits,
        word_count=word_count,
    )
