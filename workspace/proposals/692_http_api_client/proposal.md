# Proposal #692: http_api_client

**Type:** skill  
**Created:** 2026-04-20T18:20:29.880700+00:00  

## Why this is useful

Problem: The team lacks the ability to make arbitrary HTTP requests to REST APIs. Current web_fetch only retrieves web pages but cannot send POST/PUT/DELETE requests, set custom headers, or send JSON payloads. This fails tasks like 'call the GitHub API to create an issue' or 'POST data to a webhook'. Solution: A skill document teaching agents how to use the code_executor to make HTTP requests with Python's requests library, including authentication patterns, error handling, and common API workflows.

## What will change

- Modifies `skills/http_api_client.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/http_api_client.md`

## Original description

Problem: The team lacks the ability to make arbitrary HTTP requests to REST APIs. Current web_fetch only retrieves web pages but cannot send POST/PUT/DELETE requests, set custom headers, or send JSON payloads. This fails tasks like 'call the GitHub API to create an issue' or 'POST data to a webhook'. Solution: A skill document teaching agents how to use the code_executor to make HTTP requests with Python's requests library, including authentication patterns, error handling, and common API workflows.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 692` / `reject 692` via Signal.
