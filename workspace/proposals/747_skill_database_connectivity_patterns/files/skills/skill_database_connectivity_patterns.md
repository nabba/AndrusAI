# Database Connectivity Patterns

## Problem
The team lacks database connectivity:
- Cannot query SQL databases
- Cannot persist structured data
- Cannot integrate with existing application databases
- Limited data analysis capabilities for large datasets

## Solution Options

### Option 1: Python Libraries via code_executor

#### SQLite (Built-in, No Dependencies)
```python
import sqlite3

# Create/connect to database
conn = sqlite3.connect('/app/workspace/data.db')
cursor = conn.cursor()

# Create table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Insert data
cursor.execute('INSERT INTO users (name, email) VALUES (?, ?)', ('Alice', 'alice@example.com'))
conn.commit()

# Query data
cursor.execute('SELECT * FROM users WHERE name = ?', ('Alice',))
results = cursor.fetchall()
print(results)

conn.close()
```

#### PostgreSQL (psycopg2)
```python
# Requires: pip install psycopg2-binary
import psycopg2

conn = psycopg2.connect(
    host='localhost',
    database='mydb',
    user='user',
    password='password'
)
cursor = conn.cursor()
cursor.execute('SELECT version()')
print(cursor.fetchone())
conn.close()
```

#### MySQL (mysql-connector-python)
```python
# Requires: pip install mysql-connector-python
import mysql.connector

conn = mysql.connector.connect(
    host='localhost',
    database='mydb',
    user='user',
    password='password'
)
cursor = conn.cursor()
cursor.execute('SELECT * FROM table')
for row in cursor:
    print(row)
conn.close()
```

### Option 2: MCP Database Servers

The team can connect to managed database MCP servers:

#### Supabase MCP (6,810+ installations)
```
mcp_add_server:
  name: 'Supabase'
  query: 'database postgres mysql'
  env_vars: 'SUPABASE_URL=your_url;SUPABASE_KEY=your_key'
```
Provides: SQL execution, schema migrations, real-time subscriptions

#### Neon MCP (305+ installations)
```
mcp_add_server:
  name: 'neon'
  query: 'database postgres mysql'
  env_vars: 'NEON_API_KEY=your_key'
```
Provides: Serverless PostgreSQL, branching, safe migrations

#### PlanetScale MCP
```
mcp_add_server:
  name: 'planetscale'
  query: 'database postgres mysql'
  env_vars: 'PLANETSCALE_TOKEN=your_token'
```
Provides: MySQL-compatible serverless, branch-based workflows

### Best Practices

#### 1. Use Context Managers
```python
import sqlite3

with sqlite3.connect('/app/workspace/data.db') as conn:
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    results = cursor.fetchall()
# Auto-closes connection
```

#### 2. Parameterized Queries (Prevent SQL Injection)
```python
# SAFE - parameterized
cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))

# UNSAFE - string formatting (NEVER do this)
cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')  # SQL injection risk!
```

#### 3. Batch Operations
```python
users = [('Alice', 'alice@example.com'), ('Bob', 'bob@example.com')]
cursor.executemany('INSERT INTO users (name, email) VALUES (?, ?)', users)
conn.commit()
```

#### 4. Transaction Management
```python
conn = sqlite3.connect('data.db')
try:
    cursor = conn.cursor()
    cursor.execute('UPDATE accounts SET balance = balance - 100 WHERE id = 1')
    cursor.execute('UPDATE accounts SET balance = balance + 100 WHERE id = 2')
    conn.commit()  # Commit both or neither
except Exception as e:
    conn.rollback()  # Undo partial changes
    raise
```

## Decision Matrix

| Scenario | Solution |
|----------|----------|
| Local data persistence | SQLite via code_executor |
| Cloud PostgreSQL | Neon or Supabase MCP |
| Cloud MySQL | PlanetScale MCP |
| Existing database integration | Python library in code_executor |
| Schema migrations | Neon MCP (branch-based) |

## Dependencies
- SQLite: Built into Python (no install needed)
- PostgreSQL: `pip install psycopg2-binary`
- MySQL: `pip install mysql-connector-python`