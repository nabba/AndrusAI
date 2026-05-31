#!/usr/bin/env bash
# One-shot host-side deploy for the BotArmy gateway.
#
# Runs ON THE MAC (needs the Docker engine + launchd that live on the host —
# it cannot run from a CI sandbox or inside the container). Chains the four
# steps you'd otherwise type by hand:
#
#   1. git pull           — fast-forward the repo to the latest main
#   2. docker compose up -d --build gateway
#                         — rebuild + restart the gateway container so code
#                           changes (e.g. app/main.py) take effect
#   3. watchdog reload    — restart the launchd watchdog so changes to
#                           scripts/gateway_watchdog.py take effect
#   4. verify             — print watchdog status + tail the log
#
# Steps 1–3 each abort the run on failure (set -e); the verify step is
# best-effort. Use --no-pull to deploy the working tree as-is (e.g. a local
# hotfix you haven't pushed), or --skip-watchdog to rebuild the gateway only.
#
# Usage:
#   ./scripts/deploy_gateway.sh                 # pull + rebuild + watchdog + verify
#   ./scripts/deploy_gateway.sh --no-pull       # skip git pull
#   ./scripts/deploy_gateway.sh --skip-watchdog # rebuild gateway only
#
# Env overrides:
#   GATEWAY_SERVICE   compose service name (default: gateway)
#   COMPOSE_FILE      passed through to docker compose if set

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATEWAY_SERVICE="${GATEWAY_SERVICE:-gateway}"
WATCHDOG="$REPO_ROOT/scripts/install_gateway_watchdog.sh"
WATCHDOG_LOG="$REPO_ROOT/workspace/healing/.gateway_watchdog.log"

do_pull=1
do_watchdog=1
for arg in "$@"; do
  case "$arg" in
    --no-pull)       do_pull=0 ;;
    --skip-watchdog) do_watchdog=0 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "deploy_gateway: unknown argument '$arg' (try --help)" >&2
      exit 2 ;;
  esac
done

# Refuse to run where there is no Docker engine (e.g. a CI sandbox or inside
# the gateway container itself) — the whole point is the host docker build.
if ! docker info >/dev/null 2>&1; then
  echo "✗ Docker engine not reachable. Run this on the Mac host, not in a" >&2
  echo "  container/CI sandbox (the gateway image is built by host Docker)." >&2
  exit 1
fi

cd "$REPO_ROOT"

if [ "$do_pull" -eq 1 ]; then
  echo "▶ git pull"
  git pull --ff-only
else
  echo "▶ skipping git pull (--no-pull) — deploying working tree as-is"
fi

echo "▶ rebuilding + restarting compose service '$GATEWAY_SERVICE'"
docker compose up -d --build "$GATEWAY_SERVICE"

if [ "$do_watchdog" -eq 1 ]; then
  echo "▶ reloading host watchdog (picks up scripts/gateway_watchdog.py changes)"
  "$WATCHDOG" restart
else
  echo "▶ skipping watchdog reload (--skip-watchdog)"
fi

echo "▶ verify"
if [ "$do_watchdog" -eq 1 ]; then
  "$WATCHDOG" status || true
fi
echo "── recent gateway logs ──────────────────────────────────────────────"
docker compose logs --tail 30 "$GATEWAY_SERVICE" || true
if [ -f "$WATCHDOG_LOG" ]; then
  echo "── recent watchdog log ──────────────────────────────────────────────"
  tail -n 15 "$WATCHDOG_LOG" || true
fi

echo "✓ deploy complete. Watch /health come up cleanly with:"
echo "    tail -f \"$WATCHDOG_LOG\""
