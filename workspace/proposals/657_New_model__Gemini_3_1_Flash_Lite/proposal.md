# Proposal #657: New model: Gemini 3.1 Flash Lite

**Type:** skill  
**Created:** 2026-04-19T04:10:01.136518+00:00  

## Why this is useful

Tech radar discovered: Gemini 3.1 Flash Lite

Google's fastest model yet at low cost ($0.25/$1.50), suitable for high-volume agent applications.

Recommended action: Evaluate for real-time, low-latency tasks in CrewAI agents.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Gemini 3.1 Flash Lite

Google's fastest model yet at low cost ($0.25/$1.50), suitable for high-volume agent applications.

Recommended action: Evaluate for real-time, low-latency tasks in CrewAI agents.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 657` / `reject 657` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.973505+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
