# Proposal #677: Limit Strict Tools per Task

**Type:** code  
**Created:** 2026-04-20T05:44:03.339495+00:00  

## Why this is useful

Diagnosis: The API rejected the request because 41 strict tools were sent, exceeding the allowed maximum of 20, causing a 400 BadRequestError during task execution.

Fix: Reduce the number of tools marked as strict in agent or crew configuration to 20 or fewer. This can be achieved by dynamically assigning only relevant tools to each task or by adjusting tool definitions to set strict=False where possible, as the API enforces a hard limit on strict tools per request.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The API rejected the request because 41 strict tools were sent, exceeding the allowed maximum of 20, causing a 400 BadRequestError during task execution.

Fix: Reduce the number of tools marked as strict in agent or crew configuration to 20 or fewer. This can be achieved by dynamically assigning only relevant tools to each task or by adjusting tool definitions to set strict=False where possible, as the API enforces a hard limit on strict tools per request.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 677` / `reject 677` via Signal.
