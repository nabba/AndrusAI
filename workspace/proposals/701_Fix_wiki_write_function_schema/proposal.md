# Proposal #701: Fix wiki_write function schema

**Type:** code  
**Created:** 2026-04-20T20:20:04.095376+00:00  

## Why this is useful

Diagnosis: The wiki_write tool definition has an invalid JSON schema. The 'required' array includes 'title' but this field may be missing from the 'properties' object, or the schema is malformed. The schema requires that every field in 'required' must exist in 'properties'.

Fix: The function schema for 'wiki_write' has a mismatch between its 'required' array and its 'properties'. Fix by ensuring every field listed in 'required' exists in 'properties', or remove the extra 'title' from required.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The wiki_write tool definition has an invalid JSON schema. The 'required' array includes 'title' but this field may be missing from the 'properties' object, or the schema is malformed. The schema requires that every field in 'required' must exist in 'properties'.

Fix: The function schema for 'wiki_write' has a mismatch between its 'required' array and its 'properties'. Fix by ensuring every field listed in 'required' exists in 'properties', or remove the extra 'title' from required.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 701` / `reject 701` via Signal.
