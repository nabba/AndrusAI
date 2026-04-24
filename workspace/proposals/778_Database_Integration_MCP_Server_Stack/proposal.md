# Proposal #778: Database Integration MCP Server Stack

**Type:** code  
**Created:** 2026-04-24T13:10:45.862841+00:00  

## Why this is useful

Problem: No direct database access - current tooling relies on web scraping for data, which fails for private/internal databases and is inefficient for structured data. Solution: Add PostgreSQL, MySQL, and SQLite MCP servers to enable querying, schema inspection, and data operations directly from agents. This unlocks business intelligence, reporting, and LLM-powered database analysis use cases.

## What will change

- Modifies `mcp_config.json`
- Modifies `SETUP_DATABASE_MCP.md`

## Potential risks to other subsystems

- Uncategorised (mcp_config.json): impact scope unclear
- Uncategorised (SETUP_DATABASE_MCP.md): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `mcp_config.json`
- `SETUP_DATABASE_MCP.md`

## Original description

Problem: No direct database access - current tooling relies on web scraping for data, which fails for private/internal databases and is inefficient for structured data. Solution: Add PostgreSQL, MySQL, and SQLite MCP servers to enable querying, schema inspection, and data operations directly from agents. This unlocks business intelligence, reporting, and LLM-powered database analysis use cases.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 778` / `reject 778` via Signal.
