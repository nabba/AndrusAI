# Database Integration with Supabase MCP

## Problem
- No persistent structured storage for research findings, policy data, or project artifacts
- All data lives in transient memory or scattered files
- Cannot run SQL queries or maintain relational data
- No way to build cumulative knowledge across sessions

## Solution
Add Supabase MCP server to provide PostgreSQL database capabilities:
- SQL query execution
- Schema management and migrations
- Row-level CRUD operations
- Connection pooling and auth

## Implementation

### 1. Add MCP Server
```bash
# Via mcp_add_server tool
name: Supabase
 env_vars: SUPABASE_URL=<your-project-ref>;SUPabase_ANON_KEY=<your-anon-key>
```

### 2. Create Core Tables
```sql
-- Research findings storage
CREATE TABLE research_findings (
  id SERIAL PRIMARY KEY,
  topic TEXT NOT NULL,
  source_urls JSONB,
  extracted_data JSONB,
  confidence_score FLOAT,
  created_at TIMESTAMP DEFAULT NOW(),
  crew_origin TEXT,
  tags TEXT[]
);

-- Policy analysis repository
CREATE TABLE policy_documents (
  id SERIAL PRIMARY KEY,
  country_code VARCHAR(3),
  document_type VARCHAR(50),
  title TEXT,
  raw_text TEXT,
  structured_critique JSONB,
  last_updated TIMESTAMP,
  reviewed_by TEXT
);

-- Citation and source library
CREATE TABLE citation_library (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  authors TEXT,
  publication_date DATE,
  url TEXT UNIQUE,
  metadata JSONB,
  indexed_content TSVECTOR
);
```

### 3. Usage Patterns for Crews

**Research Crew:**
- Store search results with source URLs and extracted data
- Build cumulative knowledge graphs via JSONB relationships
- Maintain citation library with full-text search

**Writing Crew:**
- Retrieve policy documents and research findings
- Store draft versions and revision history
- Track white paper citations and references

**Coding Crew:**
- Store code snippets, Docker configs, and best practices
- Track deployment artifacts and environment configs

## Benefits
- Persistent knowledge base that grows over time
- Complex queries and aggregations across datasets
- Historical tracking and audit trails
- Foundation for future analytics and reporting

## Next Steps
1. Provision Supabase project
2. Connect via mcp_add_server
3. Run schema setup SQL
4. Add database query tools to agent toolkit
5. Implement data retention and archiving policies