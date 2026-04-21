# Database Integration for Persistent Data Storage

## Problem Statement
The AI agent team lacks persistent database access. While `code_executor` enables temporary computation, there is no way to:
- Store research findings in a queryable format across sessions
- Connect to existing client databases
- Perform SQL-based data analysis on structured data
- Build applications requiring persistent state

## Solution: MCP Database Server Integration

### Available MCP Servers
Based on MCP registry search, the following databases are available:

1. **Neon** (PostgreSQL) - Best for serverless, branch-based workflows
   - Execute SQL queries
   - Safe schema migrations using temporary branches
   - Performance optimization tools

2. **Supabase** - Full backend platform
   - PostgreSQL database
   - Edge Functions
   - Real-time subscriptions
   - Built-in authentication

3. **PlanetScale** - MySQL-compatible, branch-based
   - Read/write SQL queries
   - Query performance insights

### Recommended Implementation

```python
# Example: Adding Neon PostgreSQL via MCP
# Run: mcp_add_server with neon configuration
```

### Integration Patterns

#### 1. Research Data Persistence
```sql
CREATE TABLE research_findings (
    id SERIAL PRIMARY KEY,
    topic VARCHAR(255),
    source_url TEXT,
    summary TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### 2. Session Memory Storage
```sql
CREATE TABLE session_memory (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100),
    agent_role VARCHAR(50),
    content TEXT,
    embedding VECTOR(1536), -- For semantic search
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### 3. Analysis Results Cache
```sql
CREATE TABLE analysis_cache (
    id SERIAL PRIMARY KEY,
    query_hash VARCHAR(64),
    result JSONB,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## Usage Examples

### Storing Research Findings
```python
# After web research, persist findings
INSERT INTO research_findings (topic, source_url, summary, metadata)
VALUES ('Estonian deforestation policy', 'https://...', 'Summary...', '{"relevance": 0.95}');
```

### Querying Historical Data
```python
# Retrieve related past research
SELECT * FROM research_findings
WHERE topic ILIKE '%environmental%'
ORDER BY created_at DESC LIMIT 10;
```

## Skill Acquisition Steps

1. **Add MCP Database Server**
   ```
   mcp_add_server(name="neon", query="database postgres", env_vars="NEON_API_KEY=...")
   ```

2. **Create Core Schema**
   - Design tables for agent memory
   - Set up indexes for common queries
   - Configure connection pooling

3. **Implement Repository Pattern**
   - Create Python helpers for common operations
   - Add error handling for connection failures
   - Implement retry logic

## Benefits

- **Persistence**: Research and analysis survive session boundaries
- **Queryability**: SQL enables complex data relationships
- **Integration**: Connect to existing client databases
- **Performance**: Indexed queries faster than file-based storage
- **Collaboration**: Multiple agents can share structured data

## Anti-Patterns to Avoid

- Storing binary blobs in database (use file_manager)
- Running unparameterized queries (SQL injection risk)
- Creating tables per session (use session_id column)
- Ignoring connection limits (use pooling)
