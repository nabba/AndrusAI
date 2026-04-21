# Proposal #745: skill_http_api_client_patterns

**Type:** skill  
**Created:** 2026-04-21T15:55:31.967473+00:00  

## Why this is useful

PROBLEM: The team lacks a dedicated HTTP client tool for making POST/PUT/DELETE requests, handling authentication headers, and interacting with REST APIs. web_fetch only retrieves content via GET requests. Tasks requiring API interaction (testing webhooks, posting to services, authenticated API calls, form submissions) will fail. SOLUTION: Create a skill documenting HTTP client patterns using Python's requests/httpx libraries within the code_executor, including authentication patterns (Bearer tokens, API keys, Basic auth), retry logic, and error handling for the coding crew to use.

## What will change

- Modifies `skills/skill_http_api_client_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/skill_http_api_client_patterns.md`

## Original description

PROBLEM: The team lacks a dedicated HTTP client tool for making POST/PUT/DELETE requests, handling authentication headers, and interacting with REST APIs. web_fetch only retrieves content via GET requests. Tasks requiring API interaction (testing webhooks, posting to services, authenticated API calls, form submissions) will fail. SOLUTION: Create a skill documenting HTTP client patterns using Python's requests/httpx libraries within the code_executor, including authentication patterns (Bearer tokens, API keys, Basic auth), retry logic, and error handling for the coding crew to use.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 745` / `reject 745` via Signal.
