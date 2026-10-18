from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from intent_sql_agent.contracts import ExecutionRecord


class ReadOnlySQLiteExecutor:
    def __init__(self, *, timeout_seconds: float = 30.0, max_rows: int = 100_000) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows

    def execute(self, database: Path, sql: str) -> ExecutionRecord:
        sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        started = time.perf_counter()
        database = database.resolve()
        if not database.is_file():
            return self._failure(sql_hash, started, "runtime_error", "DATABASE_NOT_FOUND", str(database))

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
                timeout=self.timeout_seconds,
            )
            connection.execute("PRAGMA query_only = ON")
            deadline = time.perf_counter() + self.timeout_seconds

            def stop_after_deadline() -> int:
                return int(time.perf_counter() >= deadline)

            connection.set_progress_handler(stop_after_deadline, 1_000)
            cursor = connection.execute(sql)
            if cursor.description is None:
                return self._failure(
                    sql_hash,
                    started,
                    "invalid_statement",
                    "RESULT_SET_REQUIRED",
                    "Only statements returning a result set are allowed",
                )
            columns = tuple(item[0] for item in cursor.description)
            fetched = cursor.fetchmany(self.max_rows + 1)
            truncated = len(fetched) > self.max_rows
            rows = tuple(tuple(row) for row in fetched[: self.max_rows])
            return ExecutionRecord(
                sql_hash=sql_hash,
                status="success",
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                latency_ms=(time.perf_counter() - started) * 1_000,
            )
        except sqlite3.OperationalError as exc:
            message = str(exc)
            timed_out = "interrupted" in message.lower()
            return self._failure(
                sql_hash,
                started,
                "timeout" if timed_out else "runtime_error",
                "SQL_TIMEOUT" if timed_out else "SQLITE_OPERATIONAL_ERROR",
                message,
            )
        except sqlite3.DatabaseError as exc:
            return self._failure(
                sql_hash,
                started,
                "runtime_error",
                "SQLITE_DATABASE_ERROR",
                str(exc),
            )
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _failure(
        sql_hash: str,
        started: float,
        status: str,
        error_code: str,
        error_message: str,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            sql_hash=sql_hash,
            status=status,  # type: ignore[arg-type]
            latency_ms=(time.perf_counter() - started) * 1_000,
            error_code=error_code,
            error_message=error_message,
        )
