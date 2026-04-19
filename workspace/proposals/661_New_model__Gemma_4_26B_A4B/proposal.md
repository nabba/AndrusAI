# Proposal #661: New model: Gemma 4 26B A4B

**Type:** skill  
**Created:** 2026-04-19T04:10:24.533490+00:00  

## Why this is useful

Tech radar discovered: Gemma 4 26B A4B

Free Google MoE model with multimodal input (text/images/video), 256K context, native function calling.

Recommended action: Experiment with multimodal agent tasks in CrewAI.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Gemma 4 26B A4B

Free Google MoE model with multimodal input (text/images/video), 256K context, native function calling.

Recommended action: Experiment with multimodal agent tasks in CrewAI.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 661` / `reject 661` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.244014+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
