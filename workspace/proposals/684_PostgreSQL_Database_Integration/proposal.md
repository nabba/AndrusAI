# Proposal #684: PostgreSQL Database Integration

**Type:** code  
**Created:** 2026-04-20T11:42:54.789157+00:00  

## Why this is useful

Problem: Team has no persistent structured storage. Current data lives only in memory/files. Solution: Add Supabase/PostgreSQL MCP server for reliable data storage, SQL querying, and state persistence across research sessions.

## What will change

- Modifies `skills/database_persistence.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `skills/database_persistence.md`

## Original description

Problem: Team has no persistent structured storage. Current data lives only in memory/files. Solution: Add Supabase/PostgreSQL MCP server for reliable data storage, SQL querying, and state persistence across research sessions.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 684` / `reject 684` via Signal.
