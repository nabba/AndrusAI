# Proposal #695: llm_capability_graceful_degradation

**Type:** skill  
**Created:** 2026-04-20T18:55:07.746069+00:00  

## Why this is useful

PROBLEM: Memory logs show BadRequestError when using Codestral model with tools - 'codestral:22b does not support the use of tools'. This caused task failures. The existing llm_capability_detection skill doesn't prevent these errors or provide fallback mechanisms. SOLUTION: Create a comprehensive skill documenting LLM capability detection with graceful degradation patterns - auto-detecting tool support, falling back to alternative models or non-tool approaches, and logging capability mismatches for system improvement.

## What will change

- Modifies `skills/llm_capability_graceful_degradation.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/llm_capability_graceful_degradation.md`

## Original description

PROBLEM: Memory logs show BadRequestError when using Codestral model with tools - 'codestral:22b does not support the use of tools'. This caused task failures. The existing llm_capability_detection skill doesn't prevent these errors or provide fallback mechanisms. SOLUTION: Create a comprehensive skill documenting LLM capability detection with graceful degradation patterns - auto-detecting tool support, falling back to alternative models or non-tool approaches, and logging capability mismatches for system improvement.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 695` / `reject 695` via Signal.
