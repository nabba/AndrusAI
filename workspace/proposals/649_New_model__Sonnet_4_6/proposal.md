# Proposal #649: New model: Sonnet 4.6

**Type:** skill  
**Created:** 2026-04-19T01:13:26.280401+00:00  

## Why this is useful

Tech radar discovered: Sonnet 4.6

Anthropic's most capable Sonnet-class model with frontier performance in coding, agents, and professional work, accessible via OpenRouter.

Recommended action: Benchmark Sonnet 4.6 in CrewAI for agentic coding and complex multi-agent tasks.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Sonnet 4.6

Anthropic's most capable Sonnet-class model with frontier performance in coding, agents, and professional work, accessible via OpenRouter.

Recommended action: Benchmark Sonnet 4.6 in CrewAI for agentic coding and complex multi-agent tasks.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 649` / `reject 649` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.755607+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
