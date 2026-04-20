# Proposal #679: Tool Schema Complexity Limit

**Type:** code  
**Created:** 2026-04-20T05:59:25.868886+00:00  

## Why this is useful

Diagnosis: The schema compilation fails because the combined tool schemas are too complex when all available tools are invoked for a single research task.

Fix: Implement a tool selection or filtering mechanism in the agent's task execution logic to use only a necessary subset of tools per task, rather than all available tools. This reduces the schema compilation complexity and prevents the error.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The schema compilation fails because the combined tool schemas are too complex when all available tools are invoked for a single research task.

Fix: Implement a tool selection or filtering mechanism in the agent's task execution logic to use only a necessary subset of tools per task, rather than all available tools. This reduces the schema compilation complexity and prevents the error.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 679` / `reject 679` via Signal.
