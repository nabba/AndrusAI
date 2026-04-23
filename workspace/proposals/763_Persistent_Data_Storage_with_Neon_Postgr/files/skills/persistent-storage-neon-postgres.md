# Persistent Data Storage with Neon PostgreSQL

## Problem
The AI team currently lacks persistent, structured storage. `team_memory_store` and `memory_store` are limited in retention and query capabilities. There's no way to store large datasets, historical execution results, or complex relationships between entities across sessions.

## Solution: Neon MCP Server
The Neon MCP server provides full PostgreSQL access in a secure sandbox. It allows creating databases, tables, and running arbitrary SQL queries.

## Setup
1. Search for the server: `mcp_search_servers(query='neon postgres', limit=5)`
2. Add it: `mcp_add_server(name='neon', query='neon postgres', env_vars='NEON_API_KEY=your_key')`
3. Verify with `mcp_list_servers()`.

## When to Use Neon vs Memory
- **Use Neon** for: large datasets (>10k rows), complex joins, long-term archives, structured reports, cross-crew data sharing with strict schemas.
- **Use Team Memory** for: short-term context, quick lookups, unstructured notes that need semantic search.

## Common Schema Patterns
- Research results: `CREATE TABLE findings (id SERIAL PRIMARY KEY, query TEXT, source_url TEXT, content TEXT, timestamp TIMESTAMP);`
- Code execution logs: `CREATE TABLE exec_logs (id SERIAL PRIMARY KEY, code_hash TEXT, stdout TEXT, stderr TEXT, duration_ms INT, success BOOLEAN);`
- Crew coordination: `CREATE TABLE tasks (id SERIAL PRIMARY KEY, crew TEXT, description TEXT, status TEXT, assigned_at TIMESTAMP, completed_at TIMESTAMP);`

## Example Query
Search all research on Estonian deforestation:
```sql
SELECT * FROM findings WHERE content ILIKE '%estonian%' AND content ILIKE '%deforestation%';
```

## Best Practices
- Always parameterize queries to avoid injection (the MCP may handle this, but be cautious).
- Set a cleanup job to prune old logs (Neon offers branching; use ephemeral branches for temporary data).
- Store connection credentials securely; never hardcode in scripts.
