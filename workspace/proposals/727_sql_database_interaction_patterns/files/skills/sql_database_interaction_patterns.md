# SQL Database Interaction Patterns

## Problem Statement
The team currently lacks database querying capabilities. This limits:
- Research crew: Cannot query structured datasets, perform data analysis on SQL databases
- Coding crew: Cannot build applications with persistent storage, run migrations, or test database code

## When to Use This Skill
- User requests data from a SQL database (PostgreSQL, MySQL, SQLite)
- Analysis requires querying structured data
- Application development needs database integration
- Data migration or ETL tasks

## Available MCP Servers (Not Yet Connected)
Use `mcp_add_server` to connect:

1. **Supabase** - Full PostgreSQL with auth, storage, Edge Functions
   - Best for: Production apps, user authentication, real-time features
   - Add with: `mcp_add_server(name="Supabase", query="database postgres", env_vars="SUPABASE_URL=...;SUPABASE_KEY=...")`

2. **neon** - Serverless PostgreSQL with branching
   - Best for: Development workflows, schema migrations, testing
   - Add with: `mcp_add_server(name="neon", query="database postgres", env_vars="NEON_API_KEY=...")`

## SQL Query Patterns

### Basic SELECT Queries
```sql
-- Select all columns
SELECT * FROM table_name;

-- Select specific columns with filtering
SELECT column1, column2 
FROM table_name 
WHERE condition 
ORDER BY column1 DESC
LIMIT 100;

-- Aggregation examples
SELECT category, COUNT(*) as count, AVG(value) as avg_value
FROM products
GROUP BY category
HAVING COUNT(*) > 10;
```

### JOIN Patterns
```sql
-- Inner join
SELECT a.name, b.order_date
FROM customers a
INNER JOIN orders b ON a.id = b.customer_id;

-- Left join (include unmatched rows)
SELECT a.name, COALESCE(b.order_date, 'No orders')
FROM customers a
LEFT JOIN orders b ON a.id = b.customer_id;
```

### Common Analysis Queries
```sql
-- Time-based analysis
SELECT DATE_TRUNC('month', created_at) as month, COUNT(*)
FROM events
GROUP BY month
ORDER BY month;

-- Window functions for rankings
SELECT name, value,
       RANK() OVER (ORDER BY value DESC) as rank
FROM competitors;
```

## Workflow for Database Tasks

1. **Identify database type** - PostgreSQL, MySQL, SQLite, etc.
2. **Request MCP server addition** if not already connected
3. **Use code_executor** for local SQLite operations with Python's sqlite3
4. **Use MCP database tools** for remote/production databases

## Local SQLite via code_executor
For small datasets, use Python in code_executor:

```python
import sqlite3
import pandas as pd

# Create in-memory database
conn = sqlite3.connect(':memory:')

# Load data
df = pd.read_csv('data.csv')
df.to_sql('my_table', conn, index=False)

# Query
result = pd.read_sql_query('SELECT * FROM my_table LIMIT 10', conn)
print(result)
```

## Decision Matrix

| Task | Tool to Use |
|------|-------------|
| Local SQLite operations | code_executor with Python sqlite3 |
| Production PostgreSQL | Request Supabase/neon MCP |
| Data analysis on CSV/JSON | code_executor with pandas |
| Schema migrations | neon MCP (branching support) |
| Real-time subscriptions | Supabase MCP |

## Requesting Database MCP Addition

When a user task requires database access:

1. Explain the capability gap
2. Suggest the appropriate MCP server
3. Ask user for required credentials
4. Use `mcp_add_server` to connect

Example:
> "This task requires querying a PostgreSQL database. I can connect our team to Supabase or neon for database operations. Please provide your database credentials and I'll set up the connection."
