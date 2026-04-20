# Proposal #674: Add Error Recovery and Retry Patterns Skill

**Type:** skill  
**Created:** 2026-04-19T23:35:56.470935+00:00  

## Why this is useful

PROBLEM: Team memory shows recurring issues: slow research tasks (98-165s), model capability mismatches (BadRequestError with codestral), and failed future-oriented queries. The team lacks systematic error recovery and retry patterns. SOLUTION: Add a skill that codifies retry strategies, timeout handling, graceful degradation patterns, and the lessons already learned from past failures into a reusable reference.

## What will change

- Modifies `skills/error_recovery_and_retry_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/error_recovery_and_retry_patterns.md`

## Original description

PROBLEM: Team memory shows recurring issues: slow research tasks (98-165s), model capability mismatches (BadRequestError with codestral), and failed future-oriented queries. The team lacks systematic error recovery and retry patterns. SOLUTION: Add a skill that codifies retry strategies, timeout handling, graceful degradation patterns, and the lessons already learned from past failures into a reusable reference.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 674` / `reject 674` via Signal.
