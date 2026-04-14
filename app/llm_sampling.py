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


def build_llm_kwargs(
    phase: Phase | None,
    provider: str,
) -> dict:
    """Translate a phase + provider into crewai.LLM constructor kwargs.

    Returns an empty dict when phase is None so non-creative paths stay
    byte-identical to legacy behavior.

    Args:
        phase: one of "diverge", "discuss", "converge", or None.
        provider: "anthropic" | "openrouter" | "ollama" | "local".
                  (Anything else is treated as openrouter-compatible.)
    """
    sampling = get_sampling_params(phase)
    if not sampling:
        return {}

    temperature = sampling["temperature"]
    top_p = sampling["top_p"]
    min_p = sampling["min_p"]
    presence_penalty = sampling["presence_penalty"]

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
