# Proposal #673: Structured Data Extraction & JSON Output Skill

**Type:** skill  
**Created:** 2026-04-19T22:26:45.111150+00:00  

## Why this is useful

Problem: The team has web_search and web_fetch but lacks a systematic approach for extracting structured data (tables, lists, key-value pairs) from web pages and converting them into clean JSON/CSV. Many real-world tasks require not just fetching content but parsing it into usable formats. Research tasks have failed with empty outputs even on simple queries, partly because raw text isn't post-processed effectively. Solution: A skill document that teaches agents how to extract structured data from raw HTML/text, handle common patterns (tables, lists, nested data), validate outputs, and produce clean JSON or CSV. This also covers retry strategies when initial extraction fails.

## What will change

- Modifies `skills/structured_data_extraction.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/structured_data_extraction.md`

## Original description

Problem: The team has web_search and web_fetch but lacks a systematic approach for extracting structured data (tables, lists, key-value pairs) from web pages and converting them into clean JSON/CSV. Many real-world tasks require not just fetching content but parsing it into usable formats. Research tasks have failed with empty outputs even on simple queries, partly because raw text isn't post-processed effectively. Solution: A skill document that teaches agents how to extract structured data from raw HTML/text, handle common patterns (tables, lists, nested data), validate outputs, and produce clean JSON or CSV. This also covers retry strategies when initial extraction fails.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 673` / `reject 673` via Signal.
