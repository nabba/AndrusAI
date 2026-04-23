# Proposal #762: Add Essential MCP Servers for Production Workflows

**Type:** code  
**Created:** 2026-04-22T20:15:04.971172+00:00  

## Why this is useful

Problem: Team has zero MCP connectivity despite having skills in databases (vector DB optimization, PostgreSQL patterns), version control workflows, and data-intensive research. No way to persist data, integrate with GitHub repos, or access external services. Solution: Connect Supabase (PostgreSQL + Edge Functions), GitHub, and filesystem MCP servers. These enable: structured data storage for research findings, GitHub integration for code/version collaboration, and persistent file operations beyond the limited file_manager.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Problem: Team has zero MCP connectivity despite having skills in databases (vector DB optimization, PostgreSQL patterns), version control workflows, and data-intensive research. No way to persist data, integrate with GitHub repos, or access external services. Solution: Connect Supabase (PostgreSQL + Edge Functions), GitHub, and filesystem MCP servers. These enable: structured data storage for research findings, GitHub integration for code/version collaboration, and persistent file operations beyond the limited file_manager.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 762` / `reject 762` via Signal.
