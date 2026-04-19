# Proposal #642: New model: Gemini 3.1 Pro with Adaptive Reasoning

**Type:** skill  
**Created:** 2026-04-18T23:29:39.813262+00:00  

## Why this is useful

Tech radar discovered: Gemini 3.1 Pro with Adaptive Reasoning

February 2026 release featuring dynamic thinking levels that adjust reasoning effort based on prompt complexity for efficient multi-step planning.

Recommended action: Benchmark against current models for agent planning tasks and adopt if superior in cost-speed balance.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Gemini 3.1 Pro with Adaptive Reasoning

February 2026 release featuring dynamic thinking levels that adjust reasoning effort based on prompt complexity for efficient multi-step planning.

Recommended action: Benchmark against current models for agent planning tasks and adopt if superior in cost-speed balance.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 642` / `reject 642` via Signal.

---
## Migration note

Closed 2026-04-19T20:37:25.073530+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
