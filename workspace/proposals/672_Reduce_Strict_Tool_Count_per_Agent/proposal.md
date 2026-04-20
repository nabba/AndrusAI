# Proposal #672: Reduce Strict Tool Count per Agent

**Type:** code  
**Created:** 2026-04-19T21:58:55.653760+00:00  

## Why this is useful

Diagnosis: The agent was assigned 47 tools marked as strict, exceeding the maximum allowed of 20 strict tools by the underlying API.

Fix: Update the agent configuration to ensure no more than 20 tools are enabled in strict mode. This can be done by either marking some tools as non-strict (if they do not require strict schema enforcement), reducing the total number of tools, or distributing tools across multiple specialized agents. The limit is imposed by the API to manage complexity and performance.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The agent was assigned 47 tools marked as strict, exceeding the maximum allowed of 20 strict tools by the underlying API.

Fix: Update the agent configuration to ensure no more than 20 tools are enabled in strict mode. This can be done by either marking some tools as non-strict (if they do not require strict schema enforcement), reducing the total number of tools, or distributing tools across multiple specialized agents. The limit is imposed by the API to manage complexity and performance.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 672` / `reject 672` via Signal.
