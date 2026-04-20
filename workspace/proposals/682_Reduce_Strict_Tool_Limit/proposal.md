# Proposal #682: Reduce Strict Tool Limit

**Type:** code  
**Created:** 2026-04-20T06:27:45.661904+00:00  

## Why this is useful

Diagnosis: The agent system attempted to load 25 tools as 'strict', exceeding the provider's limit of 20 strict tools per request.

Fix: Modify the agent configuration to ensure no more than 20 tools are marked as 'strict' in any single execution. This may involve: 1) Reviewing the tool list assigned to the agent and removing non-critical tools from the strict set, 2) Reclassifying some tools as non-strict if they can operate without strict enforcement, or 3) Splitting the agent's toolkit across multiple specialized agents. The limit is enforced by the LLM provider's API and cannot be overridden.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The agent system attempted to load 25 tools as 'strict', exceeding the provider's limit of 20 strict tools per request.

Fix: Modify the agent configuration to ensure no more than 20 tools are marked as 'strict' in any single execution. This may involve: 1) Reviewing the tool list assigned to the agent and removing non-critical tools from the strict set, 2) Reclassifying some tools as non-strict if they can operate without strict enforcement, or 3) Splitting the agent's toolkit across multiple specialized agents. The limit is enforced by the LLM provider's API and cannot be overridden.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 682` / `reject 682` via Signal.
