# Proposal #758: PostgreSQL Database Integration via Supabase MCP

**Type:** code  
**Created:** 2026-04-22T15:41:10.726020+00:00  

## Why this is useful

Problem: Team lacks structured persistent storage. Cannot save/query research findings, policy data, or project artifacts efficiently. All data lives in memory or scattered files. Solution: Add Supabase MCP server to provide PostgreSQL database with SQL queries, schema management, and row operations. Enables storing research datasets, citation libraries, policy analysis results, and team knowledge base with proper indexing and retrieval.

## What will change

- Modifies `skills/database_integration_with_supabase.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `skills/database_integration_with_supabase.md`

## Original description

Problem: Team lacks structured persistent storage. Cannot save/query research findings, policy data, or project artifacts efficiently. All data lives in memory or scattered files. Solution: Add Supabase MCP server to provide PostgreSQL database with SQL queries, schema management, and row operations. Enables storing research datasets, citation libraries, policy analysis results, and team knowledge base with proper indexing and retrieval.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 758` / `reject 758` via Signal.
