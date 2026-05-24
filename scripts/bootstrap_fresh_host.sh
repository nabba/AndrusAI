#!/usr/bin/env bash
# Fresh-host bootstrap drill — Gap 1 of the 2026-05-24 ultrathink
# analysis closure.
#
# Operator-facing wrapper around the in-gateway drill module. Two modes:
#
#   --check    (default) — verify the install path artifacts + restore
#              the latest DR tarball into a scratch dir + walk source
#              ledgers. Read-only against the repo + live workspace.
#
#   --rebuild  build an ephemeral host environment side-by-side: create
#              a temp directory, restore DR into it, validate the docker
#              compose file actually composes against it, run the
#              minimum subsystem self-checks. Never touches the live
#              workspace or live containers.
#
# Exit codes:
#   0  drill PASS
#   1  drill FAIL (see workspace/resilience/drill_audit.jsonl)
#   2  prerequisites missing (python, docker, repo state)
#
# Usage:
#   scripts/bootstrap_fresh_host.sh --check
#   scripts/bootstrap_fresh_host.sh --rebuild
#
# Composes with — does not replace — the existing DR drill
# (scripts/dr_boot_drill.sh) and the source-ledger replay drill.
# This wrapper is the load-bearing "could a clean machine boot
# AndrusAI from this repo + this backup" check.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="check"
KEEP_SCRATCH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)        MODE="check"; shift ;;
    --rebuild)      MODE="rebuild"; shift ;;
    --keep-scratch) KEEP_SCRATCH=1; shift ;;
    --help|-h)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "bootstrap_fresh_host: unknown option $1 (try --help)" >&2
      exit 1
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "bootstrap_fresh_host: python3 not found on PATH" >&2
  exit 2
fi

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

echo "→ Fresh-host bootstrap drill (mode=$MODE)"

case "$MODE" in
  check)
    # Run the drill via the standard registry — same path the
    # quarterly scheduler uses. Operator gets the same DrillResult
    # the scheduler would have written to drill_audit.jsonl.
    exec "$PY" -m app.resilience_drills.__main__ run fresh_host_bootstrap
    ;;
  rebuild)
    # Same drill, but with dockerized smoke ON for this single
    # invocation. We flip the runtime setting temporarily so the
    # drill's _dockerized_smoke step actually fires.
    "$PY" - <<'PY_INLINE'
import sys

try:
    from app import runtime_settings
except Exception as exc:
    print(f"bootstrap_fresh_host: runtime_settings unavailable: {exc}", file=sys.stderr)
    sys.exit(2)

prior = runtime_settings.get_drill_fresh_host_bootstrap_dockerized_enabled()
runtime_settings.set_drill_fresh_host_bootstrap_dockerized_enabled(True)
try:
    from app.resilience_drills.runner import invoke_drill
    result = invoke_drill("fresh_host_bootstrap", dry_run=True, source="operator_rebuild")
    print(f"status={result.status.value} duration_s={round(result.duration_s, 2)}")
    if result.errors:
        for err in result.errors:
            print(f"  error: {err}")
    if result.detail:
        import json
        print("detail:")
        print(json.dumps(result.detail, indent=2, default=str)[:2000])
    sys.exit(0 if result.status.value == "pass" else 1)
finally:
    runtime_settings.set_drill_fresh_host_bootstrap_dockerized_enabled(prior)
PY_INLINE
    ;;
esac
