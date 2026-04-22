# Proposal #755: Add Neon PostgreSQL Database Connectivity

**Type:** code  
**Created:** 2026-04-22T05:56:13.902511+00:00  

## Why this is useful

Problem: Team lacks persistent structured data storage and SQL query capabilities. Estonian forestry statistics, policy documents, and research findings cannot be stored in a queryable database. Solution: Add Neon MCP server to enable PostgreSQL database operations, schema migrations, and safe SQL queries with branch-based testing.

## What will change

- Modifies `.env.example`
- Modifies `skills/database_operations_with_neon_postgresql.md`

## Potential risks to other subsystems

- Uncategorised (.env.example): impact scope unclear
- Uncategorised (skills): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `.env.example`
- `skills/database_operations_with_neon_postgresql.md`

## Original description

Problem: Team lacks persistent structured data storage and SQL query capabilities. Estonian forestry statistics, policy documents, and research findings cannot be stored in a queryable database. Solution: Add Neon MCP server to enable PostgreSQL database operations, schema migrations, and safe SQL queries with branch-based testing.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 755` / `reject 755` via Signal.
