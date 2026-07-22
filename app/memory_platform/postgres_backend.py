"""PostgreSQL/pgvector adapter for independently indexed durable spaces."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from app.memory_platform.models import (
    EpistemicClass,
    MemoryRecord,
    MemorySpace,
    Principal,
    RecallResult,
)


class Cursor(Protocol):
    description: Sequence[Sequence[object]] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> None: ...

    def fetchall(self) -> list[Sequence[object]]: ...

    def __enter__(self) -> "Cursor": ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], Connection]


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


@contextmanager
def _connection(factory: ConnectionFactory) -> Iterator[Connection]:
    conn = factory()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class PgVectorBackend:
    """Hybrid vector/FTS backend.

    SQL identifiers come only from the validated in-process registry.  Caller
    input is always passed as query parameters.
    """

    name = "durable_pgvector"

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        suppress_outbox: bool = False,
    ) -> None:
        self._connection_factory = connection_factory
        self._suppress_outbox = suppress_outbox

    def search(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> list[RecallResult]:
        clauses = ["status = 'active'"]
        params: list[object] = []
        for key, value in filters.items():
            if key not in {"tenant_id", "workspace_id", "owner_agent_id", "status"}:
                raise ValueError(f"unsupported durable-memory filter: {key}")
            clauses.append(f"{key} = %s")
            params.append(value)

        if embedding is not None:
            score_sql = """
                (0.75 * (1.0 - (embedding <=> %s::vector))
                 + 0.25 * ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)))
            """
            params = [_vector_literal(embedding), query, *params]
            order_sql = "score DESC"
        else:
            score_sql = "ts_rank_cd(search_vector, websearch_to_tsquery('english', %s))"
            params = [query, *params]
            clauses.append("search_vector @@ websearch_to_tsquery('english', %s)")
            params.append(query)
            order_sql = "score DESC"

        sql = f"""
            SELECT memory_id::text, content, source_uri, source_record_id,
                   encode(content_sha256, 'hex') AS content_sha256,
                   epistemic_class, provenance, owner_agent_id, tenant_id,
                   workspace_id, event_time, confidence, salience, significance,
                   valence, status, schema_version, attributes, created_at,
                   {score_sql} AS score
              FROM {space.qualified_table}
             WHERE {' AND '.join(clauses)}
             ORDER BY {order_sql}, memory_id
             LIMIT %s
        """
        params.append(limit)

        with _connection(self._connection_factory) as conn:
            with conn.cursor() as cur:
                if space.tenant_scoped:
                    cur.execute("SELECT set_config('app.tenant_id', %s, true)", [principal.tenant_id])
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [self._row_to_result(space, row) for row in rows]

    def put(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        record: MemoryRecord,
    ) -> MemoryRecord:
        sql, params = self._put_statement(space, record)
        with _connection(self._connection_factory) as conn:
            with conn.cursor() as cur:
                self._prepare_write_session(cur, space, principal)
                cur.execute(sql, params)
            conn.commit()
        return record

    def put_many(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        records: Sequence[MemoryRecord],
    ) -> int:
        """Idempotently write a batch in one transaction."""

        if not records:
            return 0
        with _connection(self._connection_factory) as conn:
            with conn.cursor() as cur:
                self._prepare_write_session(cur, space, principal)
                for record in records:
                    sql, params = self._put_statement(space, record)
                    cur.execute(sql, params)
            conn.commit()
        return len(records)

    def set_status(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        source_record_id: str,
        status: str,
    ) -> int:
        """Set lifecycle status by stable source identity; used for tombstones."""

        if status not in {"active", "superseded", "retracted", "archived"}:
            raise ValueError(f"unsupported memory status: {status}")
        from app.memory_platform.broker import stable_memory_id

        memory_id, _ = stable_memory_id(space.key, source_record_id, "")
        sql = f"""
            UPDATE {space.qualified_table}
               SET status = %s, updated_at = now()
             WHERE memory_id = %s::uuid
        """
        with _connection(self._connection_factory) as conn:
            with conn.cursor() as cur:
                self._prepare_write_session(cur, space, principal)
                cur.execute(sql, [status, memory_id])
                changed = int(getattr(cur, "rowcount", 0))
            conn.commit()
        return changed

    def _prepare_write_session(
        self,
        cur: Cursor,
        space: MemorySpace,
        principal: Principal,
    ) -> None:
        if self._suppress_outbox:
            cur.execute("SELECT set_config('memory_platform.suppress_outbox', 'on', true)")
        if space.tenant_scoped:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                [principal.tenant_id],
            )

    @staticmethod
    def _put_statement(
        space: MemorySpace,
        record: MemoryRecord,
    ) -> tuple[str, list[object]]:
        sql = f"""
            INSERT INTO {space.qualified_table} (
                memory_id, content, embedding, source_uri, source_record_id,
                content_sha256, epistemic_class, provenance, owner_agent_id,
                tenant_id, workspace_id, event_time, confidence, salience,
                significance, valence, status, schema_version, attributes,
                created_at, updated_at
            ) VALUES (
                %s::uuid, %s, %s::vector, %s, %s, decode(%s, 'hex'), %s, %s::jsonb,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now()
            )
            ON CONFLICT (memory_id) DO UPDATE SET
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                source_uri = EXCLUDED.source_uri,
                source_record_id = EXCLUDED.source_record_id,
                content_sha256 = EXCLUDED.content_sha256,
                provenance = EXCLUDED.provenance,
                confidence = EXCLUDED.confidence,
                salience = EXCLUDED.salience,
                significance = EXCLUDED.significance,
                valence = EXCLUDED.valence,
                status = EXCLUDED.status,
                schema_version = EXCLUDED.schema_version,
                attributes = EXCLUDED.attributes,
                updated_at = now()
            WHERE {space.qualified_table}.content_sha256 <> EXCLUDED.content_sha256
               OR {space.qualified_table}.schema_version < EXCLUDED.schema_version
        """
        embedding = _vector_literal(record.embedding) if record.embedding is not None else None
        params = [
            record.memory_id,
            record.content,
            embedding,
            record.source_uri,
            record.source_record_id,
            record.content_sha256,
            record.epistemic_class.value,
            json.dumps(dict(record.provenance), sort_keys=True),
            record.owner_agent_id,
            record.tenant_id,
            record.workspace_id,
            record.event_time,
            record.confidence,
            record.salience,
            record.significance,
            record.valence,
            record.status,
            record.schema_version,
            json.dumps(dict(record.attributes), sort_keys=True),
            record.created_at,
        ]
        return sql, params

    @staticmethod
    def _row_to_result(space: MemorySpace, row: Sequence[Any]) -> RecallResult:
        epistemic = EpistemicClass(str(row[5]))
        provenance = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
        attributes = row[17] if isinstance(row[17], dict) else json.loads(row[17] or "{}")
        created_at = row[18]
        if not isinstance(created_at, datetime):
            created_at = datetime.fromisoformat(str(created_at))
        record = MemoryRecord(
            memory_id=str(row[0]),
            space=space.key,
            content=str(row[1]),
            source_uri=str(row[2]),
            source_record_id=str(row[3]),
            content_sha256=str(row[4]),
            epistemic_class=epistemic,
            provenance=provenance,
            owner_agent_id=row[7],
            tenant_id=row[8],
            workspace_id=row[9],
            event_time=row[10],
            confidence=row[11],
            salience=row[12],
            significance=row[13],
            valence=row[14],
            status=str(row[15]),
            schema_version=int(row[16]),
            attributes=attributes,
            created_at=created_at,
        )
        return RecallResult(
            record=record,
            score=float(row[19]),
            backend=PgVectorBackend.name,
        )
