# Proposal #678: Reduce tool schema complexity for agent

**Type:** code  
**Created:** 2026-04-20T05:55:36.608677+00:00  

## Why this is useful

Diagnosis: The LLM API returns a 400 error because the combined tool schemas passed to the model exceed the provider's complexity limit, typically caused by including too many tools or overly detailed parameter definitions in a single request.

Fix: Modify the agent configuration for the research task to limit the number of tools (e.g., only include essential tools like web search and one data lookup tool) and simplify tool schemas by removing optional parameters, flattening nested objects, or omitting rarely used fields. This ensures the tool compilation fits within the LLM's constraints and prevents the 'Schema is too complex' error.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The LLM API returns a 400 error because the combined tool schemas passed to the model exceed the provider's complexity limit, typically caused by including too many tools or overly detailed parameter definitions in a single request.

Fix: Modify the agent configuration for the research task to limit the number of tools (e.g., only include essential tools like web search and one data lookup tool) and simplify tool schemas by removing optional parameters, flattening nested objects, or omitting rarely used fields. This ensures the tool compilation fits within the LLM's constraints and prevents the 'Schema is too complex' error.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 678` / `reject 678` via Signal.
