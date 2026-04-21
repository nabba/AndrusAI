# Proposal #702: Fix: coding:RuntimeError

**Type:** code  
**Created:** 2026-04-20T20:25:17.327809+00:00  
**Resolves:** `coding:RuntimeError`  

## Why this is useful

The 'wiki_write' tool definition has a schema mismatch: the key 'title' is listed in the 'required' array but is missing from the 'properties' dictionary. To fix, locate the tool definition (likely in a tools file or Pydantic model) and add 'title' to 'properties' (e.g., 'title': {'type': 'string', 'description': 'The title of the wiki page'}) or remove 'title' from the 'required' list if it is not a mandatory parameter.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

The 'wiki_write' tool definition has a schema mismatch: the key 'title' is listed in the 'required' array but is missing from the 'properties' dictionary. To fix, locate the tool definition (likely in a tools file or Pydantic model) and add 'title' to 'properties' (e.g., 'title': {'type': 'string', 'description': 'The title of the wiki page'}) or remove 'title' from the 'required' list if it is not a mandatory parameter.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 702` / `reject 702` via Signal.
