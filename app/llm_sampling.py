"""
llm_sampling.py — Phase-dependent sampling parameters for creative workflows.

Provides sampling presets for the three phases of the creative MAS pipeline
(Mechanism 5 in docs/creativity-synthesis research):

    diverge  — broad idea generation (high temperature, low min-p)
    discuss  — multi-round debate (moderate)
    converge — synthesis/final articulation (low temperature)

Non-creative callers use phase=None and get provider defaults (no change
from legacy behavior).

Parameter passthrough per provider:
    - Anthropic:  temperature, top_p
    - OpenRouter: temperature, top_p, presence_penalty
    - Ollama:     temperature, top_p, min_p (via extra_body.options)

[Unverified §7] Ollama min_p passthrough depends on the underlying llama.cpp
version. If the parameter is silently ignored, temperature + top_p still apply.
"""
from __future__ import annotations

from typing import Literal

Phase = Literal["diverge", "discuss", "converge"]

# Phase-dependent sampling table (synthesis §3.5, adapted).
# Values are deliberately conservative to preserve coherence — min_p paper
# shows T up to 1.5 stays coherent, but we cap divergence at 1.3 to reduce
# the hallucination risk on API-tier models that don't expose min_p.
_PHASE_PRESETS: dict[str, dict[str, float]] = {
    "diverge":  {"temperature": 1.3, "top_p": 0.95, "min_p": 0.05, "presence_penalty": 0.5},
    "discuss":  {"temperature": 0.9, "top_p": 0.92, "min_p": 0.10, "presence_penalty": 0.3},
    "converge": {"temperature": 0.5, "top_p": 0.90, "min_p": 0.10, "presence_penalty": 0.0},
}


def get_sampling_params(phase: Phase | None) -> dict[str, float] | None:
    """Return the raw sampling dict for a phase, or None if phase is None."""
    if phase is None:
        return None
    if phase not in _PHASE_PRESETS:
        raise ValueError(f"Unknown phase: {phase!r}. Expected one of {list(_PHASE_PRESETS)}.")
    return dict(_PHASE_PRESETS[phase])


def _affect_modulation(
    phase: Phase,
    affect_state: dict | None,
) -> tuple[float, float]:
    """Per-phase multipliers for (temperature, top_p) given an affect snapshot.

    Returns multiplicative factors clamped so output stays in sane bounds.
    Affect-state shape: {"valence": -1..1, "arousal": 0..1, "controllability": 0..1}.
    None or missing keys → (1.0, 1.0) (no modulation).

    Maps Panksepp's primary affective systems onto creative phases:
        diverge  ↔ SEEKING        — high arousal + positive valence widen exploration
        discuss  ↔ social/CAUTION — threat (negative valence + arousal) tightens top_p
        converge ↔ peace/coherence — high arousal cools T, low controllability tightens
    """
    if not affect_state:
        return 1.0, 1.0

    v = float(affect_state.get("valence", 0.0))
    a = float(affect_state.get("arousal", 0.0))
    c = float(affect_state.get("controllability", 0.5))

    pos_v = max(0.0, v)
    threat = max(0.0, -v) * a       # negative valence weighted by urgency

    if phase == "diverge":
        t_mult = (1.0 + 0.4 * a) * (1.0 + 0.3 * pos_v)
        p_mult = 1.0
    elif phase == "discuss":
        t_mult = 1.0 - 0.3 * threat
        # Low controllability → tighten top_p (less wandering when system feels
        # out of control of the discussion).
        p_mult = 1.0 - 0.15 * (1.0 - c)
    elif phase == "converge":
        # High arousal cools T further (push toward integration). Low controllability
        # also tightens — converge phase wants stability when uncertain.
        t_mult = (1.0 - 0.5 * a) * (1.0 - 0.2 * (1.0 - c))
        p_mult = 1.0 - 0.10 * (1.0 - c)
    else:
        return 1.0, 1.0

    # Clamp to safety bands so a malformed affect signal can't blow up sampling.
    t_mult = max(0.5, min(1.6, t_mult))
    p_mult = max(0.85, min(1.0, p_mult))
    return t_mult, p_mult


def build_llm_kwargs(
    phase: Phase | None,
    provider: str,
    affect_state: dict | None = None,
) -> dict:
    """Translate a phase + provider into crewai.LLM constructor kwargs.

    Returns an empty dict when phase is None so non-creative paths stay
    byte-identical to legacy behavior.

    Args:
        phase: one of "diverge", "discuss", "converge", or None.
        provider: "anthropic" | "openrouter" | "ollama" | "local".
                  (Anything else is treated as openrouter-compatible.)
        affect_state: optional snapshot {"valence","arousal","controllability"}.
                  When provided and phase is not None, biases temperature/top_p
                  per `_affect_modulation`. Default None preserves legacy behavior.
    """
    sampling = get_sampling_params(phase)
    if not sampling:
        return {}

    temperature = sampling["temperature"]
    top_p = sampling["top_p"]
    min_p = sampling["min_p"]
    presence_penalty = sampling["presence_penalty"]

    if affect_state and phase is not None:
        t_mult, p_mult = _affect_modulation(phase, affect_state)
        temperature = round(temperature * t_mult, 4)
        top_p = round(top_p * p_mult, 4)

    if provider == "anthropic":
        # Anthropic SDK supports temperature and top_p only.
        return {"temperature": temperature, "top_p": top_p}

    if provider in ("ollama", "local"):
        # litellm passes top-level kwargs; Ollama-specific options ride in
        # extra_body.options. See llm_factory._try_local for the base_url wiring.
        return {
            "temperature": temperature,
            "top_p": top_p,
            "extra_body": {"options": {"min_p": min_p, "top_p": top_p, "temperature": temperature}},
        }

    # openrouter (and unknown providers assumed OpenAI-compatible)
    return {
        "temperature": temperature,
        "top_p": top_p,
        "presence_penalty": presence_penalty,
    }


def sampling_cache_key(phase: Phase | None, provider: str) -> str:
    """Stable string for use in LLM cache keys. '' when phase is None.

    Must be deterministic so cache lookups hit; must differ across phases
    and providers so creative runs don't collide with each other or with
    non-creative runs.
    """
    if phase is None:
        return ""
    return f"{provider}:{phase}"
