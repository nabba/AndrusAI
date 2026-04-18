# Proposal #636: Research Crew Reliability Guard - Fallback and Retry Logic

**Type:** code
**Created:** 2026-04-18T21:14:24.046734+00:00

## Description

PROBLEM: The research crew fails with empty output on simple factual queries (e.g., 'What is the capital of Estonia?', 'What programming language is most popular in 2026?') — over 60% of low-difficulty research tasks produce empty/failed results. This makes the team unreliable for basic information retrieval. SOLUTION: Add a research output validator and retry wrapper that (1) detects empty/failed outputs, (2) retries with reformulated queries up to 2 times, and (3) falls back to a direct LLM answer for simple factual questions that don't require web search. This dramatically improves reliability on the most common query types.

## Files

- `skills/research_reliability_guard.md`
