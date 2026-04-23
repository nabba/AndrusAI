# Playwright Browser Automation for AI Agents

## Overview
Playwright MCP provides structured browser control through the Model Context Protocol. Agents interact with pages using accessibility trees — no vision models or screenshots required.

## Installation
```
claude mcp add playwright npx @playwright/mcp@latest
```

## Core Capabilities
- **Navigation**: goto, reload, back, forward
- **Element Interaction**: click, fill, press, check, uncheck, selectOption
- **Screenshots**: Capture page or specific elements
- **Network Mocking**: Route and intercept requests
- **Storage Management**: Authenticate once, reuse state
- **Multi-browser**: Chromium, Firefox, WebKit

## Agent Usage Pattern
When agent needs to:
1. "Navigate to <url> and add items to cart"
2. "Take a screenshot of the form after filling"
3. "Check network calls after clicking submit"

The Playwright MCP will receive the task as structured accessibility tree commands like:
- goto url="https://..."
- getByRole "textbox", name="Email" → fill
- click ref="e5"
- screenshot path="result.png"

## Advantages Over Current Browser Tools
- Deterministic element references (not brittle selectors)
- Auto-waiting (no manual sleep() calls)
- Full isolation per task
- Trace capture for debugging
- Mobile device emulation support