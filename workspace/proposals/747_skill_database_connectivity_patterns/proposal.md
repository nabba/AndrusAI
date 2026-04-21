# Proposal #747: skill_database_connectivity_patterns

**Type:** skill  
**Created:** 2026-04-21T15:55:43.319519+00:00  

## Why this is useful

PROBLEM: The team has no database connectivity tool. Tasks requiring SQL queries, data persistence, or integration with existing databases will fail. Many real-world applications require database access (PostgreSQL, MySQL, SQLite). SOLUTION: Create a skill documenting database connectivity patterns using Python libraries (sqlite3 built-in, psycopg2 for PostgreSQL, mysql-connector) within code_executor, plus reference to available MCP database servers (Supabase, Neon, PlanetScale).

## What will change

- Modifies `skills/skill_database_connectivity_patterns.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/skill_database_connectivity_patterns.md`

## Original description

PROBLEM: The team has no database connectivity tool. Tasks requiring SQL queries, data persistence, or integration with existing databases will fail. Many real-world applications require database access (PostgreSQL, MySQL, SQLite). SOLUTION: Create a skill documenting database connectivity patterns using Python libraries (sqlite3 built-in, psycopg2 for PostgreSQL, mysql-connector) within code_executor, plus reference to available MCP database servers (Supabase, Neon, PlanetScale).

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 747` / `reject 747` via Signal.
