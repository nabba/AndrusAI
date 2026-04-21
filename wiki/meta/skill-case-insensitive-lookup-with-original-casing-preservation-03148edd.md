---
aliases:
- skill case insensitive lookup with original casing preservation 03148edd
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-21T14:25:24Z'
date: '2026-04-21'
related: []
relationships: []
section: meta
source: workspace/skills/skill__case-insensitive_lookup_with_original_casing_preservation__03148edd.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: 'Skill: Case-Insensitive Lookup with Original Casing Preservation'
updated_at: '2026-04-21T14:25:24Z'
version: 1
---

<!-- generated-by: self_improvement.integrator -->
# Skill: Case-Insensitive Lookup with Original Casing Preservation

*kb: experiential | id: skill_experiential_bc57d65003148edd | status: active | usage: 0 | created: 2026-04-21T13:44:08+00:00*

# Skill: Case-Insensitive Lookup with Original Casing Preservation

## When to Use
Apply this skill when implementing lookups, searches, or comparisons where users reference stored items by name, and you want flexible matching while preserving the original data's formatting (e.g., project names, usernames, file identifiers).

## Procedure
1. **Store data with original casing** — Keep the user-provided or canonical form unchanged in your data store
2. **Normalize for comparison only** — Convert both the stored key and user input to a common case (e.g., `.lower()`) during lookup
3. **Return the original stored value** — After a match, return the preserved original casing, not the normalized version
4. **Document the behavior** — Add comments explaining case-insensitive matching for maintainability
5. **Test edge cases** — Verify mixed-case inputs (e.g., "PLG", "Plg", "plg") all match correctly

## Pitfalls
- **Lowercasing before storage** — Losing original formatting frustrates users expecting preserved names
- **Inconsistent normalization** — Using `.lower()` in some places and `.casefold()` in others can cause subtle bugs with Unicode characters
- **Returning the input instead of stored value** — If user types "plg" but project is stored as "PLG", returning "plg" corrupts data integrity
- **Forgetting to normalize both sides** — Comparing `user_input.lower()` against raw `stored_name` fails; normalize both
