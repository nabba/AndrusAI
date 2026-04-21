# Database Connectivity Patterns for Agent Teams

## Overview
This skill provides agents with patterns for database operations, enabling persistent structured storage, data analysis, and integration with external data sources.

## Built-in Option: SQLite via code_executor
SQLite is available without additional setup:

```python
import sqlite3
import json

# Create connection
conn = sqlite3.connect('/workspace/data/analysis.db')
cursor = conn.cursor()

# Create table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS research_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        source_url TEXT,
        content TEXT,
        metadata JSON,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Insert data
cursor.execute('''
    INSERT INTO research_findings (topic, source_url, content, metadata)
    VALUES (?, ?, ?, ?)
''', ('deforestation_policy', 'https://example.com', 'Key finding...', json.dumps({'confidence': 0.85}))

conn.commit()
conn.close()
```

## MCP Server Options

### PostgreSQL via Neon MCP
```
Server: neon
Query: database postgres mysql sqlite
Env Vars: NEON_API_KEY=xxx
```
Capabilities: Serverless Postgres, branching, migrations, SQL execution

### Supabase MCP
```
Server: Supabase
Query: database postgres mysql sqlite
Env Vars: SUPABASE_URL=xxx;SUPABASE_KEY=xxx
```
Capabilities: Postgres database, auth, storage, edge functions

### PlanetScale MCP
```
Server: planetscale
Query: database postgres mysql sqlite
Env Vars: PLANETSCALE_TOKEN=xxx
```
Capabilities: MySQL-compatible serverless, branching, insights

## Common Database Patterns

### Pattern 1: Research Data Persistence
```python
import sqlite3
import json
from datetime import datetime

def save_research(topic: str, findings: list, sources: list):
    conn = sqlite3.connect('/workspace/data/research.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS research_sessions (
            id INTEGER PRIMARY KEY,
            topic TEXT,
            findings TEXT,
            sources TEXT,
            created_at TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        INSERT INTO research_sessions (topic, findings, sources, created_at)
        VALUES (?, ?, ?, ?)
    ''', (topic, json.dumps(findings), json.dumps(sources), datetime.now()))
    
    conn.commit()
    return cursor.lastrowid
```

### Pattern 2: Analysis Results Cache
```python
def cache_analysis(analysis_type: str, input_hash: str, result: dict):
    conn = sqlite3.connect('/workspace/data/cache.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_cache (
            id INTEGER PRIMARY KEY,
            analysis_type TEXT,
            input_hash TEXT UNIQUE,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        INSERT OR REPLACE INTO analysis_cache (analysis_type, input_hash, result)
        VALUES (?, ?, ?)
    ''', (analysis_type, input_hash, json.dumps(result)))
    
    conn.commit()

def get_cached_analysis(analysis_type: str, input_hash: str) -> dict:
    conn = sqlite3.connect('/workspace/data/cache.db')
    cursor = conn.cursor()
    cursor.execute('SELECT result FROM analysis_cache WHERE analysis_type = ? AND input_hash = ?', 
                   (analysis_type, input_hash))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None
```

### Pattern 3: Knowledge Graph Storage
```python
def create_knowledge_graph():
    conn = sqlite3.connect('/workspace/data/knowledge.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            type TEXT,
            attributes TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            relation_type TEXT,
            weight REAL,
            FOREIGN KEY (source_id) REFERENCES entities(id),
            FOREIGN KEY (target_id) REFERENCES entities(id)
        )
    ''')
    
    conn.commit()
```

### Pattern 4: Time-Series Metrics
```python
def log_metrics(metric_name: str, value: float, tags: dict = None):
    conn = sqlite3.connect('/workspace/data/metrics.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY,
            metric_name TEXT,
            value REAL,
            tags TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        INSERT INTO metrics (metric_name, value, tags)
        VALUES (?, ?, ?)
    ''', (metric_name, value, json.dumps(tags or {})))
    
    conn.commit()
```

## Query Patterns

### Basic Queries
```python
# Simple select
cursor.execute('SELECT * FROM table WHERE column = ?', (value,))

# Aggregation
cursor.execute('SELECT COUNT(*), AVG(value) FROM table GROUP BY category')

# Join
cursor.execute('''
    SELECT a.name, b.value
    FROM table_a a
    JOIN table_b b ON a.id = b.a_id
    WHERE a.active = 1
''')
```

### Full-Text Search
```python
cursor.execute('''
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title, content
    )
''')

cursor.execute('''
    SELECT title, snippet(documents_fts, 1, '>>>', '<<<', '...', 10)
    FROM documents_fts
    WHERE documents_fts MATCH ?
    ORDER BY rank
''', ('search term',))
```

## Data Export Patterns

```python
def export_to_csv(table: str, output_path: str):
    import csv
    conn = sqlite3.connect('/workspace/data/analysis.db')
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {table}')
    
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([desc[0] for desc in cursor.description])
        writer.writerows(cursor.fetchall())

def export_to_json(table: str, output_path: str):
    conn = sqlite3.connect('/workspace/data/analysis.db')
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {table}')
    columns = [desc[0] for desc in cursor.description]
    data = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
```

## Crew-Specific Applications

### Research Crew
- Store scraped data for deduplication
- Cache API responses
- Build searchable document corpus

### Coding Crew
- Query databases in analysis scripts
- Store test results and benchmarks
- Log application metrics

### Writing Crew
- Track content versions
- Store style guides and templates
- Build reference databases

### Self-Improvement Crew
- Log agent performance metrics
- Store improvement proposals
- Track capability changes over time

## Best Practices
1. Always use parameterized queries to prevent SQL injection
2. Close connections after operations
3. Use transactions for multi-step operations
4. Index frequently queried columns
5. Regular VACUUM and ANALYZE for SQLite
6. Use appropriate data types (don't store numbers as text)
7. Implement error handling for database operations