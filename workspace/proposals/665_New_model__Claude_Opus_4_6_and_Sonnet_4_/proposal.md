# Proposal #665: New model: Claude Opus 4.6 and Sonnet 4.6

**Type:** skill  
**Created:** 2026-04-19T13:14:13.875288+00:00  

## Why this is useful

Tech radar discovered: Claude Opus 4.6 and Sonnet 4.6

Anthropic's February 2026 releases: Opus 4.6 excels in task planning and multi-step workflows; Sonnet 4.6 offers balanced performance.

Recommended action: Evaluate Claude Opus 4.6 for complex CrewAI agent coordination and error reduction in long-running tasks

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Claude Opus 4.6 and Sonnet 4.6

Anthropic's February 2026 releases: Opus 4.6 excels in task planning and multi-step workflows; Sonnet 4.6 offers balanced performance.

Recommended action: Evaluate Claude Opus 4.6 for complex CrewAI agent coordination and error reduction in long-running tasks

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 665` / `reject 665` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.336390+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
