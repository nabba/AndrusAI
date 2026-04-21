# Proposal #683: Lock file directory permission fix

**Type:** code  
**Created:** 2026-04-20T08:54:56.410490+00:00  

## Why this is useful

Diagnosis: The AI agent encounters a PermissionError when attempting to create or access a lock file in the /tmp/ directory, as the current runtime environment (e.g., sandboxed container or restricted filesystem) denies write permissions to /tmp/, preventing portalocker from opening the lock file.

Fix: Modify the agent code to use a writable directory for lock files instead of hardcoding /tmp/. For instance, replace the lock file path with a location obtained via tempfile.gettempdir() or a configurable path like ~/.cache/crewai/locks, ensuring write access in restricted environments and avoiding permission errors during file locking operations.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The AI agent encounters a PermissionError when attempting to create or access a lock file in the /tmp/ directory, as the current runtime environment (e.g., sandboxed container or restricted filesystem) denies write permissions to /tmp/, preventing portalocker from opening the lock file.

Fix: Modify the agent code to use a writable directory for lock files instead of hardcoding /tmp/. For instance, replace the lock file path with a location obtained via tempfile.gettempdir() or a configurable path like ~/.cache/crewai/locks, ensuring write access in restricted environments and avoiding permission errors during file locking operations.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 683` / `reject 683` via Signal.
