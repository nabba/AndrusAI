# Error Fix Proposal: BadRequestError from Tool-Incompatible Models

## Problem
**Error:** `coding:BadRequestError` (16 occurrences - highest frequency error)  
**Root Cause:** The system attempts to use tools with models that don't support tool calling. Specifically, `registry.ollama.ai/library/codestral:22b` fails when tools are provided.

**Impact:** Tasks fail at runtime with 402/400 errors, wasting API calls and time.

## Solution
Add a runtime guard that detects tool-incompatible models and either:
- Switch to a default tool-capable model automatically
- Remove tools from the request with a warning

## Implementation
Create `model_tool_compatibility.py`:

```python
# Predefined models known to lack tool support
TOOL_INCOMPATIBLE_MODELS = {
    "codestral:22b",
    "codestral:latest",
    # Pattern: any model starting with "codestral" from Ollama
}

def is_tool_compatible(model: str) -> bool:
    """Check if a model supports tool/function calling."""
    model_lower = model.lower()
    # Check against known incompatible models
    for incompatible in TOOL_INCOMPATIBLE_MODELS:
        if incompatible in model_lower:
            return False
    # Assume compatibility unless explicitly blacklisted
    # Could be enhanced with allowlist or capability API
    return True

def get_fallback_model(model: str) -> str:
    """Return a tool-capable fallback model for incompatible ones."""
    if not is_tool_compatible(model):
        return "gpt-4o"  # Known tool-capable default
    return model
```

**Integrate** into the LLM execution path to validate model capability before including tools.

## Benefits
- Prevents 16+ recurring errors immediately
- Reduces wasted API calls (~$1.54/request cost)
- Clear diagnostic: logs when model switching occurs
- Minimal code change (~20 lines)

## Trade-offs
- Slight model quality change when fallback occurs (acceptable trade-off for reliability)
- Needs maintenance of compatibility blacklist (can be learned over time)
