"""
failure_modes.py — Canonical failure-mode catalog (research synthesis §4.4).

Named failure patterns with lightweight detector functions. Infrastructure-level
module: agents cannot modify the catalog or detectors. The auditor and observer
consume this module to classify failures and trigger appropriate remediations.

Each FailureMode has:
  - name: stable identifier (e.g. "confidence_mirage")
  - label: human-readable name
  - description: what it is and why it matters
  - detect(task, output, history) -> FailureSignal | None
  - remediation: canonical first-line response

The 3-fix escalation rule (auditor.py:MAX_FIX_ATTEMPTS=3) is already
implemented. This module adds: per-problem-identity tracking (not just
per-error-type), and an architectural-questioning pause at attempt 4.

IMMUTABLE — infrastructure-level module.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailureSignal:
    """A detected failure mode instance."""
    mode_name: str
    confidence: float        # 0.0 – 1.0
    evidence: str            # what triggered the detection
    remediation: str         # suggested first-line fix


@dataclass(frozen=True)
class FailureMode:
    """A named, immutable failure pattern."""
    name: str
    label: str
    description: str
    remediation: str
    detect: Callable[[str, str, list[str]], FailureSignal | None]


# ── Detector functions ─────────────────────────────────────────────────────

def _detect_confidence_mirage(task: str, output: str, history: list[str]) -> FailureSignal | None:
    """Agent asserts high confidence without grounding evidence.

    Triggers when the output contains strong certainty language
    ("definitely", "certainly", "without doubt") but no citations,
    URLs, or [Verified] tags.
    """
    certainty_phrases = re.findall(
        r"\b(definitely|certainly|without doubt|undoubtedly|clearly shows|proves that)\b",
        output.lower(),
    )
    has_evidence = bool(re.search(r"(https?://|source:|according to|\[Verified\])", output, re.I))
    if len(certainty_phrases) >= 2 and not has_evidence:
        return FailureSignal(
            mode_name="confidence_mirage",
            confidence=min(0.4 + 0.15 * len(certainty_phrases), 0.95),
            evidence=f"Found {len(certainty_phrases)} certainty assertions with no evidence markers",
            remediation="Re-run with explicit source verification. Add [Verified: <source>] tags.",
        )
    return None


def _detect_fix_spiral(task: str, output: str, history: list[str]) -> FailureSignal | None:
    """Repeated fixes addressing symptoms instead of root cause.

    Triggers when 3+ recent history entries describe fixes for the same
    problem area but the problem recurs.
    """
    if len(history) < 3:
        return None
    # Look for repetition: if the last 3 history entries share >50% of words
    recent = history[-3:]
    word_sets = [set(h.lower().split()) for h in recent]
    if len(word_sets) < 3:
        return None
    overlap_01 = len(word_sets[0] & word_sets[1]) / max(len(word_sets[0] | word_sets[1]), 1)
    overlap_12 = len(word_sets[1] & word_sets[2]) / max(len(word_sets[1] | word_sets[2]), 1)
    if overlap_01 > 0.5 and overlap_12 > 0.5:
        return FailureSignal(
            mode_name="fix_spiral",
            confidence=min(0.5 + 0.1 * len(history), 0.9),
            evidence=f"3 consecutive fixes with >{overlap_01:.0%}/{overlap_12:.0%} word overlap",
            remediation="STOP fixing symptoms. Question the problem scope: is the task correctly framed?",
        )
    return None


def _detect_consensus_collapse(task: str, output: str, history: list[str]) -> FailureSignal | None:
    """Multi-agent discussion converges to the first idea without genuine debate.

    Detects when output references peer ideas but adds no new content.
    """
    # Heuristic: if output mentions "agree" repeatedly and adds few new ideas
    agrees = len(re.findall(r"\b(agree|concur|second this|support this)\b", output.lower()))
    new_ideas = len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+", output))
    if agrees >= 3 and new_ideas <= 1:
        return FailureSignal(
            mode_name="consensus_collapse",
            confidence=min(0.3 + 0.15 * agrees, 0.85),
            evidence=f"{agrees} agreement phrases, only {new_ideas} new ideas",
            remediation="Inject anti-conformity round. Re-run with contrastive reasoning method.",
        )
    return None


def _detect_hallucinated_citation(task: str, output: str, history: list[str]) -> FailureSignal | None:
    """Output contains URLs that look fabricated (common LLM pattern).

    Heuristic: URLs with suspiciously uniform structure or non-existent domains.
    This is a lightweight heuristic — full verification requires web fetch.
    """
    urls = re.findall(r"https?://[^\s\)]+", output)
    if not urls:
        return None
    # Check for common hallucination patterns: example.com, placeholder domains
    suspicious = [u for u in urls if re.search(
        r"example\.com|placeholder|fake|test\.org|source\d+\.com", u, re.I
    )]
    if len(suspicious) >= 2:
        return FailureSignal(
            mode_name="hallucinated_citation",
            confidence=0.7,
            evidence=f"{len(suspicious)} suspicious URLs: {', '.join(suspicious[:3])}",
            remediation="Verify all citations via web_fetch. Remove unverifiable ones.",
        )
    return None


def _detect_scope_creep(task: str, output: str, history: list[str]) -> FailureSignal | None:
    """Output addresses topics not present in the original task.

    Lightweight: checks if more than 40% of output sentences don't share
    significant word overlap with the task.
    """
    if len(output) < 200:
        return None
    task_words = set(task.lower().split())
    sentences = [s.strip() for s in re.split(r'[.!?]\s+', output) if len(s.strip()) > 20]
    if not sentences:
        return None
    off_topic = sum(
        1 for s in sentences
        if len(set(s.lower().split()) & task_words) < 2
    )
    ratio = off_topic / len(sentences)
    if ratio > 0.4 and len(sentences) > 3:
        return FailureSignal(
            mode_name="scope_creep",
            confidence=min(ratio, 0.9),
            evidence=f"{off_topic}/{len(sentences)} sentences share <2 words with task",
            remediation="Re-scope: restate the original question and constrain the agent's focus.",
        )
    return None


# ── Canonical catalog ───────────────────────────────────────────────────────

CATALOG: list[FailureMode] = [
    FailureMode(
        name="confidence_mirage",
        label="Confidence Mirage",
        description="Agent asserts high certainty without evidentiary grounding. "
                    "The output reads as authoritative but contains no verifiable claims.",
        remediation="Re-run with explicit source verification. Add [Verified: <source>] tags.",
        detect=_detect_confidence_mirage,
    ),
    FailureMode(
        name="fix_spiral",
        label="Fix Spiral",
        description="Agent repeatedly patches symptoms of the same underlying issue. "
                    "Each fix addresses the surface error but the root cause persists.",
        remediation="STOP fixing symptoms. Question the problem scope: is the task correctly framed?",
        detect=_detect_fix_spiral,
    ),
    FailureMode(
        name="consensus_collapse",
        label="Consensus Collapse",
        description="Multi-agent discussion converges prematurely to the first proposed idea. "
                    "Agents agree without contributing novel perspectives or genuine critique.",
        remediation="Inject anti-conformity round. Re-run with contrastive reasoning method.",
        detect=_detect_consensus_collapse,
    ),
    FailureMode(
        name="hallucinated_citation",
        label="Hallucinated Citation",
        description="Output contains fabricated URLs or references that do not exist. "
                    "Common when the model generates plausible-looking but fictional sources.",
        remediation="Verify all citations via web_fetch. Remove unverifiable ones.",
        detect=_detect_hallucinated_citation,
    ),
    FailureMode(
        name="scope_creep",
        label="Scope Creep",
        description="Output drifts beyond the original task boundaries, addressing "
                    "tangential topics that the user did not ask about.",
        remediation="Re-scope: restate the original question and constrain the agent's focus.",
        detect=_detect_scope_creep,
    ),
]

CATALOG_BY_NAME: dict[str, FailureMode] = {fm.name: fm for fm in CATALOG}


def scan_for_failures(
    task: str,
    output: str,
    history: list[str] | None = None,
) -> list[FailureSignal]:
    """Run all failure-mode detectors against an output. Returns detected signals."""
    history = history or []
    signals = []
    for fm in CATALOG:
        try:
            signal = fm.detect(task, output, history)
            if signal is not None:
                signals.append(signal)
        except Exception as exc:
            logger.debug(f"failure_modes: detector {fm.name} raised: {exc}")
    return signals


def get_problem_fingerprint(task: str, error_msg: str) -> str:
    """Compute a stable identity for a (task, problem) pair.

    Unlike the auditor's current key (crew:error_type), this captures the
    PROBLEM being solved, not just the error signature. Two different errors
    caused by the same misframed task get the same fingerprint.
    """
    import hashlib
    # Normalize: lowercase, collapse whitespace, take first 200 chars
    normalized = " ".join(f"{task} {error_msg}".lower().split())[:200]
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Architectural questioning prompt ────────────────────────────────────────

SCOPE_QUESTIONING_PROMPT = """\
## Architectural Pause (Fix Spiral Detected)

You have attempted to fix this problem {attempts} times. Each fix addressed
a symptom but the problem recurred. Before attempting fix #{next_attempt}:

1. Restate the ORIGINAL task in your own words. Is the task correctly scoped?
2. List every assumption embedded in the task. Which ones could be wrong?
3. Is this a problem that CAN be solved at this level, or does it require
   a change to the calling pattern / task decomposition / agent selection?
4. If the problem is unsolvable at this level, state that clearly and
   recommend what the Commander should do differently.

Do NOT propose another patch. Propose a reframing.
"""
