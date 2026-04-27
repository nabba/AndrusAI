"""Introspection-question detector — deterministic, hot-path-safe.

Identifies messages where the user is asking AndrusAI about its own
internal state (feelings, frustration, mood, energy, attention, focus,
mental state, what's bothering it, etc.). When detected, the chat path
should inject live homeostasis + kernel state into the LLM context so
the answer can ground in actual data instead of falling back to the
generic "I have no feelings" disclaimer.

Two-tier strategy:
  1. Topical keywords ("frustrat*", "mood", "feel", "tired", "energy",
     "curious", "wonder", "your state", etc.) — cheap regex.
  2. Self-target hint ("you", "your") within the same clause — cheap
     scoring.

A message is classified as introspection when it has BOTH a topical
keyword AND a self-target hint. This prevents false positives like
"the user feels frustrated" (topical word but not self-targeted) or
"how are things?" (self-targeted but not topical).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class IntrospectionTopic(str, Enum):
    AFFECT       = "affect"        # frustration, curiosity, mood, feelings
    ENERGY       = "energy"        # tired, drained, energy, fatigue
    ATTENTION    = "attention"     # focus, attending to, working on
    SELF_STATE   = "self_state"    # state, condition, status, how are you
    CAPABILITY   = "capability"    # what can you, are you able
    LIMITATION   = "limitation"    # what can't you, what bothers you
    META         = "meta"          # consciousness, self-awareness, sentience


# ── Topical keyword groups ──────────────────────────────────────────

_TOPIC_PATTERNS = [
    (IntrospectionTopic.AFFECT, re.compile(
        r"\b(?:frustrat\w*|mood|moods|feel(?:ing)?s?|emotion\w*|"
        r"happy|sad|angry|annoy\w+|content|"
        r"curios\w+|curiosity|wonder(?:ing|ed)?|excite\w+|"
        r"bored?|interested|enjoy(?:ing|ed)?|calm|stressed|anxious)\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.ENERGY, re.compile(
        r"\b(?:tired|drain\w+|exhaust\w+|fatigue\w*|energ\w+|"
        r"refresh\w+|rest(?:ed|ing)?|alert)\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.ATTENTION, re.compile(
        r"\b(?:focus(?:ing|ed)?|attention|attending|"
        r"working\s+on|paying\s+attention|noticing|aware)\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.SELF_STATE, re.compile(
        r"\b(?:how\s+are\s+you|are\s+you\s+(?:ok|okay|alright|fine|well)|"
        r"your\s+(?:state|condition|status|level|wellbeing|well[- ]being)|"
        r"what'?s\s+(?:up|wrong|going\s+on)\s+with\s+you|"
        r"how\s+do\s+you\s+(?:feel|do))\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.CAPABILITY, re.compile(
        r"\b(?:what\s+can\s+you|are\s+you\s+able|"
        r"can\s+you\s+(?:do|handle|process|understand|see|hear))\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.LIMITATION, re.compile(
        r"\b(?:what'?s?\s+(?:bothering|annoying|troubling)\s+you|"
        r"what\s+can'?t\s+you|what\s+(?:limits|constrains)\s+you)\b",
        re.IGNORECASE,
    )),
    (IntrospectionTopic.META, re.compile(
        r"\b(?:conscious(?:ness)?|sentien\w+|self[- ]aware\w*|"
        r"phenomenal|qualia|subjective\s+experience|inner\s+life|"
        r"homeostasis|homeostatic|kernel|subia)\b",
        re.IGNORECASE,
    )),
]

# Self-target: the message is about THE BOT, not about a user/third party.
# We need a "you/your/yourself" anchor in the message. Imperatives like
# "tell me what frustrates you" still count because of the "you".
_SELF_TARGET = re.compile(
    r"\b(?:you|your|yourself|yours|u\s|ur\s)\b",
    re.IGNORECASE,
)

# Anti-patterns: messages that mention a topical keyword but are clearly
# about a third party, NOT introspection. e.g. "the user feels frustrated".
_THIRD_PARTY_PRECEDES_TOPIC = re.compile(
    r"\b(?:the\s+user|users?|customer\w*|client\w*|they|she|he|"
    r"someone|people|everybody|nobody)\b[^.?!]{0,40}?"
    r"(?:frustrat|mood|feel|emotion|tired|drain|energ|focus|attention|"
    r"curious|wonder|conscious|sentien)",
    re.IGNORECASE,
)


@dataclass
class IntrospectionMatch:
    is_introspection: bool
    topics: list                 # list[IntrospectionTopic]
    matched_phrases: list        # list[str] — the actual matched fragments
    self_targeted: bool
    third_party_anti_match: bool
    confidence: float            # 0.0-1.0


def classify_introspection(text: str) -> IntrospectionMatch:
    """Classify a single user message. Returns a structured match."""
    if not text or not text.strip():
        return IntrospectionMatch(
            is_introspection=False, topics=[], matched_phrases=[],
            self_targeted=False, third_party_anti_match=False, confidence=0.0,
        )

    topics: list = []
    phrases: list = []
    for topic, pat in _TOPIC_PATTERNS:
        for m in pat.finditer(text):
            topics.append(topic)
            phrases.append(m.group(0))

    self_targeted = bool(_SELF_TARGET.search(text))
    third_party = bool(_THIRD_PARTY_PRECEDES_TOPIC.search(text))

    # Confidence scoring
    confidence = 0.0
    if topics:
        confidence += 0.5
    if self_targeted:
        confidence += 0.3
    if len(set(topics)) > 1:
        confidence += 0.1   # multiple topical signals reinforce
    if any(t == IntrospectionTopic.META for t in topics):
        confidence += 0.1   # explicit consciousness keywords are unambiguous
    if third_party:
        confidence -= 0.4   # strong negative signal

    confidence = max(0.0, min(1.0, confidence))

    is_introspection = (
        bool(topics)
        and self_targeted
        and not third_party
        and confidence >= 0.5
    )

    return IntrospectionMatch(
        is_introspection=is_introspection,
        topics=list(set(topics)),
        matched_phrases=phrases,
        self_targeted=self_targeted,
        third_party_anti_match=third_party,
        confidence=round(confidence, 3),
    )


def is_introspection_question(text: str) -> bool:
    """Cheap predicate — True iff the message is asking AndrusAI about
    its own state. Use when you only need yes/no."""
    return classify_introspection(text).is_introspection
