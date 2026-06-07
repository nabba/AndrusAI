# PostgreSQL 16 → 18 migration runbook (pgvector)

**Status:** ready to run, operator-scheduled. Not yet executed.
**Author:** version-audit pass, 2026-06-07.
**Scope:** the main-stack `postgres` service only (`pgvector/pgvector`), holding
the `mem0` database (mem0 vectors + `control_plane.*` + `beliefs` +
`crewai_memories` + `epistemic_claims` + …). The **firecrawl** stack's separate
`postgres:16-alpine` is intentionally left on 16 — keep it decoupled.

---

## 1. Why (honest framing — don't oversell this)

The headline pgvector win (0.8 iterative HNSW scans, faster filtered queries) is
**already in production**: the running pg16 image ships **pgvector 0.8.2**. So
this migration does **not** give a big vector-query speedup — that's banked.

What PG18 actually buys:

- **Postgres 18 async I/O subsystem** — meaningfully faster reads from storage
  for I/O-bound scans. Incremental, not dramatic, on a 1 GB dataset.
- **Staying on a current, supported major** (16 is supported until Nov 2028, so
  this is *not* urgent — it's currency + a modest engine win).
- Planner / vacuum / observability improvements that accrue over years.

**Verdict: schedule it in a planned window for currency + a modest gain — it is
not a fire.** If you're optimizing for impact-per-effort this is lower priority
than the free wins (Ollama host upgrade, image pinning).

## 2. What changes vs. what's preserved

| | |
|---|---|
| On-disk data-dir format | **Incompatible across majors** → cannot swap the tag and restart. Logical dump/restore required. |
| 768-dim vectors | **Preserved byte-for-byte. No re-embedding.** Vectors are version-agnostic table data. |
| pgvector extension | Restored from the dump's `CREATE EXTENSION`; pg18 image ships pgvector 0.8.x (≥ current 0.8.2). |
| HNSW / IVFFlat indexes | Rebuilt automatically on restore (the dump contains the `CREATE INDEX` DDL). |
| Tuning (`shared_buffers`, `work_mem`, …) | Carried by the compose `command:` block — unchanged, applies to pg18. |

## 3. Why dump/restore and not `pg_upgrade`

`pg_upgrade` (in-place, `--link`) is faster but needs **both** old and new
Postgres binaries present in one image — awkward with the single-version
`pgvector/pgvector` images. Your existing tooling (`backup.sh`,
`version-upgrade-drill.sh`) already does **logical dump/restore** with
`pg_dump … | gzip` → `gunzip -c … | psql`, and the dataset is small (969 MB
data dir, ~89 MB gzipped dump → restore is single-digit minutes). This runbook
mirrors that proven path exactly. **Estimated downtime: ~15–30 min including
verification.**

---

## 4. Pre-flight (do days ahead, zero live impact)

**4a. Prove the pg18 restore works in isolation** — the load-bearing safety
step. This standalone dry-run restores the latest dump into a throwaway pg18
container (no compose, no volume mount, no live data touched — discarded on
`rm`). Run it days ahead; a clean PASS means §5 is safe.

```bash
cd ~/BotArmy/crewai-team
set -a; source .env; set +a
PGPW="${MEM0_POSTGRES_PASSWORD:?set in .env}"

# Ensure a fresh backup exists, then take the latest dump:
bash deploy/scripts/backup.sh
LATEST="$(ls -t workspace/backups/postgres/postgres-*.sql.gz | head -1)"
echo "Drilling restore of: $LATEST"

docker pull pgvector/pgvector:pg18
docker run -d --name pg18-drill \
  -e POSTGRES_USER=mem0 -e POSTGRES_PASSWORD="$PGPW" -e POSTGRES_DB=mem0 \
  pgvector/pgvector:pg18
until docker exec pg18-drill pg_isready -U mem0 >/dev/null 2>&1; do sleep 2; done

# Restore into pg18 — ON_ERROR_STOP makes any pgvector/operator incompat fatal:
gunzip -c "$LATEST" | docker exec -i -e PGPASSWORD="$PGPW" pg18-drill \
  psql -U mem0 -d mem0 --set ON_ERROR_STOP=1

# Smoke-check:
docker exec -e PGPASSWORD="$PGPW" pg18-drill psql -U mem0 -d mem0 -c \
  "SELECT version();
   SELECT extname, extversion FROM pg_extension WHERE extname='vector';
   SELECT count(*) FROM control_plane.audit_log;"

# Teardown (nothing persisted):
docker rm -f pg18-drill
```

PASS = restore exits 0, pgvector extension present, `version()` shows 18.x,
audit_log count is sane → §5 is safe. A FAIL means stop and investigate — you've
learned it with **zero** production risk.

> **Why not `version-upgrade-drill.sh` here?** That fuller drill is currently
> broken: it still does `docker compose ... up -d postgres neo4j chromadb`, but
> the `chromadb` compose service was removed in the 2026-05-17 dual-writer fix
> (§55), so it errors with "no such service: chromadb". Fix it first (drop
> `chromadb` from the `up` line + the chroma restore block) if you want the
> overlay-isolated multi-DB drill; otherwise the standalone dry-run above fully
> covers the Postgres path, and §7's preserved-data-dir rollback is the real
> safety net regardless.

**4b. Capacity + window check**

```bash
df -h .                              # need ~3 GB free headroom for the swap
du -sh workspace/mem0_pgdata         # ~969 MB today
ls -lh workspace/backups/postgres/ | tail -3
```

---

## 5. Migration (the live window)

All commands from the repo root: `cd ~/BotArmy/crewai-team`. Load the DB
password from `.env` once:

```bash
set -a; source .env; set +a
PGPW="${MEM0_POSTGRES_PASSWORD:?set in .env}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
```

**5.1 — Stop the writers** (gateway + worker both write Postgres; the
docker-broker/proxy don't, leave them):

```bash
docker compose stop gateway worker
```

**5.2 — Take a final, consistent dump** (writers are stopped, so this is the
authoritative post-cutover state). Same flags as `backup.sh`:

```bash
docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
  pg_dump --username mem0 --dbname mem0 \
  --clean --if-exists --no-owner --no-privileges \
  | gzip > "workspace/backups/postgres/premigration-pg16-${TS}.sql.gz"

# sanity: non-trivial size
ls -lh "workspace/backups/postgres/premigration-pg16-${TS}.sql.gz"
```

**5.3 — Capture pre-migration row counts** (authoritative before/after check):

```bash
docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
  psql -U mem0 -d mem0 -At -c "
    SELECT format('SELECT %L AS tbl, count(*) FROM %I.%I',
                  schemaname||'.'||relname, schemaname, relname)
    FROM pg_stat_user_tables ORDER BY 1" \
  | docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
      psql -U mem0 -d mem0 -At \
  | sort > "/tmp/pg_counts_before_${TS}.txt"
cat "/tmp/pg_counts_before_${TS}.txt"
```

**5.4 — Stop Postgres and move the old data dir ASIDE** (this is the rollback
anchor — do NOT delete it; the migration only succeeds when you decide it did):

```bash
docker compose stop postgres
mv workspace/mem0_pgdata "workspace/mem0_pgdata.pg16-${TS}"
```

**5.5 — Point the stack at pg18.** Pull it, capture its digest, pin it in
`.env` (overrides the compose default — clean and trivially revertible):

```bash
docker pull pgvector/pgvector:pg18
PG18="$(docker inspect --format '{{index .RepoDigests 0}}' pgvector/pgvector:pg18)"
echo "pg18 = $PG18"
# Append to .env (or edit by hand):
printf 'POSTGRES_IMAGE=%s\n' "$PG18" >> .env
```

**5.6 — Bring pg18 up** (fresh `initdb` creates an empty `mem0` db via
`POSTGRES_DB`), wait healthy:

```bash
docker compose up -d postgres
# wait for healthcheck (pg_isready) to go healthy:
until [ "$(docker inspect -f '{{.State.Health.Status}}' crewai-team-postgres-1)" = healthy ]; do sleep 2; done
docker compose exec -T postgres postgres --version   # expect 18.x
```

**5.7 — Restore** (the dump's `CREATE EXTENSION vector` + `CREATE INDEX … hnsw`
recreate the extension and rebuild indexes; `ON_ERROR_STOP=1` aborts loudly on
any failure):

```bash
gunzip -c "workspace/backups/postgres/premigration-pg16-${TS}.sql.gz" \
  | docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
      psql --username mem0 --dbname mem0 --set ON_ERROR_STOP=1
```

If this exits non-zero → go straight to **§7 Rollback** (you've lost nothing).

---

## 6. Verify (before restarting the gateway)

```bash
# 6a. Engine + extension version
docker compose exec -T -e PGPASSWORD="$PGPW" postgres psql -U mem0 -d mem0 -c \
  "SELECT version(); SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

# 6b. Row counts AFTER — must match /tmp/pg_counts_before_${TS}.txt
docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
  psql -U mem0 -d mem0 -At -c "
    SELECT format('SELECT %L AS tbl, count(*) FROM %I.%I',
                  schemaname||'.'||relname, schemaname, relname)
    FROM pg_stat_user_tables ORDER BY 1" \
  | docker compose exec -T -e PGPASSWORD="$PGPW" postgres \
      psql -U mem0 -d mem0 -At \
  | sort > "/tmp/pg_counts_after_${TS}.txt"

diff "/tmp/pg_counts_before_${TS}.txt" "/tmp/pg_counts_after_${TS}.txt" \
  && echo "✅ row counts identical" || echo "❌ ROW COUNT MISMATCH — investigate before restart"

# 6c. HNSW indexes present (vector index sanity)
docker compose exec -T -e PGPASSWORD="$PGPW" postgres psql -U mem0 -d mem0 -c \
  "SELECT indexname FROM pg_indexes WHERE indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%';"
```

All three green → restart and smoke-test:

```bash
docker compose up -d gateway worker
curl -fsS http://127.0.0.1:8765/health && echo " gateway OK"
```

Then exercise a real memory/RAG path (e.g. a Signal/`/cp/chat` query that hits
mem0 retrieval) and confirm results come back.

---

## 7. Rollback (instant, lossless)

Because the pg16 data dir was only *moved*, never deleted:

```bash
docker compose stop postgres
rm -rf workspace/mem0_pgdata                       # the half-built pg18 dir
mv "workspace/mem0_pgdata.pg16-${TS}" workspace/mem0_pgdata
# revert the override:
sed -i '' '/^POSTGRES_IMAGE=pgvector\/pgvector:pg18/d' .env   # macOS sed
docker compose up -d postgres gateway worker
```

You're back on pg16 with the exact pre-migration state.

---

## 8. Post-migration (after a clean soak — e.g. 3–7 days)

1. Take a fresh backup on pg18 so the freshness monitor + drills have a current
   `all_ok` set on the new major: `bash deploy/scripts/backup.sh`.
2. Update the **compose default** to the pg18 digest so the fallback matches the
   `.env` override (then the `.env` line is belt-and-suspenders, not load-bearing):
   in `docker-compose.yml`, replace the `pg16@sha256:…` default on the `postgres`
   service with the `$PG18` digest from §5.5.
3. Bump the drill's forward target past pg18 (so it keeps testing the *next* hop):
   `POSTGRES_TARGET_TAG` default in `deploy/scripts/version-upgrade-drill.sh`
   (currently `pgvector/pgvector:0.8.0-pg17`) → a pg19/next tag when one exists.
4. Delete the old data dir once you're confident:
   `rm -rf workspace/mem0_pgdata.pg16-${TS}`.

## 9. Integration notes

- `deploy/scripts/backup.sh` is version-agnostic (`pg_dump` from whatever's
  running) — no change needed.
- The `db_backup` freshness monitor + `migration_drill` monitor keep working;
  the first post-migration backup resets their baselines.
- `substrate_radar` will now see a `@sha256`-pinned postgres image (was flagged
  as unpinned `pg16`) — one fewer finding.
- Neo4j + ChromaDB are untouched by this runbook.
