# Database Connectivity Patterns

## Overview
This skill enables the team to interact with SQL databases for persistent storage, analytics, and data operations.

## Available MCP Servers

### 1. Supabase (Recommended for Full-Stack)
- **Remote**: `https://server.smithery.ai/Supabase/mcp`
- **Best for**: PostgreSQL with built-in auth, real-time, and storage
- **Use cases**: Application backends, user data, analytics

### 2. Neon (Recommended for Development)
- **Remote**: `https://server.smithery.ai/neon/mcp`
- **Best for**: Serverless PostgreSQL with branching
- **Use cases**: Schema migrations, development branches, testing

### 3. Planetscale (Recommended for Scale)
- **Remote**: `https://server.smithery.ai/planetscale/mcp`
- **Best for**: MySQL-compatible serverless with branching
- **Use cases**: Large-scale production apps, zero-downtime migrations

## Common Patterns

### Pattern 1: Execute SQL Query
```
1. Connect to database MCP server
2. Use execute_sql tool with query parameter
3. Results returned as structured JSON
```

### Pattern 2: Schema Migration
```
1. Create temporary branch (Neon/Planetscale)
2. Run DDL statements
3. Test changes
4. Merge to main branch
```

### Pattern 3: Data Analysis
```
1. Connect to Supabase MCP
2. Query data with aggregations
3. Export results to file_manager for visualization
```

## Integration with Existing Tools
- **code_executor**: Write Python scripts that fetch DB results
- **file_manager**: Store query results as CSV/JSON
- **research crew**: Query public datasets

## Setup Instructions
1. Use `mcp_add_server` with server name and query
2. Provide API credentials via env_vars parameter
3. Test connection with simple SELECT query
