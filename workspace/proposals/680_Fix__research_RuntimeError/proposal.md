# Proposal #680: Fix: research:RuntimeError

**Type:** code  
**Created:** 2026-04-20T06:00:28.962369+00:00  
**Resolves:** `research:RuntimeError`  

## Why this is useful

The error occurs due to a too complex schema being sent to the API. The solution is to reduce the number of tools or simplify the tool schemas being used in the task execution. In the code, you should modify the task configuration to use fewer or simpler tools.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

The error occurs due to a too complex schema being sent to the API. The solution is to reduce the number of tools or simplify the tool schemas being used in the task execution. In the code, you should modify the task configuration to use fewer or simpler tools.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 680` / `reject 680` via Signal.
