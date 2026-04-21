# Database Persistence Operations

## Overview
This skill enables AI agents to interact with relational databases for structured data storage, retrieval, and manipulation.

## MCP Server Required
Add a database MCP server (choose based on needs):

### Option 1: Neon (PostgreSQL, cloud)
```
mcp_add_server(name="neon", query="database postgres sqlite", env_vars="NEON_API_KEY=your_key")
```

### Option 2: Supabase (PostgreSQL with auth/storage)
```
mcp_add_server(name="Supabase", query="database postgres sqlite", env_vars="SUPABASE_URL=url;SUPABASE_KEY=key")
```

## Common Operations

### 1. Schema Management
- Create tables with appropriate types
- Define indexes for performance
- Set up foreign key relationships
- Run migrations safely

### 2. Data Operations (CRUD)
- INSERT: Store research results, logs, agent outputs
- SELECT: Query historical data, retrieve cached results
- UPDATE: Modify existing records
- DELETE: Remove outdated data

### 3. Query Patterns
- Complex joins for related data
- Aggregations for analytics
- Full-text search (PostgreSQL)
- JSON/JSONB operations

### 4. Data Migration
- Create backup branches (Neon)
- Run schema migrations safely
- Rollback on errors

## Use Cases for AI Agent Teams

### Research Crew
- Store scraped data for analysis
- Cache search results to avoid re-fetching
- Track research session history

### Coding Crew
- Persist code execution logs
- Store test results
- Track code performance metrics

### Writing Crew
- Store draft versions
- Track editing history
- Manage content metadata

### Self-Improvement Crew
- Log improvement proposals
- Track implemented changes
- Store performance benchmarks

## Best Practices
1. Always use parameterized queries to prevent SQL injection
2. Create indexes on frequently queried columns
3. Use transactions for multi-step operations
4. Implement proper error handling and rollback
5. Document schema changes in migrations

## Integration with Vector Database
Combine with existing vector optimization skill:
- Store embeddings metadata in PostgreSQL
- Track vector index status
- Log retrieval performance metrics