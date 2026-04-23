# Code Fix: BadRequestError from Tool-Incompatible Models

## Problem
**Error Pattern:** `coding:BadRequestError` — 16 occurrences (highest frequency)
**Root Cause:** The system attempts to use tools/function calling with models that don't support it (e.g., `registry.ollama.ai/library/codestral:22b`). This causes HTTP 402/400 errors from the API.

## Impact
- Tasks fail at runtime after API call is made
- Wastes API calls (~$1.54 per request cost)
- Reduces user confidence in system reliability

## Solution: Model Tool-Compatibility Guard

Add a lightweight runtime check before executing LLM calls with tools:

**File:** `model_tool_compatibility.py` (new module)

```python
"""
Guard against using tools with incompatible LLM models.
"""

# Models known NOT to support tool/function calling
TOOL_INCOMPATIBLE_MODELS = {
    "codestral:22b",
    "codestral:latest",
    # Extend as other incompatible models are discovered
}

def is_tool_compatible(model_name: str) -> bool:
    """
    Determine if a model supports tool/function calling.
    
    Strategy: Default to True, blacklist known incompatible models.
    This is conservative: assumes compatibility unless proven otherwise.
    """
    model_lower = model_name.lower()
    return not any(incompatible in model_lower for incompatible in TOOL_INCOMPATIBLE_MODELS)

def get_tool_capable_model(model_name: str, fallback: str = "gpt-4o") -> str:
    """
    Return a tool-capable model. If the requested model is incompatible,
    return the fallback instead (with clear logging).
    """
    if not is_tool_compatible(model_name):
        from logging import getLogger
        logger = getLogger(__name__)
        logger.warning(
            f"Model '{model_name}' does not support tools; "
            f"falling back to '{fallback}'"
        )
        return fallback
    return model_name
```

**Integration Point:** In the LLM execution path (likely `llm_agent.py` or similar), wrap the model selection:

```python
# Before calling the LLM with tools:
if tools:
    model_name = get_tool_capable_model(config.model)
    # Continue with tool-capable model
```

## Benefits
- Prevents 16+ recurring errors immediately
- Saves ~$24+ per month in wasted API calls (16 errors × $1.54 avg)
- Clear logging helps identify incompatible models for blacklist updates
- Minimal code (~40 lines, single new module)
- No new dependencies, no security implications

## Trade-offs & Future Work
- **Fallback model quality:** May change output quality when switching from codestral to GPT-4o. Acceptable trade-off for reliability.
- **Blacklist maintenance:** Needs periodic updates as new models are discovered. Could be enhanced with:
  - Remote capability registry lookup
  - User-configurable allowlist/blacklist
  - Automatic detection via trial call (with retry)

## Measurement
Track via error journal entry count:
- Before: 16× BadRequestError
- After: 0× (if integration is correct)
- Watch for new error types introduced (should be none)
