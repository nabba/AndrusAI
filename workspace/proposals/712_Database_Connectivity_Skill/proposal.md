# Proposal #712: Database Connectivity Skill

**Type:** skill  
**Created:** 2026-04-20T23:34:45.445451+00:00  

## Why this is useful

Problem: The team lacks database interaction capabilities. While code_executor can run Python, there's no persistent data storage, no SQL query execution, and no way to interact with production databases. Tasks requiring data persistence, analytics on structured data, or database migrations would fail. Solution: Create a skill document teaching agents how to leverage database MCP servers (Supabase, Neon, Planetscale) for data operations.

## What will change

- Modifies `skills/database_connectivity_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/database_connectivity_patterns.md`

## Original description

Problem: The team lacks database interaction capabilities. While code_executor can run Python, there's no persistent data storage, no SQL query execution, and no way to interact with production databases. Tasks requiring data persistence, analytics on structured data, or database migrations would fail. Solution: Create a skill document teaching agents how to leverage database MCP servers (Supabase, Neon, Planetscale) for data operations.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 712` / `reject 712` via Signal.
