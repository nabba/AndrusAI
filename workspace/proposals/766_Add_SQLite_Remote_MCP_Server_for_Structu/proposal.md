# Proposal #766: Add SQLite Remote MCP Server for Structured Data Persistence

**Type:** code  
**Created:** 2026-04-22T23:58:31.587746+00:00  

## Why this is useful

Problem: The team has no database access beyond raw file storage. Research crew's ecological/forestry statistical data and policy analysis findings cannot be queried, joined, or persisted in structured form. The coding crew's Docker sandbox has ephemeral storage. Solution: Add the node2flow/sqlite-remote MCP server to provide full SQLite database capabilities via Turso/libSQL. This gives both research and coding crews a persistent, queryable datastore for tabular results, project metadata, task history, and cross-referenceable policy data.

Required actions:
1. User provisions a Turso database (or use local fallback via docker)
2. Run: mcp_add_server with name='node2flow/sqlite-remote', query='postgresql sqlite database persistence', env_vars='LIBSQL_URL=turso://your-db-url;LIBSQL_AUTH_TOK

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Problem: The team has no database access beyond raw file storage. Research crew's ecological/forestry statistical data and policy analysis findings cannot be queried, joined, or persisted in structured form. The coding crew's Docker sandbox has ephemeral storage. Solution: Add the node2flow/sqlite-remote MCP server to provide full SQLite database capabilities via Turso/libSQL. This gives both research and coding crews a persistent, queryable datastore for tabular results, project metadata, task history, and cross-referenceable policy data.

Required actions:
1. User provisions a Turso database (or use local fallback via docker)
2. Run: mcp_add_server with name='node2flow/sqlite-remote', query='postgresql sqlite database persistence', env_vars='LIBSQL_URL=turso://your-db-url;LIBSQL_AUTH_TOKEN=your-token'
3. Or simpler: use Supabase MCP server (6,872 installs) with Postgres: mcp_add_server name='Supabase', query='postgresql sqlite database persistence', env_vars='SUPABASE_ACCESS_TOKEN=...'
4. Create skill file: skills/sqlite_persistence_patterns.md with schema templates for research findings, task logs, and document metadata

Impact: Enables data-driven analysis — researchers can store/query Estonian forestry stats, policy documents get persistent IDs, coding crew can store benchmark results. Supports relational queries across all crews' outputs.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 766` / `reject 766` via Signal.
