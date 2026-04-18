# Database Persistence Strategy for Ecological Research

## Problem Statement
The team currently has:
- ❌ No persistent storage across sessions
- ❌ No structured data schema for ecological observations
- ❌ No version tracking for research outputs
- ❌ No shared knowledge base for team collaboration

## Impact Without Persistence
- Duplicate data collection across sessions
- Lost stakeholder engagement history
- No longitudinal ecological trend analysis
- Cannot build cumulative knowledge repositories
- Reproducibility impossible for rapid assessments

## Core Strategy
**Dual Storage Architecture:**
1. **SQLite** for structured relational data (observations, stakeholders, metadata).
2. **Vector Store** (via in-memory FAISS with optional persistence) for semantic search over accumulated notes.

## Implementation Stack
- SQLAlchemy (ORM) for database abstraction
- SQLite for zero-config, file-based persistence
- FAISS for approximate nearest neighbor search
- Pydantic for data validation schemas
- Alembic for schema migrations

## Essential Schemas
### 1. `ecological_observations`
```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    location TEXT,
    ecosystem_type TEXT,
    species_observed TEXT,
    abundance INTEGER,
    health_status TEXT,
    environmental_conditions JSON,
    data_source TEXT,
    confidence_score FLOAT,
    researcher_id TEXT,
    rapid_assessment_tag TEXT
);
```

### 2. `stakeholder_engagements`
```sql
CREATE TABLE stakeholder_engagements (
    id INTEGER PRIMARY KEY,
    stakeholder_name TEXT NOT NULL,
    organization TEXT,
    role TEXT,
    engagement_date DATE,
    concerns TEXT,
    priorities TEXT,
    commitments TEXT,
    followup_required BOOLEAN,
    relationship_strength FLOAT,
    tags TEXT
);
```

### 3. `policy_impact_assessments`
```sql
CREATE TABLE policy_assessments (
    id INTEGER PRIMARY KEY,
    policy_name TEXT NOT NULL,
    jurisdiction TEXT,
    effective_date DATE,
    ecia_score FLOAT,
    ecological_benefits TEXT,
    tradeoffs TEXT,
    stakeholder_comments TEXT,
    reviewed_by TEXT,
    last_updated DATETIME
);
```

### 4. `rapid_assessment_reports`
```sql
CREATE TABLE assessment_reports (
    id INTEGER PRIMARY KEY,
    report_type TEXT,
    generated_date DATETIME,
    location TEXT,
    summary TEXT,
    key_findings TEXT,
    recommendations TEXT,
    confidence_level FLOAT,
    sources_cited TEXT,
    reviewer TEXT,
    archived BOOLEAN DEFAULT FALSE
);
```

## Vector Knowledge Base Schema
Each document chunk stored as:
```python
{
    "id": str,
    "text": str,
    "embedding": np.ndarray,
    "metadata": {
        "source": "rapid_assessment/crisis_communication/stakeholder_...",
        "timestamp": str,
        "tags": List[str]
    }
}
```

## Persistence Patterns
### Pattern 1: Checkpoint-After-Research
- After each research/web search, store results with metadata.
- Enable reproducibility: rerun analysis with identical inputs.

### Pattern 2: Knowledge Accumulation
- Semantic search across ALL prior findings.
- "Find similar ecological conflicts from past cases"
- Cumulative learning: vector store grows over time.

### Pattern 3: Session Recovery
- Load last session state on restart.
- Resume interrupted assessments.

## Migration & Backup Strategy
- Alembic migrations track schema changes.
- Daily SQLite backups to `/data/backups/`.
- Export critical tables to CSV quarterly.
- Retain vector index snapshots monthly.

## Performance Optimization
- SQLite: Index on `timestamp`, `location`, `ecosystem_type`.
- FAISS: HNSW index with `ef_construction=200`, `ef_search=50`.
- Batch inserts for bulk data collection.
- Connection pooling for concurrent research crews.

## Configuration
```yaml
database:
  sqlite_path: "./data/ecological_research.db"
  vector_store_path: "./data/vector_index.faiss"
  backup:
    interval: "daily"
    retention_days: 90
  connection:
    pool_size: 5
    max_overflow: 10
```

## Success Metrics
- % of research outputs persisted (Target: 100%)
- Retrieval latency for semantic search (<2s)
- Storage growth rate monitored (alert if >10GB/month)
- Schema migration success rate (Target: 100%)

## Next Steps
1. Implement core SQLite models with SQLAlchemy.
2. Build FAISS wrapper with metadata filtering.
3. Create `research_crew` hooks to auto-store findings.
4. Design data export utilities (CSV, JSON, GeoPackage).

---

*This foundational skill enables reproducibility, longitudinal analysis, and cumulative knowledge building across all ecological research workflows.*