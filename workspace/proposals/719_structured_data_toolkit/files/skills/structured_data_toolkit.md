# Structured Data Toolkit

## Purpose
Enable agents to work with structured data sources: SQL databases, CSV/Excel files, and data transformations using Python libraries.

## Core Capabilities

### 1. SQLite Database Operations
```python
import sqlite3
import json

# Create in-memory database
db = sqlite3.connect(':memory:')
cursor = db.cursor()

# Create table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS research_data (
        id INTEGER PRIMARY KEY,
        title TEXT,
        source TEXT,
        date_collected TEXT,
        data_json TEXT
    )
''')

# Insert data
cursor.execute('''
    INSERT INTO research_data (title, source, date_collected, data_json)
    VALUES (?, ?, ?, ?)
''', ('Estonian Forest Report', 'EPA', '2024-01-15', json.dumps({'pages': 45})))

db.commit()

# Query data
cursor.execute('SELECT * FROM research_data WHERE source = ?', ('EPA',))
results = cursor.fetchall()
print(results)
db.close()
```

### 2. CSV Processing
```python
import csv
from io import StringIO

# Parse CSV data
csv_data = '''name,value,category
Alpha,100,A
Beta,200,B
Gamma,150,A'''

reader = csv.DictReader(StringIO(csv_data))
data = [row for row in reader]

# Filter and aggregate
a_category = [int(row['value']) for row in data if row['category'] == 'A']
print(f"Category A total: {sum(a_category)}")

# Write CSV
output = StringIO()
writer = csv.DictWriter(output, fieldnames=['name', 'value', 'category'])
writer.writeheader()
writer.writerows(data)
print(output.getvalue())
```

### 3. JSON Data Handling
```python
import json

# Parse and query JSON
json_data = '''{
    "records": [
        {"id": 1, "type": "policy", "year": 2023},
        {"id": 2, "type": "report", "year": 2024}
    ]
}'''

data = json.loads(json_data)
recent = [r for r in data['records'] if r['year'] >= 2024]
print(json.dumps(recent, indent=2))
```

### 4. Data Analysis with Pandas
```python
import pandas as pd
from io import StringIO

# Create DataFrame
data = '''date,value,category
2024-01-01,100,A
2024-01-02,150,A
2024-01-03,120,B
2024-01-04,180,B'''

df = pd.read_csv(StringIO(data))

# Group and aggregate
summary = df.groupby('category').agg({
    'value': ['sum', 'mean', 'count']
})
print(summary)

# Filter and transform
filtered = df[df['value'] > 130].copy()
filtered['value_normalized'] = (filtered['value'] - filtered['value'].min()) / (filtered['value'].max() - filtered['value'].min())
print(filtered)
```

### 5. Data Transformation Patterns
```python
# Pivot tables
pivot = df.pivot_table(
    values='value',
    index='date',
    columns='category',
    aggfunc='sum'
)

# Time series processing
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date')
df['rolling_avg'] = df['value'].rolling(window=2).mean()

# Merge datasets
df1 = pd.DataFrame({'id': [1, 2], 'name': ['A', 'B']})
df2 = pd.DataFrame({'id': [1, 2], 'value': [100, 200]})
merged = pd.merge(df1, df2, on='id')
```

### 6. Statistical Analysis
```python
import statistics

values = [100, 150, 120, 180, 200, 95, 170]

stats = {
    'mean': statistics.mean(values),
    'median': statistics.median(values),
    'stdev': statistics.stdev(values) if len(values) > 1 else 0,
    'min': min(values),
    'max': max(values)
}
print(stats)
```

## Database Connection Patterns

### PostgreSQL (if psycopg2 available)
```python
# Note: Requires psycopg2-binary installed
import psycopg2

# conn = psycopg2.connect(
#     host='localhost',
#     database='mydb',
#     user='user',
#     password='pass'
# )
# cursor = conn.cursor()
# cursor.execute('SELECT * FROM table LIMIT 10')
```

## Best Practices

1. **Use context managers** - `with sqlite3.connect() as db:`
2. **Parameterize queries** - Prevent SQL injection with `?` placeholders
3. **Handle large datasets in chunks** - Use iterators for memory efficiency
4. **Validate data types** - Check before transformations
5. **Cache frequently accessed data** - Store in SQLite for repeated queries

## Common Use Cases

- Store and query scraped research data
- Aggregate and analyze multi-source datasets
- Transform data between formats (CSV → JSON → SQL)
- Generate summary statistics for reports
- Deduplicate and clean datasets
