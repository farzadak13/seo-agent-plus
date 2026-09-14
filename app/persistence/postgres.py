from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as exc:  # pragma: no cover
    psycopg = None
    dict_row = None
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None

from app.models.persistence import PersistenceRecord
from app.persistence.contracts import (
    PersistenceConflictError,
    PersistenceNotFoundError,
)


class PostgresRepository:
    """
    PostgreSQL implementation of the database-agnostic Repository contract.

    Records are append-only: replacement inserts a new version instead of
    overwriting the old one. ``get`` returns the latest version.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connection_factory: Callable[..., Any] | None = None,
    ) -> None:
        if psycopg is None:
            raise RuntimeError(
                "psycopg is required for PostgresRepository"
            ) from _PSYCOPG_IMPORT_ERROR

        self._transaction_connection = ContextVar(f"postgres_tx_{id(self)}", default=None)
        self._dsn = dsn
        self._connection_factory = connection_factory or psycopg.connect

    @contextmanager
    def _connect(self):
        existing = self._transaction_connection.get()
        if existing is not None:
            yield existing
            return
        with self._connection_factory(self._dsn, row_factory=dict_row) as connection:
            yield connection

    @contextmanager
    def transaction(self):
        if self._transaction_connection.get() is not None:
            yield
            return
        with self._connect() as connection:
            token = self._transaction_connection.set(connection)
            try:
                yield
            finally:
                self._transaction_connection.reset(token)

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=row["record_id"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            schema_version=row["schema_version"],
            version=row["version"],
            payload=row["payload"],
            created_at=row["created_at"],
            snapshot_id=row["snapshot_id"],
            data_snapshot_id=row["data_snapshot_id"],
            rule_version=row["rule_version"],
            config_version=row["config_version"],
        )

    def create(self, record: PersistenceRecord) -> PersistenceRecord:
        sql = """
        INSERT INTO persistence_records (
            record_id,
            aggregate_type,
            aggregate_id,
            version,
            schema_version,
            payload,
            created_at,
            snapshot_id,
            data_snapshot_id,
            rule_version,
            config_version
        )
        VALUES (
            %(record_id)s,
            %(aggregate_type)s,
            %(aggregate_id)s,
            %(version)s,
            %(schema_version)s,
            %(payload)s::jsonb,
            %(created_at)s,
            %(snapshot_id)s,
            %(data_snapshot_id)s,
            %(rule_version)s,
            %(config_version)s
        )
        """

        with self._connect() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql,
                        {
                            **record.model_dump(mode="json"),
                            "payload": _json_dumps(record.payload),
                        },
                    )
            except Exception as exc:
                connection.rollback()
                if _is_unique_violation(exc):
                    raise PersistenceConflictError(
                        "aggregate/version already exists: "
                        f"{record.aggregate_type}:"
                        f"{record.aggregate_id}:v{record.version}"
                    ) from exc
                raise

        return record

    def get(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
    ) -> PersistenceRecord | None:
        sql = """
        SELECT
            record_id,
            aggregate_type,
            aggregate_id,
            version,
            schema_version,
            payload,
            created_at,
            snapshot_id,
            data_snapshot_id,
            rule_version,
            config_version
        FROM persistence_records
        WHERE aggregate_type = %s
          AND aggregate_id = %s
        ORDER BY version DESC
        LIMIT 1
        """

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (aggregate_type, aggregate_id),
                )
                row = cursor.fetchone()

        if row is None:
            return None

        return self._record_from_row(row)

    def list(
        self,
        *,
        aggregate_type: str | None = None,
        limit: int | None = None,
    ) -> Sequence[PersistenceRecord]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")

        clauses: list[str] = []
        params: list[Any] = []

        if aggregate_type is not None:
            clauses.append("aggregate_type = %s")
            params.append(aggregate_type)

        where = (
            f"WHERE {' AND '.join(clauses)}"
            if clauses
            else ""
        )

        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT %s"
            params.append(limit)

        sql = f"""
        SELECT DISTINCT ON (aggregate_type, aggregate_id)
            record_id,
            aggregate_type,
            aggregate_id,
            version,
            schema_version,
            payload,
            created_at,
            snapshot_id,
            data_snapshot_id,
            rule_version,
            config_version
        FROM persistence_records
        {where}
        ORDER BY aggregate_type, aggregate_id, version DESC
        {limit_sql}
        """

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

        return [self._record_from_row(row) for row in rows]

    def replace(
        self,
        record: PersistenceRecord,
        *,
        expected_version: int,
    ) -> PersistenceRecord:
        if record.version != expected_version + 1:
            raise PersistenceConflictError("Replacement must increment expected version by one.")
        insert_sql = """
        INSERT INTO persistence_records (
            record_id,
            aggregate_type,
            aggregate_id,
            version,
            schema_version,
            payload,
            created_at,
            snapshot_id,
            data_snapshot_id,
            rule_version,
            config_version
        )
        SELECT
            %(record_id)s,
            %(aggregate_type)s,
            %(aggregate_id)s,
            %(version)s,
            %(schema_version)s,
            %(payload)s::jsonb,
            %(created_at)s,
            %(snapshot_id)s,
            %(data_snapshot_id)s,
            %(rule_version)s,
            %(config_version)s
        WHERE EXISTS (
            SELECT 1
            FROM persistence_records
            WHERE aggregate_type = %(aggregate_type)s
              AND aggregate_id = %(aggregate_id)s
              AND version = %(expected_version)s
        )
        AND %(version)s = (
            SELECT MAX(version) + 1
            FROM persistence_records
            WHERE aggregate_type = %(aggregate_type)s
              AND aggregate_id = %(aggregate_id)s
        )
        """

        values = {
            **record.model_dump(mode="json"),
            "payload": _json_dumps(record.payload),
            "expected_version": expected_version,
        }

        with self._connect() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(insert_sql, values)
                    if cursor.rowcount != 1:
                        exists = self._aggregate_exists(
                            connection,
                            aggregate_type=record.aggregate_type,
                            aggregate_id=record.aggregate_id,
                        )
                        if not exists:
                            raise PersistenceNotFoundError(
                                "aggregate not found: "
                                f"{record.aggregate_type}:"
                                f"{record.aggregate_id}"
                            )

                        raise PersistenceConflictError(
                            "optimistic concurrency conflict"
                        )
            except (PersistenceConflictError, PersistenceNotFoundError):
                connection.rollback()
                raise
            except Exception as exc:
                connection.rollback()
                if _is_unique_violation(exc):
                    raise PersistenceConflictError(
                        "optimistic concurrency conflict"
                    ) from exc
                raise

        return record

    @staticmethod
    def _aggregate_exists(
        connection: Any,
        *,
        aggregate_type: str,
        aggregate_id: str,
    ) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM persistence_records
                    WHERE aggregate_type = %s
                      AND aggregate_id = %s
                )
                """,
                (aggregate_type, aggregate_id),
            )
            row = cursor.fetchone()

        if isinstance(row, dict):
            return bool(next(iter(row.values())))

        return bool(row[0])


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _is_unique_violation(exc: Exception) -> bool:
    if psycopg is None:
        return False

    return isinstance(
        exc,
        psycopg.errors.UniqueViolation,
    )


__all__ = ["PostgresRepository"]

