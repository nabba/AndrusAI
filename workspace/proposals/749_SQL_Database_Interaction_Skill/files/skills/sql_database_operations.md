# SQL Database Operations

## When to Use
Any task requiring: querying relational databases, data extraction for analysis, database schema inspection, or data migration planning.

## Prerequisites
- `code_executor` tool available
- Database connection credentials (host, port, database, user, password)

## Procedure

### 1. SQLite (Local/Embedded)
```python
import sqlite3
conn = sqlite3.connect(':memory:')  # or path to .db file
cursor = conn.cursor()
cursor.execute('SELECT * FROM table_name')
results = cursor.fetchall()
conn.close()
```

### 2. PostgreSQL via psycopg2
```python
import psycopg2
conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='mydb',
    user='user',
    password='pass'
)
cursor = conn.cursor()
cursor.execute('SELECT version()')
print(cursor.fetchone())
conn.close()
```

### 3. SQLAlchemy (ORM + Cross-Database)
```python
from sqlalchemy import create_engine, text
engine = create_engine('postgresql://user:pass@localhost/mydb')
with engine.connect() as conn:
    result = conn.execute(text('SELECT * FROM users'))
    for row in result:
        print(row)
```

## Safety Patterns
- Always use parameterized queries: `cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))`
- Never interpolate strings into SQL
- Close connections in `finally` blocks or use context managers
- Store credentials in environment variables, never hardcode

## Common Patterns
- Data extraction: Use pandas `read_sql()` for analysis
- Bulk inserts: Use `executemany()` for efficiency
- Schema inspection: Query `information_schema` tables

## Limitations
- Remote database connections require network access from code_executor
- Credentials must be provided by user or stored securely