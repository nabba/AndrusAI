# Database Querying Patterns

## Problem
The team lacks database connectivity. This blocks:
- Extracting data from production databases
- Running analytics queries
- Data migration and transformation
- Report generation from live data
- Integration with existing data infrastructure

## Solution: Add Database MCP Server

### PostgreSQL Options

**Neon (Recommended for Development)**
```
mcp_add_server:
  name: "neon"
  query: "database postgres mysql mongodb"
  env_vars: "NEON_API_KEY=xxx"
```
- Branch-based development (safe schema migrations)
- Serverless PostgreSQL
- Auto-scaling

**Supabase (Full Backend Platform)**
```
mcp_add_server:
  name: "Supabase"
  query: "database postgres mysql mongodb"
  env_vars: "SUPABASE_URL=xxx;SUPABASE_KEY=xxx"
```
- PostgreSQL with REST API
- Real-time subscriptions
- Authentication built-in

**PlanetScale (MySQL-compatible)**
```
mcp_add_server:
  name: "planetscale"
  query: "database postgres mysql mongodb"
  env_vars: "PLANETSCALE_TOKEN=xxx"
```
- Branch-based workflow
- No downtime schema changes
- MySQL dialect

## Common Query Patterns

### 1. Data Extraction
```sql
-- Always use LIMIT for exploration
SELECT * FROM users LIMIT 10;

-- Use explicit column lists for production
SELECT id, email, created_at FROM users WHERE active = true;
```

### 2. Aggregation Queries
```sql
SELECT 
  DATE(created_at) as date,
  COUNT(*) as count,
  SUM(amount) as total
FROM orders
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

### 3. Safe Schema Changes
1. Create a branch (Neon/PlanetScale)
2. Apply migration
3. Test thoroughly
4. Merge to main branch

## Best Practices

### Query Safety
- Always use LIMIT in exploratory queries
- Use transactions for multi-step operations
- Test schema changes on branches first
- Backup before destructive operations

### Performance
- Create indexes for frequently queried columns
- Use EXPLAIN ANALYZE for slow queries
- Prefer specific columns over SELECT *
- Use pagination for large result sets

### Security
- Use read-only users for analytics
- Never expose credentials in logs
- Parameterize queries to prevent SQL injection
- Rotate credentials regularly

## Workflow Integration

1. **Research Crew**: Query data for analysis
2. **Coding Crew**: Build migrations and transformations
3. **Writing Crew**: Generate reports from query results

## Error Handling

```python
# In code_executor, handle connection errors gracefully
try:
    result = query_database(sql)
except ConnectionError:
    # Check MCP server status with mcp_list_servers
    # Re-add server if disconnected
except QueryError as e:
    # Log error and suggest fixes
    # Common issues: syntax, permissions, timeout
```
