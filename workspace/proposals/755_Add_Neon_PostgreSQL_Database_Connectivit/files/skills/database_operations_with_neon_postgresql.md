---
category: infrastructure
tags: [database, postgresql, sql, neon]
---

# Database Operations with Neon PostgreSQL

The team currently lacks persistent structured data storage. This skill covers:

## Setup
- Add Neon MCP server via `mcp_add_server` with name "neon"
- Requires NEON_API_KEY and NEON_PROJECT_ID environment variables

## Core Capabilities
- Create/manage PostgreSQL projects and branches
- Execute parameterized SQL queries safely
- Perform schema migrations using temporary branches (safe changes)
- Inspect database schemas, tables, and relationships
- Performance optimization through query analysis

## Use Cases
- Store scraped Estonian forestry statistics in normalized tables
- Track research project metadata and sources
- Cache web search results to avoid redundant API calls
- Maintain audit trails of policy document analyses

## Best Practices
- Always use branches for schema changes, then merge after validation
- Parameterize queries to prevent SQL injection
- Use connection pooling for high-frequency operations
### Query Optimization
- Create indexes on frequently queried columns (e.g., document dates, source URLs)
- Use EXPLAIN ANALYZE for complex queries
- Partition large time-series data (forestry statistics by year)

## Example Workflow
1. Research crew scrapes Estonia forestry data → store in `raw_imports` table
2. Coding crew transforms and normalizes → move to `forestry_stats` table
3. Writing crew queries for report generation using JOINs across policy, statistics, and analysis tables
4. Self-improvement crew logs successful patterns to `methodology_registry`