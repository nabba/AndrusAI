# Durable Memory Platform — Architecture and Migration Contract

Status: additive platform deployed; two pilots backfilled; no production read cutovers
Date: 2026-07-22

## Decision

BotArmy will use one PostgreSQL/pgvector platform for canonical durable
memory. Durable memory remains divided into separately indexed and permissioned
systems. Operational memory remains on the physically separate Chroma data
plane during the migration. Governance history is stored by a separate
PostgreSQL service with independent credentials, volume, network reachability,
and append-only enforcement.

This decision simplifies database technology and recovery without collapsing
cognitive distinctions. It does not make a claim that the system is sentient.
It preserves the temporal continuity, differentiated self-memory, affect,
belief revision, controlled spontaneous recall, unresolved tensions, and
cross-domain association that may support emergent subjectivity and creativity.

## Non-negotiable boundaries

1. Factual, theoretical, subjective, fictional, procedural, evaluative, and
   dialectical records always retain an explicit epistemic class.
2. Full and curated episodes are different physical tables and different
   permissions. Ordinary and spontaneous recall cannot access the full table.
3. Fiction is unavailable to Researcher, Critic, and Self-Improver. Creative
   blending is an explicit bridge and never removes fictional labeling.
4. Tenant memory requires both broker authorization and PostgreSQL row-level
   security.
5. Governance/evaluation state is not a memory space and is never writable by
   agents or Self-Improver.
6. Operational Chroma is disposable and cannot become the sole copy of durable
   knowledge or autobiography.
7. Source documents and hash-chained source ledgers remain authoritative until
   their space completes cutover and soak.
8. Cross-space recall occurs only through a registered bridge.

## Physical topology

| Boundary | Technology | Authority | Failure behavior |
|---|---|---|---|
| Durable memory | Dedicated PostgreSQL 18 + pgvector | Canonical after per-space cutover | Operational features may degrade; durable history remains |
| Operational memory | Existing embedded Chroma on its named volume | Short-lived working state only | Rebuild or expire without rewriting durable memory |
| Governance | Separate PostgreSQL service and volume | Append-only governance record | Agents continue only within last approved policy; no governance mutation |
| Documents/wiki | Files and source ledgers | Authoritative source or projection, declared per source | Reindex without changing source meaning |

The additive Compose overlay is `deploy/memory-platform.compose.yml`. It does
not modify the protected base Compose file and publishes no database ports.
Its one-shot migration and backfill jobs reuse the existing gateway image and
mount their narrow task sources read-only instead of rebuilding the full
application image for every isolated project.
The governance service and its migrator are the only members of the dedicated
internal-only `governance_boundary` network; the gateway is not attached.

## Executed baseline (2026-07-22)

- Source-ledger inventory classified every discovered source; none remain
  unclassified.
- The durable-memory and governance PostgreSQL services are provisioned on
  distinct named volumes with distinct generated credentials.
- Both checksummed initial migrations are applied and verified.
- `creative.aesthetics` is backfilled at exact parity (1/1) and remains on the
  legacy read route.
- `creative.tensions` is backfilled at exact parity (4/4) and remains on the
  legacy read route.
- A checksummed backup of both databases has passed an isolated restore drill.

This baseline is deliberately short of dual-write and shadow-read phases.
Those phases require live-path wiring and the approval boundaries below.

## Durable memory systems

The machine-readable authority is `app.memory_platform.registry`. Every
durable memory system maps to one table with its own HNSW and GIN indexes.

| Schema | Memory systems |
|---|---|
| `knowledge` | episteme, philosophy claims/counterclaims, enterprise knowledge |
| `autobiographical` | experiential, episodic-full, episodic-curated, narrative, affect |
| `identity_memory` | beliefs, predictions, prediction errors |
| `procedural` | skills, trajectory lessons, transfer insights, learned policies |
| `creative` | fiction, aesthetics, unresolved tensions, ideas |
| `tenant_memory` | project documents, experiences, lessons with forced RLS |
| `memory_admin` | source registry, edges, outbox, migration state, retrieval audit |

PostgreSQL `memory_edges` replaces the present small Neo4j projection after a
successful equivalence soak. It records typed links such as `supports`,
`contradicts`, `promoted_from`, `continues`, and `inspired_by` without making
the connected records epistemically identical.

## Access model

Agents never receive database owner credentials. The typed broker checks an
`ActorRole` and ownership/tenant context. Database group roles add a second
boundary. Production LOGIN roles should be created separately and granted only
the required NOLOGIN groups.

Examples:

| Process | Broker role | Database groups |
|---|---|---|
| Research retrieval | `researcher` | `memory_factual_reader`, tenant reader when scoped |
| Writer creativity | `writer` | factual + fiction + creative-evaluative readers |
| Ordinary self-reflection | `self_reflection` | curated + private-identity readers |
| Retrospective promotion | `retrospective` | deep-recall reader + narrowly scoped autobiographical writer |
| Knowledge ingestion | `knowledge_ingester` | knowledge ingester; fiction ingester only for fiction sources |
| Reconciler | `reconciler` | reconciler plus one target writer at a time |
| Audit | `auditor` | memory auditor; separate governance auditor credential |

