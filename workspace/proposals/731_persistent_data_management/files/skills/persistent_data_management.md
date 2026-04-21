# Persistent Data Management Skill

## Purpose
Enable structured data storage and retrieval across sessions using SQLite/PostgreSQL databases.

## MCP Server Required
Add the `neon` or built-in SQLite MCP server for database operations.

## Core Capabilities

### 1. Research Data Storage
```sql
-- Store research findings with metadata
CREATE TABLE IF NOT EXISTS research_findings (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    source_url TEXT,
    content TEXT,
    confidence_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tags TEXT
);
```

### 2. Knowledge Accumulation
- Store extracted insights from web research
- Track source provenance for citation
- Enable semantic search preparation (pre-vector storage)

### 3. Analysis History
- Log all code executions with results
- Track query patterns for optimization
- Build reusable query templates

## Workflow Integration

### Research Crew
After completing research, store findings:
1. Extract key facts and sources
2. Insert into research_findings table
3. Tag with relevant topics for retrieval

### Coding Crew
Before executing analysis:
1. Check if relevant data already exists in database
2. Query historical results to avoid redundant work
3. Store new analysis results with parameters

### Writing Crew
When synthesizing reports:
1. Query database for all relevant findings on topic
2. Use structured data for citations
3. Track report generation history

## Anti-Patterns to Avoid
- Do NOT store sensitive credentials in database
- Do NOT create tables without IF NOT EXISTS guards
- Do NOT skip indexing on frequently queried columns

## Example Usage Patterns

### Store Research Finding
```
INSERT INTO research_findings (topic, source_url, content, confidence_score, tags)
VALUES ('Estonian forestry policy', 'https://...', 'Policy states...', 0.85, 'environment, policy, Estonia');
```

### Retrieve Historical Research
```
SELECT * FROM research_findings 
WHERE topic LIKE '%Estonia%' 
ORDER BY created_at DESC 
LIMIT 10;
```

## Recommended MCP Server
Use `neon` for PostgreSQL (cloud) or local SQLite for embedded storage.
Command to add: `mcp_add_server` with name='neon' or built-in SQLite option.
