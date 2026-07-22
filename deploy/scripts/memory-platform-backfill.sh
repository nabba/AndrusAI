#!/usr/bin/env bash
# Run a one-space backfill while enforcing embedded Chroma single-writer safety.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <memory-space> [backfill options...]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(
  docker compose
  -f docker-compose.yml
  -f deploy/memory-platform.compose.yml
  --profile memory-platform
)

if [[ -n "$("${COMPOSE[@]}" ps --status running -q gateway)" ]]; then
  echo "ERROR: gateway is running; stop it before opening the embedded Chroma volume" >&2
  exit 3
fi

if [[ -n "$("${COMPOSE[@]}" ps --status running -q memory-platform-backfill)" ]]; then
  echo "ERROR: another memory-platform backfill is already running" >&2
  exit 4
fi

"${COMPOSE[@]}" run --rm memory-platform-backfill "$@"
