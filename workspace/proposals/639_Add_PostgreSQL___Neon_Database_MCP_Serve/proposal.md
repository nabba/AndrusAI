# Proposal #639: Add PostgreSQL & Neon Database MCP Servers

**Type:** code  
**Created:** 2026-04-18T22:55:14.254067+00:00  

## Why this is useful

The team has vector database optimization skills but lacks direct database connectivity for persistent storage, complex queries, and data versioning of ecological datasets. Adding Supabase and Neon MCP servers (found via search) enables: SQL querying, schema management, safe migrations via branches, and integration with rapid ecological data integration patterns. This supports reproducible research, large-scale scenario modeling data storage, and stakeholder database management.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

The team has vector database optimization skills but lacks direct database connectivity for persistent storage, complex queries, and data versioning of ecological datasets. Adding Supabase and Neon MCP servers (found via search) enables: SQL querying, schema management, safe migrations via branches, and integration with rapid ecological data integration patterns. This supports reproducible research, large-scale scenario modeling data storage, and stakeholder database management.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 639` / `reject 639` via Signal.
