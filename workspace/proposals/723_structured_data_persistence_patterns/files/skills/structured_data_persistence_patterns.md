# Structured Data Persistence Patterns

## Overview
This skill provides patterns for persisting and querying structured data using SQLite and file-based storage within the code_executor environment.

## SQLite Database Patterns

### Basic Database Setup
```python
import sqlite3
import os

# Create database in workspace
db_path = '/app/workspace/data/research.db'
os.makedirs(os.path.dirname(db_path), exist_ok=True)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
```

### Create Tables
```python
# Research documents table
cursor.execute('''
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_url TEXT,
    content TEXT,
    metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Key findings table
cursor.execute('''
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    finding_type TEXT,
    content TEXT,
    confidence REAL,
    FOREIGN KEY (document_id) REFERENCES documents(id)
)
''')

# Create indexes for common queries
cursor.execute('CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created_at)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_findings_type ON findings(finding_type)')

conn.commit()
```

### Insert Data
```python
import json
from datetime import datetime

# Insert document
cursor.execute('''
INSERT INTO documents (title, source_url, content, metadata)
VALUES (?, ?, ?, ?)
''', (
    'Estonian Deforestation Policy 2023',
    'https://example.com/policy.pdf',
    'Full document text...',
    json.dumps({'author': 'Ministry of Environment', 'pages': 45})
))

doc_id = cursor.lastrowid

# Insert findings
cursor.execute('''
INSERT INTO findings (document_id, finding_type, content, confidence)
VALUES (?, ?, ?, ?)
''', (doc_id, 'policy_change', 'New logging restrictions announced', 0.95))

conn.commit()
```

### Query Patterns
```python
# Simple query
cursor.execute('SELECT * FROM documents WHERE title LIKE ?', ('%deforestation%',))
results = cursor.fetchall()

# Join query
cursor.execute('''
SELECT d.title, f.finding_type, f.content
FROM documents d
JOIN findings f ON d.id = f.document_id
WHERE f.confidence > 0.8
ORDER BY d.created_at DESC
''')

# Aggregate query
cursor.execute('''
SELECT finding_type, COUNT(*) as count, AVG(confidence) as avg_conf
FROM findings
GROUP BY finding_type
ORDER BY count DESC
''')

stats = cursor.fetchall()
for row in stats:
    print(f"{row[0]}: {row[1]} findings, avg confidence: {row[2]:.2f}")
```

### Full-Text Search
```python
# Create FTS table
cursor.execute('''
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, content, source_url
)
''')

# Populate FTS
cursor.execute('''
INSERT INTO documents_fts (title, content, source_url)
SELECT title, content, source_url FROM documents
''')

# Search
cursor.execute('''
SELECT title, snippet(documents_fts, 1, '>>>', '<<<', '...', 20) as snippet
FROM documents_fts
WHERE documents_fts MATCH ?
ORDER BY rank
''', ('deforestation AND policy',))

for row in cursor.fetchall():
    print(f"{row[0]}\n  {row[1]}\n")
```

## JSON File Storage Patterns

### Structured JSON Storage
```python
import json
from datetime import datetime

class JSONStore:
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = self._load()
    
    def _load(self):
        try:
            with open(self.filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'records': [], 'metadata': {}}
    
    def save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.data, f, indent=2, default=str)
    
    def add(self, record):
        record['_id'] = len(self.data['records']) + 1
        record['_timestamp'] = datetime.now().isoformat()
        self.data['records'].append(record)
        self.save()
        return record['_id']
    
    def query(self, filter_func=None):
        if filter_func:
            return [r for r in self.data['records'] if filter_func(r)]
        return self.data['records']

# Usage
store = JSONStore('/app/workspace/data/research_results.json')
store.add({'topic': 'deforestation', 'findings': ['finding1', 'finding2']})
results = store.query(lambda r: 'forest' in r.get('topic', ''))
```

## CSV Data Management

### Large Dataset Handling
```python
import csv

class CSVStore:
    def __init__(self, filepath, fieldnames=None):
        self.filepath = filepath
        self.fieldnames = fieldnames
    
    def append(self, row):
        file_exists = os.path.exists(self.filepath)
        
        with open(self.filepath, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames or row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    
    def query(self, filter_func=None):
        results = []
        with open(self.filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if filter_func is None or filter_func(row):
                    results.append(row)
        return results
    
    def count(self):
        with open(self.filepath, 'r') as f:
            return sum(1 for _ in f) - 1  # Subtract header

# Usage
csv_store = CSVStore('/app/workspace/data/analysis.csv', 
                     fieldnames=['date', 'topic', 'score', 'notes'])
csv_store.append({'date': '2024-01-15', 'topic': 'policy', 'score': 0.85, 'notes': ''})
high_scores = csv_store.query(lambda r: float(r['score']) > 0.8)
```

## Data Export Patterns

### Export to Multiple Formats
```python
def export_research_data(db_path, output_dir):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Export to JSON
    cursor.execute('SELECT * FROM documents')
    docs = [{'id': r[0], 'title': r[1], 'url': r[2], 'content': r[3]} 
            for r in cursor.fetchall()]
    
    with open(f'{output_dir}/documents.json', 'w') as f:
        json.dump(docs, f, indent=2)
    
    # Export to CSV
    cursor.execute('SELECT id, title, created_at FROM documents')
    with open(f'{output_dir}/document_index.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'title', 'created_at'])
        writer.writerows(cursor.fetchall())
    
    conn.close()
    return f'Exported {len(docs)} documents'
```

## Best Practices

1. **Always use parameterized queries** to prevent SQL injection
2. **Create indexes** for frequently queried columns
3. **Use transactions** for batch operations:
   ```python
   with conn:
       cursor.executemany('INSERT INTO docs VALUES (?, ?)', data_list)
   ```
4. **Backup before schema changes**:
   ```python
   import shutil
   shutil.copy(db_path, f'{db_path}.backup')
   ```
5. **Close connections** when done: `conn.close()`
6. **Use context managers** for automatic cleanup:
   ```python
   with sqlite3.connect(db_path) as conn:
       cursor = conn.cursor()
       # operations here
   ```

## Common Workflows

### Research Project Database Setup
```python
def setup_research_db(project_name):
    db_path = f'/app/workspace/projects/{project_name}/research.db'
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Standard schema for research projects
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            type TEXT,
            url TEXT,
            title TEXT,
            accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS extractions (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            data_type TEXT,
            content TEXT,
            metadata JSON,
            FOREIGN KEY (source_id) REFERENCES sources(id)
        );
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY,
            extraction_id INTEGER,
            analysis_type TEXT,
            result TEXT,
            confidence REAL,
            FOREIGN KEY (extraction_id) REFERENCES extractions(id)
        );
    ''')
    
    conn.commit()
    conn.close()
    return db_path
```

### Incremental Data Collection
```python
def save_research_incremental(db_path, source_url, content, findings):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if already processed
    cursor.execute('SELECT id FROM sources WHERE url = ?', (source_url,))
    if cursor.fetchone():
        return None  # Already exists
    
    # Insert new
    cursor.execute('INSERT INTO sources (url, type) VALUES (?, ?)', 
                   (source_url, 'web'))
    source_id = cursor.lastrowid
    
    cursor.execute('INSERT INTO extractions (source_id, content) VALUES (?, ?)',
                   (source_id, content))
    
    for finding in findings:
        cursor.execute('INSERT INTO analysis (extraction_id, result, confidence) VALUES (?,?,?)',
                       (cursor.lastrowid, finding['text'], finding.get('confidence', 0.5)))
    
    conn.commit()
    conn.close()
    return source_id
```
