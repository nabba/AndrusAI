# Proposal #726: REST API Integration Skill for External Services

**Type:** skill  
**Created:** 2026-04-21T10:45:43.680321+00:00  

## Why this is useful

PROBLEM: Agents lack structured guidance on integrating with external APIs. While code_executor can run Python requests, there's no standardized pattern for authentication, error handling, rate limiting, or response parsing. This leads to fragile, inconsistent API integrations. SOLUTION: Create a comprehensive skill document teaching best practices for REST API integration including auth patterns, retry logic, pagination handling, and response validation.

## What will change

- Modifies `skills/rest_api_integration_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/rest_api_integration_patterns.md`

## Original description

PROBLEM: Agents lack structured guidance on integrating with external APIs. While code_executor can run Python requests, there's no standardized pattern for authentication, error handling, rate limiting, or response parsing. This leads to fragile, inconsistent API integrations. SOLUTION: Create a comprehensive skill document teaching best practices for REST API integration including auth patterns, retry logic, pagination handling, and response validation.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 726` / `reject 726` via Signal.
