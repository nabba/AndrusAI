"""
schema_transforms.py — Registry of tool-schema post-processors.

Motivation
----------
Different LLM providers enforce subtly different rules on the JSON
Schema that describes a tool's ``parameters``.  Examples we hit in
practice:

* Anthropic's tool_use rejects more than ~20 *strict* tools per request,
  so we flip ``strict: True`` → ``strict: False``.
* Azure (reached via OpenRouter) runs an OpenAI-style strict validator
  even when ``strict: False`` and rejects the combination
  "property listed in ``required`` but has a ``default`` value", so we
  drop defaulted fields from ``required``.
* Gemini will, in future versions, reject ``additionalProperties: false``
  on nested objects; Bedrock flattens nested schemas; and so on.

Historically we had *one* hook location in ``crews/base_crew.py`` that
inlined both Anthropic and Azure fixes in a single function.  That works
but doesn't scale: each new provider quirk means editing one function,
and the intent (why each line is there) blurs over time.

This module is the single point of truth for "what mutations do we apply
to every tool schema before it leaves the process".  Each transform:

* Has a short descriptive name (used in startup logs)
* Operates on the **function object** (the ``{name, description,
  parameters, strict}`` dict that CrewAI emits)
* Is idempotent (applying it twice is the same as applying it once —
  required because CrewAI re-emits schemas across retries)
* Never throws on well-formed input; errors are logged and swallowed
  so a single buggy transform can't wedge the whole tool palette

Usage
-----
::

    from app.observability import schema_transforms as st

    def my_transform(fn: dict) -> None:
        ...mutate in place...

    st.register(my_transform, name="my_transform")

    # Later, in the tool-schema emission path:
    for s in schemas:
        st.apply_to_function_schema(s.get("function", {}))
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# Ordered list: transforms run in registration order.  Callers that care
# about ordering (e.g. "fix required list AFTER strict bool is flipped")
# should register in the right sequence.
_transforms: list[tuple[str, Callable[[dict], None]]] = []


def register(transform: Callable[[dict], None], *, name: str) -> None:
    """Register a function-schema transform.

    Idempotent: if ``name`` is already registered, this replaces the
    existing entry (caller-friendly for hot-reload / install-on-restart
    scenarios; the module re-imports itself cleanly).
    """
    for i, (existing_name, _) in enumerate(_transforms):
        if existing_name == name:
            _transforms[i] = (name, transform)
            logger.debug("schema_transforms: replaced existing '%s'", name)
            return
    _transforms.append((name, transform))
    logger.info("schema_transforms: registered '%s' (total=%d)",
                name, len(_transforms))


def apply_to_function_schema(fn: dict) -> None:
    """Apply every registered transform to a single function-schema dict.

    ``fn`` is the OpenAI-format ``{"name": ..., "description": ...,
    "parameters": ..., "strict": ...}`` object.  Mutations are in place.
    """
    if not isinstance(fn, dict):
        return
    for name, t in _transforms:
        try:
            t(fn)
        except Exception:
            logger.debug("schema_transforms: transform '%s' raised",
                         name, exc_info=True)


def registered_names() -> list[str]:
    """Observability helper: names of all registered transforms, in
    execution order.  Useful for startup diagnostics and tests."""
    return [name for name, _ in _transforms]


# ── Built-in transforms ─────────────────────────────────────────────
#
# These live here (rather than in the consumer module that owns the
# symptom) because they represent cross-provider correctness rules, not
# CrewAI-framework-internal mechanics.  Registration is triggered by
# ``crews/base_crew.py::_patch_crewai_strict_false`` on startup so the
# registry only carries entries that have been explicitly wired in —
# keeps test isolation clean.


def flip_strict_to_false(fn: dict) -> None:
    """Anthropic's 20-strict-tool limit is the hardest provider cap we
    hit; emitting ``strict: False`` lifts it and lets Anthropic fall
    back to its more generous schema-complexity budget instead.

    CrewAI hard-codes ``strict: True`` on every tool (for OpenAI
    strict-mode structured-outputs support), so we flip it back.  Any
    OpenAI caller that actually needed strict mode would have to opt
    back in, but none of ours do.
    """
    if fn.get("strict") is True:
        fn["strict"] = False


def drop_defaulted_fields_from_required(fn: dict) -> None:
    """When ``strict: False``, OpenAI-compat validators (notably Azure,
    which OpenRouter can route Claude through) reject the combination
    "property listed in ``required`` but has a ``default`` value"
    because a field with a default is semantically optional.

    CrewAI's strict-mode transform force-lists every property as required
    (to satisfy strict mode's "all properties must be required" rule),
    leaving ``default`` set on optional ones — the combination is
    internally inconsistent for non-strict callers.  Restore the
    JSON Schema semantic by removing any property with a ``default``
    from ``required``.  Recurses into nested object schemas.
    """
    params = fn.get("parameters")
    if isinstance(params, dict):
        _prune_required(params)


def _prune_required(schema: dict) -> None:
    """Recursive helper for :func:`drop_defaulted_fields_from_required`."""
    required = schema.get("required")
    properties = schema.get("properties")
    if isinstance(required, list) and isinstance(properties, dict):
        cleaned = [
            name for name in required
            if not (
                isinstance(properties.get(name), dict)
                and "default" in properties[name]
            )
        ]
        if cleaned != required:
            schema["required"] = cleaned

    if isinstance(properties, dict):
        for child in properties.values():
            if isinstance(child, dict) and child.get("type") == "object":
                _prune_required(child)
            elif isinstance(child, dict) and isinstance(child.get("items"), dict):
                items = child["items"]
                if items.get("type") == "object":
                    _prune_required(items)


def install_default_transforms() -> None:
    """Register the two built-in transforms in the correct order.

    Order matters: flipping the strict bool first keeps the state
    machine obvious (non-strict mode → apply non-strict rules).
    """
    register(flip_strict_to_false,
             name="flip_strict_to_false")
    register(drop_defaulted_fields_from_required,
             name="drop_defaulted_fields_from_required")
