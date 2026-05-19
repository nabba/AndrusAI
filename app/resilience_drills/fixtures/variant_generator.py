"""variant_generator — produces injection variants for task_recovery.

The drill picks one variant per failure class per run. By default
(``drill_task_recovery_llm_variants_enabled = True``) variants come
from a cheap-tier LLM call so the injected payload differs each run
— this is the anti-Goodhart layer. If the LLM call fails or the
output doesn't validate, we fall back to the curated pool in
:mod:`app.resilience_drills.fixtures.task_recovery_crew`.

The LLM is instructed to produce JSON conforming to one of four
strict schemas (one per failure class). The output is validated
structurally before use — no LLM string ever reaches the drill
fixture without passing the schema check below.

LLM routing
-----------

We route through :func:`app.llm_factory.create_specialist_llm` with
``force_tier="budget"`` rather than hitting Anthropic directly. This
gets us, for free:

* The cascade — local Ollama / OpenRouter cheap tier / cloud
  fallback in the order the operator has configured.
* Runtime-mode awareness (free / budget / balanced / quality /
  insane / anthropic). A "free" deployment never hits a paid API
  for variant generation.
* The ``chat_blocked_models`` runtime_settings list — if Anthropic
  is temporarily blocked, the selector routes around it.
* Cost telemetry through the standard ``agent_scope`` wrappers in
  ``llm_factory`` (cost attribution lands in /cp/costs).
* Pre-call budget enforcement.

Cost
----

One call per class per drill run, ~150 input tokens + ~80 output
tokens at the catalog's current budget-tier pricing ≈ $0.0001/call
× 4 = $0.0004/drill on the variant side. Combined with the crew
kickoff cost (the dominant term), the per-drill total stays well
under the $0.10 cap.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.resilience_drills.fixtures.task_recovery_crew import fallback_variant

logger = logging.getLogger(__name__)


# ── Schemas (one per class) ──────────────────────────────────────────────


_VALID_SHAPES = {"value_as_string", "whole_payload_string"}
_VALID_FIELDS = {"value", "label"}
_VALID_KINDS = {"nan", "inf", "neg_inf"}


def _validate_variant(failure_class: str, candidate: Any) -> dict[str, Any] | None:
    """Return ``candidate`` if it matches the class schema, else None.

    Strict: every required key present, every value of the expected
    type/enum, no surplus keys.
    """
    if not isinstance(candidate, dict):
        return None

    if failure_class == "type_mismatch":
        shape = candidate.get("shape")
        if shape not in _VALID_SHAPES:
            return None
        out: dict[str, Any] = {"shape": shape}
        if shape == "value_as_string":
            wrong_value = candidate.get("wrong_value", "forty-two")
            if not isinstance(wrong_value, str) or not wrong_value:
                return None
            if len(wrong_value) > 50:
                return None
            out["wrong_value"] = wrong_value
        return out

    if failure_class == "missing_field":
        field = candidate.get("field")
        if field not in _VALID_FIELDS:
            return None
        return {"field": field}

    if failure_class == "numerical_anomaly":
        kind = candidate.get("kind")
        if kind not in _VALID_KINDS:
            return None
        return {"kind": kind}

    if failure_class == "transient_timeout":
        fail_until = candidate.get("fail_until_attempt", 1)
        if not isinstance(fail_until, int) or not 1 <= fail_until <= 3:
            return None
        msg = candidate.get("message", "drill_lookup timed out")
        if not isinstance(msg, str) or len(msg) > 200:
            return None
        return {"fail_until_attempt": fail_until, "message": msg}

    return None


# ── LLM prompt + call ────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You generate ONE small JSON object that specifies HOW a synthetic "
    "drill tool should misbehave during a resilience test. Output ONLY "
    "the JSON object — no prose, no code fences, no comments. The drill "
    "machinery validates your output against a strict schema; surplus "
    "keys, missing keys, or out-of-enum values will be rejected and a "
    "curated fallback will be used instead. Stay inside the schema."
)


_USER_PROMPT_TEMPLATES = {
    "type_mismatch": (
        "Failure class: type_mismatch.\n"
        "Schema: {\"shape\": \"value_as_string\"|\"whole_payload_string\", "
        "\"wrong_value\"?: string (≤50 chars, required iff shape=value_as_string)}\n"
        "Pick ONE shape. If value_as_string, invent a plausible-looking "
        "string that an LLM might confuse with the integer 42 (e.g. "
        "spelled-out, hex, encoded). Output one object."
    ),
    "missing_field": (
        "Failure class: missing_field.\n"
        "Schema: {\"field\": \"value\"|\"label\"}\n"
        "Choose which field to elide from the tool's return shape. Both "
        "are plausible drops. Output one object."
    ),
    "numerical_anomaly": (
        "Failure class: numerical_anomaly.\n"
        "Schema: {\"kind\": \"nan\"|\"inf\"|\"neg_inf\"}\n"
        "Pick one numeric pathology. Output one object."
    ),
    "transient_timeout": (
        "Failure class: transient_timeout.\n"
        "Schema: {\"fail_until_attempt\": int 1..3, "
        "\"message\"?: string (≤200 chars)}\n"
        "Pick how many initial attempts to fail before the tool "
        "recovers, and a plausible timeout error message. Output "
        "one object."
    ),
}


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> Any | None:
    """Strip code fences / surrounding prose; parse the first JSON
    object found. Returns the parsed value or None."""
    if not raw:
        return None
    # Strip ```...``` fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        m = _JSON_OBJECT_RE.search(raw)
        if not m:
            return None
        candidate = m.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _llm_generate(failure_class: str) -> dict[str, Any] | None:
    """One budget-tier LLM call via the project's standard cascade.
    Returns a validated variant dict or None on any failure
    (network, parse, schema validation).

    All exception paths are swallowed — the caller falls back to the
    curated pool. The drill must never fail because variant
    generation failed.

    The selector picks the budget tier explicitly via
    ``force_tier="budget"``. Under "free" runtime mode this resolves
    to a local/free model; under any cost mode it picks the
    cheapest acceptable cloud option from the catalog.
    """
    user_prompt = _USER_PROMPT_TEMPLATES.get(failure_class)
    if user_prompt is None:
        return None

    try:
        from app.llm_factory import create_specialist_llm
    except Exception:
        return None

    try:
        llm = create_specialist_llm(
            max_tokens=256,
            role="drill_variant",
            task_hint=(
                "Generate one small JSON injection variant for a "
                "resilience drill. Strict schema validation downstream."
            ),
            force_tier="budget",
        )
    except Exception:
        logger.debug("variant_generator: LLM construction failed for %s",
                     failure_class, exc_info=True)
        return None

    # crewai LLM convention: concatenate system + user with a
    # blank line separator, then ``.call(prompt)``.
    full_prompt = f"{_SYSTEM_PROMPT}\n\n{user_prompt}"
    try:
        raw = str(llm.call(full_prompt) or "").strip()
    except Exception:
        logger.debug("variant_generator: LLM call failed for %s",
                     failure_class, exc_info=True)
        return None

    parsed = _extract_json(raw)
    if parsed is None:
        logger.debug("variant_generator: unparseable LLM output for %s: %r",
                     failure_class, raw[:120])
        return None

    validated = _validate_variant(failure_class, parsed)
    if validated is None:
        logger.debug("variant_generator: variant failed schema for %s: %r",
                     failure_class, parsed)
        return None

    return validated


# ── Public API ───────────────────────────────────────────────────────────


def generate_variant(
    failure_class: str,
    *,
    use_llm: bool = True,
    fallback_seed: int | None = None,
) -> tuple[dict[str, Any], str]:
    """Produce a variant for ``failure_class``.

    Returns ``(variant_dict, source)`` where ``source`` is either
    ``"llm"`` (LLM generated + validated) or ``"fallback"`` (curated
    pool). The drill audit records which source was used so an
    operator inspecting a regression can tell whether the LLM was in
    the loop.
    """
    if use_llm:
        v = _llm_generate(failure_class)
        if v is not None:
            return v, "llm"
    return fallback_variant(failure_class, seed=fallback_seed), "fallback"


__all__ = ["generate_variant", "_validate_variant"]
