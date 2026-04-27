"""Forge runtime — capability-guarded execution of registered tools.

Two execution paths:
  - Declarative: a JSON recipe interpreted in the gateway by trusted code,
    using a capability-guarded HTTP client. Covers the common case of
    "call a REST API and return some JSON".
  - Python sandbox: agent-written Python runs in the existing Docker
    sandbox (network disabled, RO filesystem, mem/CPU caps). Inputs marshalled
    via stdin, outputs read from stdout. Pure-compute only in this phase —
    a future phase will add a capability-mediated I/O bridge.

Every invocation:
  1. Resolves the kill switch (env + runtime override + per-tool status)
  2. Checks the per-tool budget (calls/hour ceiling)
  3. Validates the input against the manifest's parameter schema
  4. Runs through the appropriate runtime
  5. Logs an entry to forge_invocations with capabilities used and any
     capability violations the runtime caught
"""
