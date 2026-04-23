# Proposal #761: Slack Notifications for Team Coordination

**Type:** code  
**Created:** 2026-04-22T19:30:17.233614+00:00  

## Why this is useful

Problem: Team operates in isolation with no real-time coordination or stakeholder updates. Research completions, code failures, and important findings are not communicated. Solution: Add Slack MCP server to send notifications, create alerts, and enable cross-crew communication. This improves situational awareness and enables human-in-the-loop oversight.

## What will change

- Modifies `skills/slack_integration_for_team_awareness.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `skills/slack_integration_for_team_awareness.md`

## Original description

Problem: Team operates in isolation with no real-time coordination or stakeholder updates. Research completions, code failures, and important findings are not communicated. Solution: Add Slack MCP server to send notifications, create alerts, and enable cross-crew communication. This improves situational awareness and enables human-in-the-loop oversight.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 761` / `reject 761` via Signal.
