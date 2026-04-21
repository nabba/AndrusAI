# Proposal #694: rest_api_client_tool

**Type:** skill  
**Created:** 2026-04-20T18:55:02.032355+00:00  

## Why this is useful

PROBLEM: Team has web_fetch for basic HTTP GET requests but lacks robust API testing capabilities. Cannot easily test REST endpoints, handle authentication (OAuth, API keys), manage rate limits, or construct complex API requests. Tasks involving API integration require manual code execution workarounds. SOLUTION: Document a comprehensive REST API client workflow using MCP servers (Postman) or code_executor with requests/httpx libraries, covering authentication patterns, pagination handling, and error recovery.

## What will change

- Modifies `skills/rest_api_client_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/rest_api_client_patterns.md`

## Original description

PROBLEM: Team has web_fetch for basic HTTP GET requests but lacks robust API testing capabilities. Cannot easily test REST endpoints, handle authentication (OAuth, API keys), manage rate limits, or construct complex API requests. Tasks involving API integration require manual code execution workarounds. SOLUTION: Document a comprehensive REST API client workflow using MCP servers (Postman) or code_executor with requests/httpx libraries, covering authentication patterns, pagination handling, and error recovery.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 694` / `reject 694` via Signal.
