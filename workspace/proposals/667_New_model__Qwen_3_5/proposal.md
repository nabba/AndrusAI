# Proposal #667: New model: Qwen 3.5

**Type:** skill  
**Created:** 2026-04-19T13:14:25.372245+00:00  

## Why this is useful

Tech radar discovered: Qwen 3.5

Alibaba's open-weight model released February 2026, featured in top AI model lists.

Recommended action: Assess Qwen 3.5 as cost-effective open-weight alternative for agent tool-calling and multilingual tasks

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Qwen 3.5

Alibaba's open-weight model released February 2026, featured in top AI model lists.

Recommended action: Assess Qwen 3.5 as cost-effective open-weight alternative for agent tool-calling and multilingual tasks

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 667` / `reject 667` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.418659+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
