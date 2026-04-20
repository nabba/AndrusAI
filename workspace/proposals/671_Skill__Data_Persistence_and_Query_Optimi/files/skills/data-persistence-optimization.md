# Data Persistence and Query Optimization Patterns

## Core Competencies

### 1. Database Schema Design for AI Workloads
- Entity-relationship modeling for agent state, memory, and artifacts
- Normalization vs denormalization trade-offs for retrieval patterns
- Time-series data design for metrics and logs

### 2. Indexing Strategies
- B-tree vs hash vs GIN/GIST indexes for different query patterns
- Composite indexes for multi-field searches
- Partial indexes for filtered queries
- Index maintenance and fragmentation management

### 3. Query Optimization
- EXPLAIN ANALYZE for query plan inspection
- Common pitfalls: N+1 queries, missing JOIN conditions
- Query caching strategies (application-level vs database-level)
- Connection pooling and prepared statements

### 4. Vector Database Specifics
- HNSW vs IVF indexing for Approximate Nearest Neighbor search
- Dimension reduction techniques (PCA, quantization)
- Metadata filtering combined with vector search
- Batch query optimization vs real-time latency trade-offs

### 5. Caching Patterns
- Redis/Memcached for hot data
- Cache invalidation strategies (TTL, write-through, write-back)
- Multi-tier caching: L1 (in-process), L2 (redis), L3 (database)
- Cache warming patterns for predictable queries

### 6. Migration and Versioning
- Database migration tools (Alembic, Flyway)
- Zero-downtime schema changes
- Data backfill and rollback procedures

## Implementation Notes
- Start with SQLite for prototyping, migrate to PostgreSQL for production
- Use connection pools appropriate for async workloads
- Monitor query performance with pg_stat_statements or equivalent
- Establish baseline metrics before and after optimization

## Exercises
1. Design schema for agent conversation memory with metadata filtering
2. Optimize a slow JOIN query using EXPLAIN ANALYZE
3. Implement caching layer for vector search results
4. Create migration script for adding new index to large table