# Proposal #644: New model: Meta Llama 4 Scout

**Type:** skill  
**Created:** 2026-04-18T23:29:51.331253+00:00  

## Why this is useful

Tech radar discovered: Meta Llama 4 Scout

Open-weight model with industry-leading 10 million token context window, ideal for massive-scale data processing in agents.

Recommended action: Experiment with long-context tasks in CrewAI to leverage extended memory capabilities.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Meta Llama 4 Scout

Open-weight model with industry-leading 10 million token context window, ideal for massive-scale data processing in agents.

Recommended action: Experiment with long-context tasks in CrewAI to leverage extended memory capabilities.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 644` / `reject 644` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.641132+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
