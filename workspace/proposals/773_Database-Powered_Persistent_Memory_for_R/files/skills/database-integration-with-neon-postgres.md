---
id: database-integration-neon-postgres
created: 2025-01-20
author: System Improvement Analyst
type: skill
category: data-persistence
---

# Database-Powered Persistent Memory for Research Agents

## Problem Statement
The research crew currently operates with volatile memory. Results from `web_search`, `web_fetch`, and synthesis output are stored in transient conversation context or basic text files. There is no:
- Persistent storage across sessions
- Structured querying of accumulated research
- Relational linking between queries, sources, and findings
- Audit trail of research provenance

This mirrors the #1 failure pattern identified in enterprise AI guides: **data pipeline failures** causing incorrect operation in production.

## Solution: Neon PostgreSQL MCP Integration

### Why Neon?
- Modern PostgreSQL hosting with branch-based migrations (safe schema evolution)
- MCP server available via remote connection (no local setup)
- Supports complex queries, indexes, and JSON data types
- Enables vector search capabilities with pgvector for semantic similarity

### Architecture

```
Research Agent
    ↓ (creates)
research_queries table
    ↓ (generates)
research_findings table
    ↓ (cites)
sources table
    ↓ (stores)
entity_relationships (Estonian policy actors, PSPs, etc.)
```

### Schema Design

```sql
-- Track all research queries with parameters
CREATE TABLE research_queries (
    id SERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    search_terms JSONB,
    crew_origin VARCHAR(50), -- research, coding, writing
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB
);

-- Store synthesized findings with provenance
CREATE TABLE research_findings (
    id SERIAL PRIMARY KEY,
    query_id INT REFERENCES research_queries(id) ON DELETE CASCADE,
    finding_text TEXT NOT NULL,
    confidence_score FLOAT,
    source_count INT,
    extracted_entities JSONB, -- Named entities: PSP names, countries, policies
    tags TEXT[], -- e.g. ['estonia', 'forestry', 'payment-provider']
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Canonical source tracking with deduplication
CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    finding_id INT REFERENCES research_findings(id) ON DELETE CASCADE,
    url TEXT UNIQUE,
    title TEXT,
    snippet TEXT,
    domain VARCHAR(255),
    content_hash CHAR(64), -- SHA256 for deduplication
    retrieved_at TIMESTAMPTZ
);

--Specialized Estonian research tables
CREATE TABLE estonian_entities (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(50), -- 'organization', 'policy_document', 'person'
    name TEXT NOT NULL,
    metadata JSONB, -- Roles, dates, relationships
    first_seen_in_query INT REFERENCES research_queries(id)
);

-- Vector embeddings for semantic search (using pgvector)
CREATE TABLE finding_embeddings (
    finding_id INT PRIMARY KEY REFERENCES research_findings(id) ON DELETE CASCADE,
    embedding vector(1536), -- OpenAI ada-002 dimension
    updated_at TIMESTAMPTZ
);

-- Indexes for performance
CREATE INDEX idx_findings_created ON research_findings(created_at DESC);
CREATE INDEX idx_findings_tags ON research_findings USING GIN(tags);
CREATE INDEX idx_sources_domain ON sources(domain);
CREATE INDEX idx_entities_name ON estonian_entities(name text_pattern_ops);
CREATE INDEX idx_embeddings_vector ON finding_embeddings USING ivfflat (embedding vector_cosine_ops);
```

### Implementation Pattern

1. **Initialize connection** in research agent startup:
   ```python
   import mcp_client
   neon = mcp_client.get_server('neon')
   ```

2. **Log every search**:
   ```python
   query_id = neon.execute(
       'INSERT INTO research_queries (query_text, search_terms, crew_origin) VALUES (?, ?, ?) RETURNING id',
       [user_query, json.dumps(search_params), 'research']
   )
   ```

3. **Store findings with citation linking**:
   ```python
   finding_id = neon.execute(
       'INSERT INTO research_findings (query_id, finding_text, confidence_score, source_count, tags) VALUES (?, ?, ?, ?, ?) RETURNING id',
       [query_id, synthesis, 0.92, len(sources), ['estonia', 'forestry']]
   )
   for source in sources:
       neon.execute('INSERT INTO sources (finding_id, url, title, snippet, domain, content_hash) VALUES (?, ?, ?, ?, ?, ?)',
           [finding_id, source.url, source.title, source.snippet, source.domain, sha256(source.content)])
   ```

4. **Enable cross-session retrieval**:
   ```python
   # Get all findings about Estonian forestry from last 30 days
   results = neon.execute('''
       SELECT f.finding_text, s.url, s.title
       FROM research_findings f
       JOIN sources s ON f.id = s.finding_id
       WHERE 'estonia' = ANY(f.tags)
         AND 'forestry' = ANY(f.tags)
         AND f.created_at > NOW() - INTERVAL '30 days'
       ORDER BY f.created_at DESC
   ''')
   ```

### Migration Strategy

Use Neon's temporary branches for zero-downtime schema updates:
1. Create migration SQL files in `migrations/YYYYMMDD_description.sql`
2. Apply on feature branch
3. Test with production-like data
4. Merge to main through CI pipeline

### Benefits
- **Stateful agents**: Previous research remains accessible across conversations
- **Audit trail**: Full provenance from question to answer
- **Efficiency**: Avoid re-searching same topics (deduplication via content_hash)
- **Enterprise-ready**: Meets compliance requirements for data retention
- **Enables new capabilities**:
  - Trend analysis across time
  - Entity relationship mapping
  - Automated literature reviews

### Assets Required
Add MCP server: `neon` (from search results - Neon PostgreSQL)
Environment variables: `DATABASE_URL` (connection string)

### Success Metrics
- 100% of research outputs stored persistently within 1 week
- <100ms retrieval for 95% of queries
- Zero data loss on server restart