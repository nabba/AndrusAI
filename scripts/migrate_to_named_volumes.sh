#!/usr/bin/env bash
# migrate_to_named_volumes.sh — optional Stage 1 migration.
#
# Why: bind mounts on macOS (even with VirtioFS) are 2-5x slower than named
# Docker volumes for hot DB data. Migrating chromadb / mem0_pgdata / mem0_neo4j
# to named volumes eliminates the FS-translation overhead on every read/write.
#
# This script is OPT-IN and DESTRUCTIVE (briefly). It will:
#   1. docker compose down (stops the stack)
#   2. docker volume create the 3 named volumes
#   3. Copy existing bind-mount data into the named volumes
#   4. Print the sed commands to switch compose to the new volumes
#   5. Restart the stack
#
# You can back out by deleting the named volumes and reverting docker-compose.yml.
# The original bind-mount dirs under ./workspace remain untouched.

set -euo pipefail

cd "$(dirname "$0")/.."

PROJ="$(basename "$(pwd)")"   # typically "crewai-team"
read -p "About to docker compose down + copy workspace/{memory,mem0_pgdata,mem0_neo4j} into named volumes. Continue? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 0; }

echo ">> Stopping stack…"
docker compose down

for entry in "memory:chromadb_data" "mem0_pgdata:mem0_pgdata" "mem0_neo4j:mem0_neo4j"; do
  SRC_DIR="${entry%%:*}"
  VOL_NAME="${entry##*:}"
  FULL_VOL="${PROJ}_${VOL_NAME}"
  SRC_PATH="$(pwd)/workspace/${SRC_DIR}"

  if [[ ! -d "$SRC_PATH" ]]; then
    echo ">> Skipping $SRC_DIR (no existing data at $SRC_PATH)"
    continue
  fi

  echo ">> Creating named volume $FULL_VOL and copying from $SRC_PATH"
  docker volume create "$FULL_VOL" >/dev/null
  docker run --rm \
    -v "$FULL_VOL":/dst \
    -v "$SRC_PATH":/src:ro \
    alpine sh -c 'cp -a /src/. /dst/ && echo "  → $(du -sh /dst | cut -f1) migrated"'
done

cat <<'EOF'

>> Migration copy complete.

Next step — edit docker-compose.yml to USE the named volumes. Replace these
three lines in the services block:

  chromadb.volumes:   "- ./workspace/memory:/chroma/chroma"
                 →    "- chromadb_data:/chroma/chroma"
  postgres.volumes:   "- ./workspace/mem0_pgdata:/var/lib/postgresql/data"
                 →    "- mem0_pgdata:/var/lib/postgresql/data"
  neo4j.volumes:      "- ./workspace/mem0_neo4j:/data"
                 →    "- mem0_neo4j:/data"

Then add at the end of docker-compose.yml (replacing the existing "volumes: {}"):

  volumes:
    chromadb_data:
    mem0_pgdata:
    mem0_neo4j:

Finally:
  docker compose up -d
  docker compose logs -f gateway  # verify clean startup

If anything misbehaves, revert the compose changes and the stack will come
back up on the original bind mounts (original data is still in ./workspace).
EOF
