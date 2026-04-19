# Proposal #659: New model: Qwen3-Next 80B

**Type:** skill  
**Created:** 2026-04-19T04:10:12.980870+00:00  

## Why this is useful

Tech radar discovered: Qwen3-Next 80B

Free MoE model optimized for RAG, tool use, and agentic workflows with 262K context.

Recommended action: Integrate for tool-equipped agents in CrewAI to boost RAG and tool-calling performance.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Qwen3-Next 80B

Free MoE model optimized for RAG, tool use, and agentic workflows with 262K context.

Recommended action: Integrate for tool-equipped agents in CrewAI to boost RAG and tool-calling performance.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 659` / `reject 659` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.107231+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
