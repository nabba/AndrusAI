# Proposal #681: Simplify Research Agent Tool Configuration

**Type:** code  
**Created:** 2026-04-20T06:05:15.431023+00:00  

## Why this is useful

Diagnosis: The research agent's tool set includes too many tools with complex schemas, exceeding the LLM provider's limits for function calling compilation.

Fix: Modify the agent initialization code to reduce the number of tools and simplify their parameter schemas. Specifically, for the research task agent, remove non-essential tools (e.g., keep only web search, data analysis, and document writing) and refactor custom tool schemas to use flat parameter structures with minimal required fields. This reduces the total schema token count and complexity passed to the LLM provider, preventing the 400 error.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The research agent's tool set includes too many tools with complex schemas, exceeding the LLM provider's limits for function calling compilation.

Fix: Modify the agent initialization code to reduce the number of tools and simplify their parameter schemas. Specifically, for the research task agent, remove non-essential tools (e.g., keep only web search, data analysis, and document writing) and refactor custom tool schemas to use flat parameter structures with minimal required fields. This reduces the total schema token count and complexity passed to the LLM provider, preventing the 400 error.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 681` / `reject 681` via Signal.