No role corresponding to Self-Improver receives governance writer membership.
Learned policies are procedural memories, not safety or evaluation criteria.
Fiction and creative-evaluative grants are distinct, as are public beliefs and
private identity state; a process cannot obtain the broader category merely to
read one neighboring table.

## Retrieval contract

Every result includes:

- memory-space key;
- epistemic class;
- source URI and provenance;
- source record ID and content hash;
- backend and retrieval score;
- bridge name when cross-space retrieval was used.

The broker rejects backend results whose space or epistemic class differs from
the requested registry entry. It also rejects missing provenance, tenant
leakage, and agent-private ownership leakage.

The only initial cross-space bridges are:

- `factual_context`;
- `self_reflection`;
- `creative_blend`;
- `retrospective_review`;
- `procedural_transfer`.

## Migration protocol

Each space moves independently:

```text
discovered -> schema_ready -> backfilled -> dual_write -> shadow_read
           -> ready -> cutover -> soak -> retired
```

Any non-retired phase may enter `aborted` through the declared state machine.
Rollback transitions return shadow/cutover/soak to the last safe read route.
`cutover` and `retired` require an operator approval identifier.

Before cutover, the legacy source remains authoritative. A stable source ID and
content SHA-256 make target writes idempotent. Changes are delivered through a
durable ledger/outbox, never untracked best-effort dual writes. After cutover,
the durable table and its transactional outbox become canonical and downstream
Chroma/wiki projections consume outbox events.

### Cutover gates

Default gates are:

- at least 500 shadow queries and seven representative days;
- mean NDCG@10 at least 0.90 against the legacy ranking;
- 100% provenance completeness;
- zero permission violations;
- 100% write/checksum parity;
- no unresolved outbox events;
- outbox lag no more than 300 seconds;
- successful backup and isolated restore drill.

Low-volume pilots may use an operator-approved replay corpus, but boundary
violations are never waivable.

## Migration order

1. Aesthetics and tensions as low-volume technical pilots.
2. Episteme and philosophy.
3. Enterprise knowledge.
4. Fiction, with negative permission tests.
5. Procedural memories.
6. Experiential and narrative memory.
7. Beliefs and typed edges.
8. Tenant/business memory after RLS adversarial tests.
9. Full and curated SubIA episodes last.

The large `prosocial_game` population is not copied into ordinary live recall.
It remains in a read-only rollback/archive source while aggregates and audited
exemplars are generated. Deletion or compaction requires a separate operator
retention decision.

## Operator commands

Validate migrations without connecting:

```bash
.venv/bin/python scripts/apply_memory_platform_migrations.py --target durable --dry-run
.venv/bin/python scripts/apply_memory_platform_migrations.py --target governance --dry-run
```

Inventory source ledgers without opening or mutating Chroma:

```bash
.venv/bin/python scripts/memory_platform_admin.py inventory \
  --workspace-root workspace --chroma-root workspace
```

Create and inspect migration states:

```bash
.venv/bin/python scripts/memory_platform_admin.py init creative.aesthetics creative.tensions
.venv/bin/python scripts/memory_platform_admin.py status creative.aesthetics
.venv/bin/python scripts/memory_platform_admin.py readiness creative.aesthetics
```

Start isolated databases only after providing new secrets:

```bash
.venv/bin/python scripts/provision_memory_platform_secrets.py --env-file .env
docker compose -f docker-compose.yml -f deploy/memory-platform.compose.yml \
  --profile memory-platform up -d durable-memory-postgres governance-postgres
```

Apply migrations with the one-shot services. Migration files are checksummed;
editing an applied migration is refused.

Backfill one durable space only through the single-writer guard, then record
completion only when independently verified source and target counts match:

```bash
deploy/scripts/memory-platform-backfill.sh creative.aesthetics --batch-size 100
.venv/bin/python scripts/memory_platform_admin.py record-backfill \
  creative.aesthetics --expected-records 1 --migrated-records 1 \
  --source-checkpoint sha256:<source-ledger-sha256>
```

Create a checksummed backup and prove it can be restored into disposable,
isolated volumes:

```bash
deploy/scripts/memory-platform-backup.sh
deploy/scripts/memory-platform-restore-drill.sh \
  workspace/backups/memory-platform/latest/manifest.json
```

## Approval boundaries

The current foundation is additive and does not modify protected live paths.
The following later actions require explicit operator review:

1. wiring `app/retrieval/orchestrator.py` to the typed broker;
2. changing fiction integration or its immutable access prompt;
3. wiring full/curated stores into `app/subia` and regenerating its integrity manifest;
4. changing evaluation/governance infrastructure writers;
5. performing a production read cutover;
6. retiring Chroma collections, Neo4j, indexes, or historical experience rows.
