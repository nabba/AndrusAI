# Proposal #658: New model: NVIDIA Nemotron 3 Super

**Type:** skill  
**Created:** 2026-04-19T04:10:07.259165+00:00  

## Why this is useful

Tech radar discovered: NVIDIA Nemotron 3 Super

Free 120B hybrid Mamba-Transformer MoE model with 1M context, optimized for multi-agent applications and benchmarks like SWE-Bench Verified.

Recommended action: Test as cost-free backbone for production CrewAI multi-agent systems.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: NVIDIA Nemotron 3 Super

Free 120B hybrid Mamba-Transformer MoE model with 1M context, optimized for multi-agent applications and benchmarks like SWE-Bench Verified.

Recommended action: Test as cost-free backbone for production CrewAI multi-agent systems.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 658` / `reject 658` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.054832+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
