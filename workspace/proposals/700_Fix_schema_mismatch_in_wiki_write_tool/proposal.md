# Proposal #700: Fix schema mismatch in wiki_write tool

**Type:** code  
**Created:** 2026-04-20T19:48:38.606574+00:00  

## Why this is useful

Diagnosis: The JSON schema for the 'wiki_write' tool is invalid because 'title' is listed in the 'required' array but is missing from the 'properties' object.

Fix: Update the input schema (Pydantic model or args_schema) for the 'wiki_write' tool. Ensure that 'title' is defined as a field in the model's properties, or remove 'title' from the list of required fields if it is not a valid parameter.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The JSON schema for the 'wiki_write' tool is invalid because 'title' is listed in the 'required' array but is missing from the 'properties' object.

Fix: Update the input schema (Pydantic model or args_schema) for the 'wiki_write' tool. Ensure that 'title' is defined as a field in the model's properties, or remove 'title' from the list of required fields if it is not a valid parameter.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 700` / `reject 700` via Signal.
