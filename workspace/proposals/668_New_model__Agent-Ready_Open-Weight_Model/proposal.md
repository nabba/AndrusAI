# Proposal #668: New model: Agent-Ready Open-Weight Models

**Type:** skill  
**Created:** 2026-04-19T13:14:31.563703+00:00  

## Why this is useful

Tech radar discovered: Agent-Ready Open-Weight Models

2026 trend of open-weight models natively trained for agent use with built-in tool use, structured outputs, and long-context reasoning.

Recommended action: Prioritize these models in CrewAI model selection for enhanced tool-calling and autonomous workflows

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Agent-Ready Open-Weight Models

2026 trend of open-weight models natively trained for agent use with built-in tool use, structured outputs, and long-context reasoning.

Recommended action: Prioritize these models in CrewAI model selection for enhanced tool-calling and autonomous workflows

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 668` / `reject 668` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.507464+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
