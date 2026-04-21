# Structured Data Persistence for Research Tasks

## Problem
The research crew gathers valuable data but has no structured persistence mechanism:
- Data stored in flat files is hard to query
- Cross-task knowledge cannot be accumulated
- Previous research is difficult to reference
- No efficient way to build on past findings

## Solution: SQLite-Based Research Database

### Schema Design

```sql
-- Research sessions table
CREATE TABLE IF NOT EXISTS research_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE,
    query TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'active',
    crew TEXT DEFAULT 'research'
);

-- Findings table (stores structured research outputs)
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    source_url TEXT,
    source_title TEXT,
    finding_type TEXT,  -- 'fact', 'statistic', 'quote', 'summary', 'entity'
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
);

-- Entities extracted from research
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT,  -- 'person', 'organization', 'location', 'date', 'concept'
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    mention_count INTEGER DEFAULT 1,
    UNIQUE(name, entity_type)
);

-- Entity-Finding relationships
CREATE TABLE IF NOT EXISTS entity_findings (
    finding_id INTEGER,
    entity_id INTEGER,
    relationship TEXT,  -- 'mentioned', 'authored', 'located_in', 'occurred_on'
    PRIMARY KEY (finding_id, entity_id),
    FOREIGN KEY (finding_id) REFERENCES findings(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
    content,
    content='findings',
    content_rowid='id'
);

-- Source credibility tracking
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    domain TEXT,
    credibility_score REAL DEFAULT 0.5,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 1
);
```

### Usage Pattern in Research Crew

```python
import sqlite3
import json
from datetime import datetime
from pathlib import Path

class ResearchDatabase:
    def __init__(self, db_path: str = 'workspace/research.db'):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
    
    def start_session(self, task_id: str, query: str) -> int:
        """Start a new research session."""
        cursor = self.conn.execute(
            'INSERT OR REPLACE INTO research_sessions (task_id, query) VALUES (?, ?)',
            (task_id, query)
        )
        self.conn.commit()
        return cursor.lastrowid
    
    def add_finding(self, session_id: int, source_url: str, content: str,
                    finding_type: str = 'fact', metadata: dict = None) -> int:
        """Store a research finding."""
        cursor = self.conn.execute(
            '''INSERT INTO findings 
               (session_id, source_url, content, finding_type, metadata)
               VALUES (?, ?, ?, ?, ?)''',
            (session_id, source_url, content, finding_type, json.dumps(metadata or {}))
        )
        self.conn.commit()
        finding_id = cursor.lastrowid
        
        # Update FTS index
        self.conn.execute(
            'INSERT INTO findings_fts (rowid, content) VALUES (?, ?)',
            (finding_id, content)
        )
        self.conn.commit()
        
        return finding_id
    
    def add_entity(self, name: str, entity_type: str) -> int:
        """Add or update an entity."""
        self.conn.execute(
            '''INSERT INTO entities (name, entity_type) VALUES (?, ?)
               ON CONFLICT(name, entity_type) DO UPDATE SET
               mention_count = mention_count + 1,
               last_updated = CURRENT_TIMESTAMP''',
            (name, entity_type)
        )
        self.conn.commit()
        result = self.conn.execute(
            'SELECT id FROM entities WHERE name = ? AND entity_type = ?',
            (name, entity_type)
        ).fetchone()
        return result['id']
    
    def search_findings(self, query: str, limit: int = 10) -> list:
        """Full-text search across findings."""
        results = self.conn.execute(
            '''SELECT f.*, s.credibility_score
               FROM findings_fts ft
               JOIN findings f ON ft.rowid = f.id
               LEFT JOIN sources s ON f.source_url = s.url
               WHERE findings_fts MATCH ?
               ORDER BY bm25(findings_fts) DESC
               LIMIT ?''',
            (query, limit)
        ).fetchall()
        return [dict(r) for r in results]
    
    def get_session_findings(self, session_id: int) -> list:
        """Get all findings for a research session."""
        results = self.conn.execute(
            'SELECT * FROM findings WHERE session_id = ? ORDER BY extracted_at',
            (session_id,)
        ).fetchall()
        return [dict(r) for r in results]
    
    def complete_session(self, session_id: int):
        """Mark a research session as complete."""
        self.conn.execute(
            'UPDATE research_sessions SET completed_at = CURRENT_TIMESTAMP, status = ? WHERE id = ?',
            ('completed', session_id)
        )
        self.conn.commit()

# Usage in research workflow
# db = ResearchDatabase()
# session_id = db.start_session(task_id='task_123', query='Estonian deforestation rates')
# db.add_finding(session_id, 'https://example.com', 'Estonia lost 2.3% forest cover in 2024')
# db.add_entity('Estonia', 'location')
# db.add_entity('2024', 'date')
```

### Integration Points

1. **Research Crew Start**: Initialize session with task_id
2. **Web Search Results**: Store each source as a finding
3. **Entity Extraction**: Track all mentioned entities
4. **Cross-Session Queries**: Build on past research
5. **Credibility Tracking**: Score sources over time

### Benefits

- **Cumulative Knowledge**: Each research task adds to the knowledge base
- **Efficient Queries**: FTS5 for fast text search
- **Entity Tracking**: Build knowledge graphs over time
- **Source Credibility**: Track reliability of sources
- **Audit Trail**: Complete history of research activities

### Best Practices

1. Always call `start_session()` at research task start
2. Store findings immediately after extraction
3. Extract and store entities from each finding
4. Use `search_findings()` before new research to avoid duplication
5. Call `complete_session()` when research is done

### File Location
```
workspace/
├── research.db          # SQLite database
├── research.db.backup   # Periodic backups
└── exports/             # JSON exports for sharing
```
