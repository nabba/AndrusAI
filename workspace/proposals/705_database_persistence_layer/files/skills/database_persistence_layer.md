# Database Persistence Layer Skill

## Purpose
Enable persistent data storage, structured queries, and data pipeline development.

## Gap Addressed
The team has `advanced_vector_database_optimization` skill but no database tools. Cannot persist data between sessions, store structured records, or build data applications.

## Required MCP Server
Use `mcp_add_server` with one of:
- **Supabase** (name: `Supabase`, query: `database postgres`) - Full PostgreSQL + auth
- **Neon** (name: `neon`, query: `database postgres`) - Serverless PostgreSQL
- **PlanetScale** (name: `planetscale`, query: `database postgres`) - MySQL-compatible

## Core Capabilities Gained

### Schema Management
- Create tables and indexes
- Define relationships (foreign keys)
- Run migrations
- Manage constraints

### Data Operations
- INSERT records with validation
- SELECT with filtering, sorting, pagination
- UPDATE with conditions
- DELETE with safeguards

### Query Optimization
- Create indexes for performance
- Analyze query plans
- Monitor slow queries
- Optimize table structure

### Advanced Features
- Full-text search
- JSON/JSONB queries
- Stored procedures
- Triggers and functions

## Common Workflow Patterns

### Pattern 1: Research Data Persistence
```
1. Research crew gathers web data
2. Parse and structure data
3. Check if table exists, create if not
4. INSERT with deduplication
5. Query for analysis
```

### Pattern 2: Session Memory Persistence
```
1. During task execution, store findings
2. Use JSONB for flexible schema
3. Query previous sessions for context
4. Build cumulative knowledge base
```

### Pattern 3: Analytics Pipeline
```
1. Aggregate data from multiple sources
2. Store in staging table
3. Transform with SQL
4. Insert into production table
5. Generate reports via query
```

## Schema Design Patterns

### Research Findings Table
```sql
CREATE TABLE research_findings (
  id SERIAL PRIMARY KEY,
  topic VARCHAR(255) NOT NULL,
  source_url TEXT,
  summary TEXT,
  metadata JSONB,
  created_at TIMESTAMP DEFAULT NOW(),
  session_id UUID
);

CREATE INDEX idx_topic ON research_findings(topic);
CREATE INDEX idx_session ON research_findings(session_id);
```

### Task Execution Log
```sql
CREATE TABLE task_executions (
  id SERIAL PRIMARY KEY,
  task_description TEXT,
  crew VARCHAR(50),
  status VARCHAR(20),
  result JSONB,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  duration_seconds INT
);
```

### Knowledge Base
```sql
CREATE TABLE knowledge_base (
  id SERIAL PRIMARY KEY,
  key VARCHAR(255) UNIQUE,
  value TEXT,
  category VARCHAR(100),
  embedding VECTOR(1536), -- if using pgvector
  updated_at TIMESTAMP DEFAULT NOW()
);
```

## Authentication Requirements
- Database connection string or URL
- API key for managed services
- Set via env_vars when adding server

## Integration with Existing Tools
- `code_executor`: Run data transformations
- `memory_store`: Sync with database for persistence
- `web_search`: Gather data for storage
- `file_manager`: Export query results to CSV/JSON

## Best Practices
1. Always use parameterized queries (prevent SQL injection)
2. Create indexes on frequently queried columns
3. Use transactions for multi-step operations
4. Implement soft deletes (deleted_at column)
5. Add created_at/updated_at timestamps
6. Use JSONB for flexible metadata storage

## Performance Optimization
- Use connection pooling
- Batch inserts (100-1000 rows per batch)
- Create covering indexes for common queries
- Use EXPLAIN ANALYZE for slow queries
- Partition large tables by date

## Backup & Recovery
- Managed services include automatic backups
- Export critical data to files periodically
- Use transactions for data integrity
- Log schema changes for audit trail
