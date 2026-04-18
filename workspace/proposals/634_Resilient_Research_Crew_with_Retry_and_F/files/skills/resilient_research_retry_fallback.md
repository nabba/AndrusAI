# Resilient Research: Retry and Fallback Strategies

## Problem
Research tasks sometimes return empty or failed output. This is unacceptable — the user should always receive a meaningful response.

## Retry Protocol
When a web search returns no useful results:

### Step 1: Rephrase and Retry (up to 2 retries)
- Broaden the query: remove specific dates, version numbers, or niche terms
- Narrow the query: add domain-specific keywords or site filters
- Try synonyms or alternative phrasings
- Example: "most popular programming language 2026" → "programming language popularity rankings" → "Stack Overflow developer survey 2025 2026"

### Step 2: Try Alternative Sources
- If web_search fails, try web_fetch on known authoritative URLs:
  - Stack Overflow surveys: https://survey.stackoverflow.co/
  - Wikipedia for factual lookups
  - Official project/product websites
- Use browser_fetch for JavaScript-heavy pages that web_fetch can't parse

### Step 3: Graceful Degradation
- If all web sources fail, synthesize an answer from internal knowledge
- Clearly state the limitation: "Based on my training data (not live web results)..."
- Never return an empty response

### Step 4: Report the Issue
- Log failed searches to team memory for pattern analysis
- Include the query, number of retries, and failure reason

## Anti-Patterns to Avoid
- Returning empty/blank output without explanation
- Giving up after a single failed search
- Not attempting to rephrase queries
- Treating "no web results" as "no answer possible"

## Simple Questions Protocol
For simple questions like "what time is it?" or "what is your purpose?":
- These do NOT require web search
- Answer directly from knowledge/capabilities
- Do not waste time on unnecessary web searches for self-referential questions