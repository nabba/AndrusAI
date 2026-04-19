# Proposal #648: New model: NVIDIA-Nemotron-Nano-9B-v2 Free Model

**Type:** skill  
**Created:** 2026-04-19T00:41:59.150832+00:00  

## Why this is useful

Tech radar discovered: NVIDIA-Nemotron-Nano-9B-v2 Free Model

NVIDIA released Nemotron-Nano-9B-v2 as a free model on OpenRouter, designed as a unified model for both reasoning and non-reasoning tasks with lower computational requirements.[1]

Recommended action: Test Nemotron-Nano-9B-v2 as a lightweight backbone model for CrewAI agents requiring rapid inference or deployment on resource-constrained environments.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: NVIDIA-Nemotron-Nano-9B-v2 Free Model

NVIDIA released Nemotron-Nano-9B-v2 as a free model on OpenRouter, designed as a unified model for both reasoning and non-reasoning tasks with lower computational requirements.[1]

Recommended action: Test Nemotron-Nano-9B-v2 as a lightweight backbone model for CrewAI agents requiring rapid inference or deployment on resource-constrained environments.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 648` / `reject 648` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.720146+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

Stub planted: `openrouter/nvidia/nemotron-nano-9b-v2` — awaiting enrichment by the next OpenRouter discovery cycle.
