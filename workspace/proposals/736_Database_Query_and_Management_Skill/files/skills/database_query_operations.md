# Database Query and Management Skill

## Overview
This skill enables the team to connect to, query, and manage relational databases (PostgreSQL, MySQL, SQLite) for data persistence and analysis tasks.

## Core Capabilities

### 1. Connection Management
- Establish secure database connections using connection strings
- Handle connection pooling for efficiency
- Manage environment variables for credentials (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)

### 2. SQL Query Operations
```sql
-- Common query patterns
SELECT * FROM table WHERE condition ORDER BY column LIMIT n;
INSERT INTO table (columns) VALUES (values) RETURNING *;
UPDATE table SET column = value WHERE condition;
DELETE FROM table WHERE condition;

-- Aggregation patterns
SELECT category, COUNT(*), AVG(value) FROM table GROUP BY category;

-- Join patterns
SELECT a.*, b.name FROM table_a a JOIN table_b b ON a.id = b.a_id;
```

### 3. Schema Inspection
```sql
-- List tables
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';

-- Describe table structure
SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'target_table';
```

### 4. Data Migration Patterns
- Create backup tables before schema changes
- Use transactions for multi-step operations
- Implement idempotent migrations

## MCP Server Integration

### Connecting Supabase MCP Server
Use `mcp_add_server` with:
- Server name: `Supabase`
- Query: `database postgres mysql`
- Env vars: `SUPABASE_URL=your-url;SUPABASE_KEY=your-key`

### Connecting Neon MCP Server
- Server name: `neon`
- Query: `database postgres mysql`
- Env vars: `NEON_API_KEY=your-key`

## Common Use Cases

### Research Data Storage
```python
# Store research findings
INSERT INTO research_findings (topic, source, summary, created_at)
VALUES ('policy_analysis', 'https://...', 'Summary text', NOW());
```

### Aggregated Reporting
```sql
-- Generate summary statistics
SELECT topic, COUNT(*) as finding_count
FROM research_findings
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY topic;
```

## Best Practices
1. Always use parameterized queries to prevent SQL injection
2. Wrap destructive operations in transactions
3. Create indexes on frequently queried columns
4. Use RETURNING clause to get inserted/updated row data
5. Close connections when done (MCP handles this automatically)

## Error Handling
- Connection errors: Check credentials and network access
- Query errors: Validate SQL syntax and table/column names
- Permission errors: Verify user has required privileges
- Timeout errors: Add appropriate indexes or optimize queries