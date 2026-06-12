#!/bin/bash
# migrate_chroma_to_volume.sh — one-shot copy of derived chromadb data from
# the workspace bind mount onto the chroma_data named volume (Phase 1b of the
# gateway serving-plane hardening; see docs/GATEWAY_SERVING_PLANE.md).
#
# Run with the GATEWAY STOPPED (quiescent sqlite + WAL). The script:
#   1. copies, per KB: chroma.sqlite3 (+ -wal/-shm if present) and every
#      UUID-named HNSW segment dir   ->  /chroma/<kb>/
#      EXCLUDING ledgers/snapshots/texts (those stay on the workspace)
#   2. runs PRAGMA integrity_check on every copied database
#   3. chowns the volume to appuser (uid 1000)
#
# Idempotent: re-running overwrites the volume copy from the bind mount.
# The bind-mount originals are NEVER touched.
#
# Usage:  ./scripts/migrate_chroma_to_volume.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if docker compose ps --status running gateway 2>/dev/null | grep -q gateway; then
    echo "REFUSED: gateway is running — stop it first (docker compose stop gateway)" >&2
    exit 1
fi

docker compose run --rm --no-deps \
    -v "$(pwd)/workspace:/src:ro" \
    -v chroma_data:/chroma \
    --entrypoint bash gateway -c '
set -euo pipefail
copied=0
for db in /src/*/chroma.sqlite3; do
    [ -e "$db" ] || continue
    kbdir="$(dirname "$db")"
    kb="$(basename "$kbdir")"
    case "$kb" in
        *corrupt_*|*bak_*|*_backup|*.backup) echo "skip quarantined: $kb"; continue ;;
    esac
    echo "── $kb"
    mkdir -p "/chroma/$kb"
    cp -a "$db" "/chroma/$kb/"
    for side in "-wal" "-shm"; do
        [ -e "${db}${side}" ] && cp -a "${db}${side}" "/chroma/$kb/"
    done
    # HNSW segment dirs are UUID-named (8-4-4-4-12 hex).
    for seg in "$kbdir"/*-*-*-*-*; do
        [ -d "$seg" ] || continue
        base="$(basename "$seg")"
        if echo "$base" | grep -qE "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"; then
            cp -a "$seg" "/chroma/$kb/"
        fi
    done
    copied=$((copied+1))
done
echo "copied $copied KBs"

echo "── integrity checks"
python - <<PYEOF
import sqlite3, sys
from pathlib import Path
bad = []
for db in sorted(Path("/chroma").glob("*/chroma.sqlite3")):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        row = con.execute("PRAGMA integrity_check(1)").fetchone()
        con.close()
        status = row[0] if row else "no_result"
    except Exception as exc:
        status = f"error: {exc}"
    print(f"{db.parent.name:14s} {status}")
    if status != "ok":
        bad.append(db.parent.name)
if bad:
    print(f"FAILED integrity: {bad}", file=sys.stderr)
    sys.exit(1)
print("all databases ok")
PYEOF

chown -R 1000:1000 /chroma
echo "── volume contents"
du -sh /chroma/* 2>/dev/null || true
'
echo "Migration copy complete. Next: set CHROMA_DATA_ROOT=/chroma (Phase 1b commit) and start the gateway."
