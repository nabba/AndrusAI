# Proposal #641: Cap Strict Tools in API Requests

**Type:** code  
**Created:** 2026-04-18T22:56:38.682028+00:00  

## Why this is useful

Diagnosis: The AI agent system is attempting to pass 49 tools marked as strict in a single API request, exceeding the service's maximum limit of 20, which causes a 400 invalid_request_error during task execution.

Fix: Modify the tool handling logic in the agent or crew framework to limit the number of strict tools sent per request to 20 or fewer, either by filtering non-essential tools or by reclassifying some tools as non-strict in their definitions, ensuring compliance with API constraints.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The AI agent system is attempting to pass 49 tools marked as strict in a single API request, exceeding the service's maximum limit of 20, which causes a 400 invalid_request_error during task execution.

Fix: Modify the tool handling logic in the agent or crew framework to limit the number of strict tools sent per request to 20 or fewer, either by filtering non-essential tools or by reclassifying some tools as non-strict in their definitions, ensuring compliance with API constraints.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 641` / `reject 641` via Signal.
