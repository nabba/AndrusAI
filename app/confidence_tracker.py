"""
confidence_tracker.py — AUQ dual-process hallucination cascade prevention.

Implements the runtime prevention paradigm from Agentic Uncertainty
Quantification (arXiv:2601.15703): instead of diagnosing hallucination
cascades post-hoc, track confidence through the agent chain and trigger
reflection when it drops below threshold.

System 1 (fast): Heuristic confidence extraction from LLM responses
  — hedge phrase detection, structural signals, response completeness
System 2 (slow): Triggered only when System 1 detects low confidence
  — sets ctx.metadata["needs_reflection"] for commander's reflexion loop

The chain state lives in threading.local() (same pattern as rate_throttle.py),
resets per request, never blocks, never sets ctx.abort.

TIER_IMMUTABLE — confidence gating is safety-relevant.

Reference: arXiv:2601.15703 "Agentic Uncertainty Quantification"
"""

import logging
import re
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

CONFIDENCE_FLOOR = 0.25  # Below this → flag for reflection
CONFIDENCE_DECAY = 0.95  # Cumulative confidence decays per step
MIN_RESPONSE_LENGTH = 20  # Very short responses get a penalty

# ── Hedge phrase patterns (System 1: fast lexical scan) ──────────────────────

_HEDGE_PATTERNS = re.compile(
    r"\b("
    r"I'?m not (?:entirely |fully |completely )?(?:sure|certain|confident)"
    r"|(?:it'?s |this is )?(?:possible|plausible) that"
    r"|(?:may|might|could) (?:be|have been)"
    r"|I (?:think|believe|suspect) (?:that |so)?"
    r"|(?:uncertain|unclear|ambiguous|debatable)"
    r"|(?:I don'?t|I do not) (?:know|have|recall)"
    r"|(?:as far as I know|to (?:my|the best of my) knowledge)"
    r"|(?:approximately|roughly|around|about) \d"
    r"|(?:not necessarily|not always|sometimes)"
    r"|(?:I cannot verify|cannot confirm|unable to confirm)"
    r"|(?:this may be|this might be) (?:incorrect|inaccurate|outdated)"
    r")\b",
    re.I,
)

_REFUSAL_PATTERNS = re.compile(
    r"\b("
    r"I (?:can'?t|cannot|am unable to|won'?t|will not)"
    r"|(?:I'?m sorry|apologies|unfortunately)"
    r"|(?:not able to|not possible for me)"
    r"|(?:outside my|beyond my) (?:scope|capabilities|knowledge)"
    r")\b",
    re.I,
)

_CONFIDENCE_BOOSTERS = re.compile(
    r"\b("
    r"(?:definitely|certainly|absolutely|clearly|undoubtedly)"
    r"|(?:the answer is|the result is|the solution is)"
    r"|(?:here is|here are|the following)"
    r"|```"  # Code blocks signal concrete output
    r")\b",
    re.I,
)


# ── Chain state ──────────────────────────────────────────────────────────────

@dataclass
class ConfidenceFrame:
    """One step in the confidence chain."""
    agent_id: str
    step_index: int
    raw_confidence: float
    cumulative_confidence: float


_thread_local = threading.local()


def get_chain() -> list[ConfidenceFrame]:
    """Get the confidence chain for the current request thread."""
    if not hasattr(_thread_local, "chain"):
        _thread_local.chain = []
    return _thread_local.chain


def reset_chain() -> None:
    """Reset the confidence chain (called at start of each request)."""
    _thread_local.chain = []


# ── Confidence extraction (System 1: heuristic, <1ms) ────────────────────────

