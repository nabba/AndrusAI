# Proposal #698: API Testing and HTTP Methods Skill

**Type:** skill  
**Created:** 2026-04-20T19:38:50.754286+00:00  

## Why this is useful

PROBLEM: The team has web_fetch for GET requests but cannot perform POST, PUT, DELETE, or authenticated API calls. This severely limits integration with external services, webhooks, REST APIs, and automation workflows. Tasks like 'create a GitHub issue via API', 'send data to a webhook', or 'interact with SaaS platforms' would fail. SOLUTION: A skill document teaching how to use Python's requests library within the code_executor to perform full HTTP operations including authentication (API keys, OAuth tokens, Basic Auth), custom headers, JSON payloads, and response handling.

## What will change

- Modifies `skills/api_testing_with_python_requests.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/api_testing_with_python_requests.md`

## Original description

PROBLEM: The team has web_fetch for GET requests but cannot perform POST, PUT, DELETE, or authenticated API calls. This severely limits integration with external services, webhooks, REST APIs, and automation workflows. Tasks like 'create a GitHub issue via API', 'send data to a webhook', or 'interact with SaaS platforms' would fail. SOLUTION: A skill document teaching how to use Python's requests library within the code_executor to perform full HTTP operations including authentication (API keys, OAuth tokens, Basic Auth), custom headers, JSON payloads, and response handling.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 698` / `reject 698` via Signal.
