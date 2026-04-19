# Proposal #664: Task Router Guard: Prevent Non-Research Queries from Failing

**Type:** skill  
**Created:** 2026-04-19T12:49:58.808167+00:00  

## Why this is useful

Problem: Simple meta-questions ('What time is it?', 'What is your name?', 'What is your purpose?') and future-prediction queries are routed to the research crew, which attempts web searches and returns empty/failed outputs. This wastes time and produces poor user experience. Solution: A routing guard skill that teaches the system to identify query types that should be answered directly by the orchestrator (meta/identity questions, time queries, greetings) rather than delegated to specialist crews, and to handle speculative/future queries by reframing them as trend analysis.

## What will change

- Modifies `skills/task_routing_guard.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/task_routing_guard.md`

## Original description

Problem: Simple meta-questions ('What time is it?', 'What is your name?', 'What is your purpose?') and future-prediction queries are routed to the research crew, which attempts web searches and returns empty/failed outputs. This wastes time and produces poor user experience. Solution: A routing guard skill that teaches the system to identify query types that should be answered directly by the orchestrator (meta/identity questions, time queries, greetings) rather than delegated to specialist crews, and to handle speculative/future queries by reframing them as trend analysis.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 664` / `reject 664` via Signal.
