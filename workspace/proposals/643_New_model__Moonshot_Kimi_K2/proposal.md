# Proposal #643: New model: Moonshot Kimi K2

**Type:** skill  
**Created:** 2026-04-18T23:29:45.573003+00:00  

## Why this is useful

Tech radar discovered: Moonshot Kimi K2

Trillion-parameter MoE model topping benchmarks like ECI, positioning as a top competitor from China for advanced reasoning.

Recommended action: Test for high-complexity agent reasoning workflows via OpenRouter.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Moonshot Kimi K2

Trillion-parameter MoE model topping benchmarks like ECI, positioning as a top competitor from China for advanced reasoning.

Recommended action: Test for high-complexity agent reasoning workflows via OpenRouter.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 643` / `reject 643` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:13.626810+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

Stub planted: `openrouter/moonshotai/kimi-k2-0905` — awaiting enrichment by the next OpenRouter discovery cycle.
