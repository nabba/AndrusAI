# Proposal #655: New model: GLM-5.1

**Type:** skill  
**Created:** 2026-04-19T03:43:42.575935+00:00  

## Why this is useful

Tech radar discovered: GLM-5.1

GLM-5.1 is a new open-source LLM released under MIT license that leads all open-source models on software engineering benchmarks like SWE-bench Pro and Terminal Bench.

Recommended action: Evaluate GLM-5.1 for integration into CrewAI agents to boost software engineering and coding task performance.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: GLM-5.1

GLM-5.1 is a new open-source LLM released under MIT license that leads all open-source models on software engineering benchmarks like SWE-bench Pro and Terminal Bench.

Recommended action: Evaluate GLM-5.1 for integration into CrewAI agents to boost software engineering and coding task performance.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 655` / `reject 655` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.874429+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
