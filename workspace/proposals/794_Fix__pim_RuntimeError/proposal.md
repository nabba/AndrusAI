# Proposal #794: Fix: pim:RuntimeError

**Type:** code  
**Created:** 2026-04-28T07:00:14.899617+00:00  
**Resolves:** `pim:RuntimeError`  

## Why this is useful

The error is a credit limitation on OpenRouter preventing the request of 4096 max_tokens. Lower the `max_tokens` parameter in the LLM configuration to 3000 or less to fit within the available credit balance.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

The error is a credit limitation on OpenRouter preventing the request of 4096 max_tokens. Lower the `max_tokens` parameter in the LLM configuration to 3000 or less to fit within the available credit balance.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 794` / `reject 794` via Signal.
