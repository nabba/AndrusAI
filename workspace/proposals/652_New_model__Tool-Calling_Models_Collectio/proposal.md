# Proposal #652: New model: Tool-Calling Models Collection (April 2026)

**Type:** skill  
**Created:** 2026-04-19T01:47:19.207713+00:00  

## Why this is useful

Tech radar discovered: Tool-Calling Models Collection (April 2026)

OpenRouter updated tool-calling model rankings (April 2026) based on real usage data. Tool calling enables LLMs to suggest and execute external tools, critical for agentic workflows.

Recommended action: Prioritize integration of top-ranked tool-calling models into CrewAI; use real usage data from OpenRouter to select optimal models for agent tool invocation tasks.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Tool-Calling Models Collection (April 2026)

OpenRouter updated tool-calling model rankings (April 2026) based on real usage data. Tool calling enables LLMs to suggest and execute external tools, critical for agentic workflows.

Recommended action: Prioritize integration of top-ranked tool-calling models into CrewAI; use real usage data from OpenRouter to select optimal models for agent tool invocation tasks.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 652` / `reject 652` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.860872+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