def extract_confidence(llm_response: str, agent_id: str = "") -> float:
    """Extract a confidence score from an LLM response using heuristics.

    Returns 0.0-1.0 where:
      1.0 = highly confident (concrete, specific, no hedging)
      0.5 = neutral
      0.0 = very uncertain (heavy hedging, refusals, empty)

    This is System 1 (fast path) — no LLM call, pure pattern matching.
    """
    if not llm_response or len(llm_response.strip()) < MIN_RESPONSE_LENGTH:
        return 0.3  # Very short/empty → low confidence

    text = llm_response[:3000]  # Cap scan length for performance

    # Count hedge indicators
    hedge_count = len(_HEDGE_PATTERNS.findall(text))
    refusal_count = len(_REFUSAL_PATTERNS.findall(text))
    booster_count = len(_CONFIDENCE_BOOSTERS.findall(text))

    # Base confidence
    score = 0.7

    # Hedge penalty: -0.08 per hedge phrase (up to -0.40)
    score -= min(0.40, hedge_count * 0.08)

    # Refusal penalty: -0.15 per refusal (up to -0.30)
    score -= min(0.30, refusal_count * 0.15)

    # Booster bonus: +0.05 per booster (up to +0.20)
    score += min(0.20, booster_count * 0.05)

    # Structural signals
    # Code blocks and structured output boost confidence
    if "```" in text:
        score += 0.10
    # Lists/numbered items suggest structured thinking
    if re.search(r"^\s*(?:\d+\.|[-*])\s", text, re.M):
        score += 0.05

    # Length signal: very long responses on simple queries may indicate padding
    word_count = len(text.split())
    if word_count > 500:
        score -= 0.05  # Minor penalty for excessive length

    return max(0.0, min(1.0, round(score, 3)))


# ── SUBIA integration ────────────────────────────────────────────────────────

def _perturb_coherence(confidence: float) -> None:
    """Low confidence perturbs SUBIA coherence downward."""
    if confidence >= 0.5:
        return
    try:
        from app.subia.kernel import get_active_kernel
        kernel = get_active_kernel()
        if kernel and hasattr(kernel, "homeostasis"):
            delta = -0.05 * (1.0 - confidence)  # Worse confidence → bigger drop
            current = kernel.homeostasis.variables.get("coherence", 0.5)
            kernel.homeostasis.variables["coherence"] = max(0.0, current + delta)
    except Exception:
        pass


# ── Lifecycle hooks ──────────────────────────────────────────────────────────

def create_confidence_gate_hook():
    """POST_LLM_CALL hook: extract confidence, track chain, flag for reflection."""
    def _hook(ctx):
        try:
            response = ctx.data.get("llm_response", "")
            if not response:
                response = str(ctx.modified_data.get("llm_response", ""))
            if not response:
                return ctx

            raw = extract_confidence(response, ctx.agent_id)
            chain = get_chain()
            step_idx = len(chain)

            # Cumulative confidence decays with each step
            prev_cumulative = chain[-1].cumulative_confidence if chain else 1.0
            cumulative = prev_cumulative * CONFIDENCE_DECAY * raw

            frame = ConfidenceFrame(
                agent_id=ctx.agent_id,
                step_index=step_idx,
                raw_confidence=raw,
                cumulative_confidence=round(cumulative, 3),
            )
            chain.append(frame)

            # Write to metadata for downstream consumers
            ctx.metadata["_step_confidence"] = raw
            ctx.metadata["_cumulative_confidence"] = cumulative
            ctx.metadata["confidence_chain"] = [
                {"agent": f.agent_id, "step": f.step_index,
                 "raw": f.raw_confidence, "cumulative": f.cumulative_confidence}
                for f in chain[-5:]  # Last 5 frames
            ]

            # System 2 trigger: flag for reflection if below floor
            if cumulative < CONFIDENCE_FLOOR:
                ctx.metadata["needs_reflection"] = True
                logger.info(
                    f"confidence_tracker: cumulative={cumulative:.2f} < {CONFIDENCE_FLOOR} "
                    f"— flagging for reflection (agent={ctx.agent_id}, step={step_idx})"
                )

            _perturb_coherence(raw)

        except Exception:
            pass  # Never block on confidence tracking failure
        return ctx
    return _hook


def create_confidence_reset_hook():
    """PRE_TASK hook: reset the confidence chain at the start of each task."""
    def _hook(ctx):
        reset_chain()
        return ctx
    return _hook
