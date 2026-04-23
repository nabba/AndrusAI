# Proposal #759: Playwright MCP Server Integration for Reliable Browser Automation

**Type:** code  
**Created:** 2026-04-22T16:53:50.726959+00:00  

## Why this is useful

Problem: Current browser tools (browser_fetch, browser_click, browser_screenshot) are brittle and lack structured page understanding. The team needs deterministic, accessibility-aware web automation for research and QA tasks. Solution: Install the Playwright MCP server to give agents structured browser control via accessibility trees, auto-waiting, and cross-browser support. Add npx @playwright/mcp@latest to MCP configuration.

## What will change

- Modifies `skills/playwright_browser_automation.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `skills/playwright_browser_automation.md`

## Original description

Problem: Current browser tools (browser_fetch, browser_click, browser_screenshot) are brittle and lack structured page understanding. The team needs deterministic, accessibility-aware web automation for research and QA tasks. Solution: Install the Playwright MCP server to give agents structured browser control via accessibility trees, auto-waiting, and cross-browser support. Add npx @playwright/mcp@latest to MCP configuration.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 759` / `reject 759` via Signal.
