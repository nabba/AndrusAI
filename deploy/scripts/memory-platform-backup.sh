#!/usr/bin/env bash
# Back up the physically separate durable-memory and governance databases.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

PROJECT="${MEMORY_PLATFORM_COMPOSE_PROJECT:-crewai-team}"
BACKUP_ROOT="${MEMORY_PLATFORM_BACKUP_ROOT:-${REPO_ROOT}/workspace/backups/memory-platform}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${BACKUP_ROOT}/${STAMP}"
mkdir -p "$RUN_DIR"
chmod 700 "$BACKUP_ROOT" "$RUN_DIR"

COMPOSE=(
  docker compose
  -p "$PROJECT"
  -f docker-compose.yml
  -f deploy/memory-platform.compose.yml
  --profile memory-platform
)

for service in durable-memory-postgres governance-postgres; do
  if [[ "$("${COMPOSE[@]}" ps --status running -q "$service" | wc -l | tr -d ' ')" != "1" ]]; then
    echo "ERROR: $service is not running in project $PROJECT" >&2
    exit 2
  fi
done

DURABLE_DUMP="${RUN_DIR}/durable-memory.dump"
GOVERNANCE_DUMP="${RUN_DIR}/governance.dump"

"${COMPOSE[@]}" exec -T durable-memory-postgres \
  pg_dump --username memory_owner --dbname botarmy_memory \
  --format=custom --compress=6 --no-password >"$DURABLE_DUMP"

"${COMPOSE[@]}" exec -T governance-postgres \
  pg_dump --username governance_owner --dbname botarmy_governance \
  --format=custom --compress=6 --no-password >"$GOVERNANCE_DUMP"

chmod 600 "$DURABLE_DUMP" "$GOVERNANCE_DUMP"
DURABLE_SHA="$(shasum -a 256 "$DURABLE_DUMP" | awk '{print $1}')"
GOVERNANCE_SHA="$(shasum -a 256 "$GOVERNANCE_DUMP" | awk '{print $1}')"

MANIFEST="${RUN_DIR}/manifest.json"
python3 - "$MANIFEST" "$STAMP" "$DURABLE_DUMP" "$DURABLE_SHA" "$GOVERNANCE_DUMP" "$GOVERNANCE_SHA" <<'PY'
import json
import sys
from pathlib import Path

manifest, stamp, durable_path, durable_sha, governance_path, governance_sha = sys.argv[1:]
payload = {
    "format_version": 1,
    "created_at": stamp,
    "durable": {"path": durable_path, "sha256": durable_sha},
    "governance": {"path": governance_path, "sha256": governance_sha},
}
Path(manifest).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
chmod 600 "$MANIFEST"

ln -sfn "$RUN_DIR" "${BACKUP_ROOT}/latest"
echo "$MANIFEST"
