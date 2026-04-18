# Proposal #634: Resilient Research Crew with Retry and Fallback Logic

**Type:** skill
**Created:** 2026-04-18T20:15:41.237250+00:00

## Description

Problem: The research crew frequently returns empty or failed output (observed on multiple tasks like 'What programming language is most popular in 2026?' and 'What is your purpose?'). These failures cascade to the user as blank responses with no recovery attempt. Solution: A skill document that teaches the research crew a structured retry-and-fallback workflow: (1) retry the same query with rephrased search terms, (2) try alternative search strategies (broader/narrower queries), (3) if web search fails, attempt to answer from internal knowledge, (4) always return a meaningful response even if it's 'I could not find current data, but here is what I know...'

## Files

- `skills/resilient_research_retry_fallback.md`
