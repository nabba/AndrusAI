# Proposal #660: New model: Devstral 2

**Type:** skill  
**Created:** 2026-04-19T04:10:18.745370+00:00  

## Why this is useful

Tech radar discovered: Devstral 2

Free 123B dense coding model from Mistral with agentic features and multi-file orchestration under MIT license.

Recommended action: Deploy for coding-heavy CrewAI agents requiring file orchestration.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Devstral 2

Free 123B dense coding model from Mistral with agentic features and multi-file orchestration under MIT license.

Recommended action: Deploy for coding-heavy CrewAI agents requiring file orchestration.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 660` / `reject 660` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.163623+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
