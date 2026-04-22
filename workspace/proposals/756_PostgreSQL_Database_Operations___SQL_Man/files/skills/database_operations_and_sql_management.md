# Database Operations & SQL Management

## Overview
Enable persistent data storage, complex querying, and application-level database integration for the agent team.

## Core Competencies
- PostgreSQL connection management and configuration
- CRUD operations (Create, Read, Update, Delete)
- Complex joins, subqueries, and set operations
- Indexing strategies and query optimization
- Schema design and migrations
- Transaction management and ACID properties
- Connection pooling and performance tuning
- Backup, restore, and disaster recovery

## MCP Integration
- **Server**: `neon` (PostgreSQL management)
- **Tool**: `mcp_add_server` with name `neon`
- **Capabilities**: Execute SQL queries, manage branches, perform safe migrations, optimize performance

## Usage Patterns
```
# Execute queries
SELECT * FROM research_findings WHERE topic = 'estonian_policy';

# Schema migrations
CREATE TABLE IF NOT EXISTS agent_memory (
    id SERIAL PRIMARY KEY,
    topic VARCHAR(255),
    content TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

# Index creation for performance
CREATE INDEX idx_topic ON research_findings(topic);
```

## Best Practices
1. Use parameterized queries to prevent SQL injection
2. Implement proper connection pooling
3. Regular vacuum and analyze operations
4. Monitor slow queries and optimize
5. Use transactions for data consistency
6. Implement proper indexing strategy
7. Regular backups with point-in-time recovery

## Common Workflows
- Store research results with structured metadata
- Track agent performance and metrics
- Maintain application state and user sessions
- Cache expensive query results
- Build data pipelines and ETL processes
- Enable complex analytical queries across datasets
