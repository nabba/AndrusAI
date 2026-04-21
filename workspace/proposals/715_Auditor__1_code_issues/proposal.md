# Proposal #715: Auditor: 1 code issues

**Type:** code  
**Created:** 2026-04-21T00:02:47.285721+00:00  

## Why this is useful

Path traversal vulnerability in aesthetics/api.py - flagged_by parameter used unsanitized in file path construction

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Path traversal vulnerability in aesthetics/api.py - flagged_by parameter used unsanitized in file path construction

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 715` / `reject 715` via Signal.
