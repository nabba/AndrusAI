#!/usr/bin/env bash
# Restore both memory boundaries into an isolated Compose project and smoke them.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

BACKUP_ROOT="${MEMORY_PLATFORM_BACKUP_ROOT:-${REPO_ROOT}/workspace/backups/memory-platform}"
MANIFEST="${1:-${BACKUP_ROOT}/latest/manifest.json}"
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: backup manifest not found: $MANIFEST" >&2
  exit 2
fi

BACKUP_VALUES="$(python3 - "$MANIFEST" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
for key in ("durable", "governance"):
    path = Path(manifest[key]["path"])
    if not path.is_file():
        raise SystemExit(f"missing {key} dump: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest[key]["sha256"]:
        raise SystemExit(f"checksum mismatch: {path}")
print(manifest["durable"]["path"])
print(manifest["governance"]["path"])
PY
)"
DURABLE_DUMP="$(printf '%s\n' "$BACKUP_VALUES" | sed -n '1p')"
GOVERNANCE_DUMP="$(printf '%s\n' "$BACKUP_VALUES" | sed -n '2p')"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PROJECT="botarmy-memory-restore-drill-$(printf '%s' "$STAMP" | tr '[:upper:]' '[:lower:]')"
export DURABLE_MEMORY_POSTGRES_PASSWORD="restore-drill-durable-${STAMP}"
export GOVERNANCE_POSTGRES_PASSWORD="restore-drill-governance-${STAMP}"

COMPOSE=(
  docker compose
  -p "$PROJECT"
  -f docker-compose.yml
  -f deploy/memory-platform.compose.yml
  --profile memory-platform
)

cleanup() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"${COMPOSE[@]}" up -d durable-memory-postgres governance-postgres
"${COMPOSE[@]}" run --rm durable-memory-migrate >/dev/null
"${COMPOSE[@]}" run --rm governance-migrate >/dev/null

"${COMPOSE[@]}" exec -T durable-memory-postgres \
  pg_restore --username memory_owner --dbname botarmy_memory \
  --clean --if-exists --no-owner --exit-on-error <"$DURABLE_DUMP"
"${COMPOSE[@]}" exec -T governance-postgres \
  pg_restore --username governance_owner --dbname botarmy_governance \
  --clean --if-exists --no-owner --exit-on-error <"$GOVERNANCE_DUMP"

"${COMPOSE[@]}" run --rm durable-memory-migrate \
  python scripts/apply_memory_platform_migrations.py --target durable --check >/dev/null
"${COMPOSE[@]}" run --rm governance-migrate \
  python scripts/apply_memory_platform_migrations.py --target governance --check >/dev/null

"${COMPOSE[@]}" exec -T durable-memory-postgres \
  psql --username memory_owner --dbname botarmy_memory --set ON_ERROR_STOP=1 \
  --command "SELECT count(*) FROM memory_admin.memory_spaces; SELECT count(*) FROM memory_admin.migration_state;" >/dev/null
"${COMPOSE[@]}" exec -T governance-postgres \
  psql --username governance_owner --dbname botarmy_governance --set ON_ERROR_STOP=1 \
  --command "SELECT count(*), bool_and(octet_length(event_hash) = 32) FROM governance_boundary.events;" >/dev/null

echo "memory-platform restore drill passed: $MANIFEST"
