# Proposal #666: New model: Grok 4.20

**Type:** skill  
**Created:** 2026-04-19T13:14:19.594563+00:00  

## Why this is useful

Tech radar discovered: Grok 4.20

xAI's proprietary model released February 17, 2026, competing in 2026 top model rankings.

Recommended action: Test Grok 4.20 for creative reasoning and real-time agent interactions if accessible via OpenRouter

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Grok 4.20

xAI's proprietary model released February 17, 2026, competing in 2026 top model rankings.

Recommended action: Test Grok 4.20 for creative reasoning and real-time agent interactions if accessible via OpenRouter

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 666` / `reject 666` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.345956+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
