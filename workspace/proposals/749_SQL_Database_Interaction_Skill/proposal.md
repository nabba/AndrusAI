# Proposal #749: SQL Database Interaction Skill

**Type:** skill  
**Created:** 2026-04-21T16:27:05.592816+00:00  

## Why this is useful

Problem: The team has no database connectivity. Tasks requiring SQL queries, data analysis from relational databases, or database migrations would fail. The code_executor can run Python but cannot connect to external PostgreSQL/MySQL databases. Solution: A skill document teaching agents how to use Python libraries (sqlite3, psycopg2, sqlalchemy) with the code_executor for database operations, including connection string patterns, safe query construction, and result parsing.

## What will change

- Modifies `skills/sql_database_operations.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/sql_database_operations.md`

## Original description

Problem: The team has no database connectivity. Tasks requiring SQL queries, data analysis from relational databases, or database migrations would fail. The code_executor can run Python but cannot connect to external PostgreSQL/MySQL databases. Solution: A skill document teaching agents how to use Python libraries (sqlite3, psycopg2, sqlalchemy) with the code_executor for database operations, including connection string patterns, safe query construction, and result parsing.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 749` / `reject 749` via Signal.
