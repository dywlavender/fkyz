from __future__ import annotations

"""Indexed persistence for runtime history and execution records.

The application used several independently rewritten JSON arrays for runtime
state.  That is convenient for a prototype, but every list or update becomes
an O(n) parse plus an O(n) rewrite as history grows.  This module keeps the
legacy files as migration input and makes SQLite the runtime source of truth.

The database is deliberately small and boring: JSON payloads preserve the
existing API contract while scalar columns and indexes serve list, filter,
latest-run and group-summary queries without loading detail arrays.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from risk_review_web.paths import (
    REVIEW_RECORDS_PATH,
    RUNTIME_DB_PATH,
    SCHEDULED_RUNS_PATH,
    SCHEDULED_TASKS_PATH,
    VALIDATION_RUNS_PATH,
)


class RuntimeDatabaseError(RuntimeError):
    """Raised when the runtime database cannot be initialized safely."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeDatabaseError(f"运行状态迁移失败：无法解析 {path}") from exc
    return value


def _as_list(path: Path) -> list[dict[str, Any]]:
    value = _load_json(path, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeDatabaseError(f"运行状态迁移失败：{path.name} 顶层必须是对象数组")
    return value


def _summary_payload(payload: dict[str, Any], detail_keys: Iterable[str] = ("items",)) -> dict[str, Any]:
    result = dict(payload)
    for key in detail_keys:
        result.pop(key, None)
    return result


def _review_artifact_names(record: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for group_name in ("standardArtifacts", "reviewArtifacts"):
        group = record.get(group_name) or {}
        if isinstance(group, dict):
            names.update(str(value) for value in group.values() if value)
    for item in record.get("items") or []:
        if not isinstance(item, dict):
            continue
        names.update(
            str(item[key])
            for key in ("standardFileId", "standardMetadataFileId", "reviewFileId")
            if item.get(key)
        )
    return names


class RuntimeDatabase:
    """Small SQLite gateway shared by runtime history repositories."""

    def __init__(self, path: Path = RUNTIME_DB_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS review_records (
                        record_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        task_status TEXT NOT NULL DEFAULT '',
                        conclusion TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        factor_name TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT '',
                        archived INTEGER NOT NULL DEFAULT 0,
                        deleted INTEGER NOT NULL DEFAULT 0,
                        summary_json TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_review_records_order
                        ON review_records(archived, deleted, updated_at DESC, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_review_records_filter
                        ON review_records(source, task_status, conclusion, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_review_records_task
                        ON review_records(task_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS review_record_artifacts (
                        record_id TEXT NOT NULL,
                        file_name TEXT NOT NULL,
                        PRIMARY KEY(record_id, file_name),
                        FOREIGN KEY(record_id) REFERENCES review_records(record_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_review_record_artifacts_file
                        ON review_record_artifacts(file_name, record_id);

                    CREATE TABLE IF NOT EXISTS scheduled_tasks (
                        task_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 0,
                        item_count INTEGER NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_order
                        ON scheduled_tasks(created_at DESC);

                    CREATE TABLE IF NOT EXISTS scheduled_runs (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL DEFAULT '',
                        finished_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT '',
                        summary_json TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_scheduled_runs_task
                        ON scheduled_runs(task_id, started_at DESC, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_scheduled_runs_order
                        ON scheduled_runs(started_at DESC, created_at DESC);

                    CREATE TABLE IF NOT EXISTS validation_runs (
                        run_id TEXT PRIMARY KEY,
                        group_id TEXT NOT NULL DEFAULT '',
                        case_id TEXT NOT NULL DEFAULT '',
                        task_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL DEFAULT '',
                        finished_at TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_validation_runs_group
                        ON validation_runs(group_id, finished_at DESC, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_validation_runs_case
                        ON validation_runs(case_id, finished_at DESC, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_validation_runs_task
                        ON validation_runs(task_id, status);
                    """
                )
                self._migrate_legacy_locked(connection)
            except sqlite3.Error as exc:
                raise RuntimeDatabaseError(f"无法初始化运行数据库：{self.path}") from exc
            finally:
                connection.close()

    def _migrate_legacy_locked(self, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            "SELECT value FROM runtime_meta WHERE key = ?",
            ("legacy_json_v1",),
        ).fetchone()
        if marker:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Re-check after acquiring the write lock so two processes starting
            # together never import the same legacy rows twice.
            marker = connection.execute(
                "SELECT value FROM runtime_meta WHERE key = ?",
                ("legacy_json_v1",),
            ).fetchone()
            if marker:
                connection.execute("COMMIT")
                return
            self._import_review_records(connection, _as_list(REVIEW_RECORDS_PATH))
            self._import_scheduled_tasks(connection, _as_list(SCHEDULED_TASKS_PATH))
            self._import_scheduled_runs(connection, _as_list(SCHEDULED_RUNS_PATH))
            self._import_validation_runs(connection, _as_list(VALIDATION_RUNS_PATH))
            connection.execute(
                "INSERT INTO runtime_meta(key, value) VALUES(?, ?)",
                ("legacy_json_v1", "complete"),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _import_review_records(connection: sqlite3.Connection, records: list[dict[str, Any]]) -> None:
        for record in records[-5000:]:
            RuntimeDatabase._upsert_review_record(connection, record)

    @staticmethod
    def _import_scheduled_tasks(connection: sqlite3.Connection, tasks: list[dict[str, Any]]) -> None:
        for task in tasks:
            RuntimeDatabase._upsert_scheduled_task(connection, task)

    @staticmethod
    def _import_scheduled_runs(connection: sqlite3.Connection, runs: list[dict[str, Any]]) -> None:
        for run in runs[-1000:]:
            RuntimeDatabase._upsert_scheduled_run(connection, run)

    @staticmethod
    def _import_validation_runs(connection: sqlite3.Connection, runs: list[dict[str, Any]]) -> None:
        for run in runs[-1000:]:
            RuntimeDatabase._upsert_validation_run(connection, run)

    @staticmethod
    def _upsert_review_record(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
        record_id = str(record.get("recordId") or "")
        if not record_id:
            return
        payload = dict(record)
        summary = _summary_payload(payload)
        connection.execute(
            """
            INSERT INTO review_records(
                record_id, task_id, source, task_status, conclusion, title,
                factor_name, created_at, updated_at, archived, deleted,
                summary_json, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                task_id=excluded.task_id,
                source=excluded.source,
                task_status=excluded.task_status,
                conclusion=excluded.conclusion,
                title=excluded.title,
                factor_name=excluded.factor_name,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                archived=excluded.archived,
                deleted=excluded.deleted,
                summary_json=excluded.summary_json,
                payload_json=excluded.payload_json
            """,
            (
                record_id,
                str(payload.get("taskId") or ""),
                str(payload.get("source") or ""),
                str(payload.get("taskStatus") or payload.get("status") or ""),
                str(payload.get("conclusion") or ""),
                str(payload.get("title") or ""),
                str(payload.get("factorName") or ""),
                str(payload.get("createdAt") or ""),
                str(payload.get("updatedAt") or payload.get("createdAt") or ""),
                int(bool(payload.get("archived"))),
                int(bool(payload.get("deleted"))),
                _json(summary),
                _json(payload),
            ),
        )
        connection.execute("DELETE FROM review_record_artifacts WHERE record_id = ?", (record_id,))
        connection.executemany(
            "INSERT OR IGNORE INTO review_record_artifacts(record_id, file_name) VALUES(?, ?)",
            [(record_id, file_name) for file_name in _review_artifact_names(payload)],
        )

    @staticmethod
    def _upsert_scheduled_task(connection: sqlite3.Connection, task: dict[str, Any]) -> None:
        task_id = str(task.get("taskId") or "")
        if not task_id:
            return
        payload = dict(task)
        connection.execute(
            """
            INSERT INTO scheduled_tasks(task_id, created_at, updated_at, enabled, item_count, payload_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                enabled=excluded.enabled,
                item_count=excluded.item_count,
                payload_json=excluded.payload_json
            """,
            (
                task_id,
                str(payload.get("createdAt") or ""),
                str(payload.get("updatedAt") or payload.get("createdAt") or ""),
                int(bool(payload.get("enabled"))),
                len(payload.get("items") or []),
                _json(payload),
            ),
        )

    @staticmethod
    def _upsert_scheduled_run(connection: sqlite3.Connection, run: dict[str, Any]) -> None:
        run_id = str(run.get("runId") or "")
        if not run_id:
            return
        payload = dict(run)
        connection.execute(
            """
            INSERT INTO scheduled_runs(
                run_id, task_id, status, started_at, finished_at, created_at,
                summary_json, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                task_id=excluded.task_id,
                status=excluded.status,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                created_at=excluded.created_at,
                summary_json=excluded.summary_json,
                payload_json=excluded.payload_json
            """,
            (
                run_id,
                str(payload.get("taskId") or ""),
                str(payload.get("status") or ""),
                str(payload.get("startedAt") or ""),
                str(payload.get("finishedAt") or ""),
                str(payload.get("createdAt") or ""),
                _json(_summary_payload(payload)),
                _json(payload),
            ),
        )

    @staticmethod
    def _upsert_validation_run(connection: sqlite3.Connection, run: dict[str, Any]) -> None:
        run_id = str(run.get("runId") or "")
        if not run_id:
            return
        payload = dict(run)
        connection.execute(
            """
            INSERT INTO validation_runs(
                run_id, group_id, case_id, task_id, status, started_at,
                finished_at, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                group_id=excluded.group_id,
                case_id=excluded.case_id,
                task_id=excluded.task_id,
                status=excluded.status,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                payload_json=excluded.payload_json
            """,
            (
                run_id,
                str(payload.get("groupId") or run_id),
                str(payload.get("caseId") or ""),
                str(payload.get("taskId") or ""),
                str(payload.get("status") or ""),
                str(payload.get("startedAt") or ""),
                str(payload.get("finishedAt") or ""),
                _json(payload),
            ),
        )

    def _fetch_payload(self, table: str, key_column: str, key: str) -> dict[str, Any] | None:
        if table not in {"review_records", "scheduled_tasks", "scheduled_runs", "validation_runs"}:
            raise ValueError("invalid runtime table")
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    f"SELECT payload_json FROM {table} WHERE {key_column} = ?",
                    (str(key),),
                ).fetchone()
                return json.loads(row[0]) if row else None
            finally:
                connection.close()

    # Review records -----------------------------------------------------
    def upsert_review_record(self, record: dict[str, Any]) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_review_record(connection, record)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def list_review_records(
        self,
        *,
        include_deleted: bool = False,
        include_archived: bool = True,
        summaries: bool = False,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if not include_deleted:
            where.append("deleted = 0")
        if not include_archived:
            where.append("archived = 0")
        condition = f"WHERE {' AND '.join(where)}" if where else ""
        column = "summary_json" if summaries else "payload_json"
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    f"SELECT {column} FROM review_records {condition} ORDER BY updated_at DESC, created_at DESC",
                    params,
                ).fetchall()
                return [json.loads(row[0]) for row in rows]
            finally:
                connection.close()

    def query_review_records(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        where = ["deleted = 0", "archived = ?"]
        params: list[Any] = [int(bool(filters.get("archived")))]
        keyword = str(filters.get("keyword") or "").strip().lower()
        if keyword:
            like = f"%{keyword}%"
            where.append("lower(title || ' ' || factor_name || ' ' || record_id || ' ' || task_id) LIKE ?")
            params.append(like)
        for column, key in (("source", "source"), ("task_status", "task_status"), ("conclusion", "conclusion")):
            value = str(filters.get(key) or "").strip()
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        date_from = str(filters.get("date_from") or "").strip()
        date_to = str(filters.get("date_to") or "").strip()
        if date_from:
            where.append("updated_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("updated_at <= ?")
            params.append(date_to)
        condition = " AND ".join(where)
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_limit") or 100), int(filters.get("page_size") or 20)))
        offset = (page - 1) * page_size
        with self.lock:
            connection = self._connect()
            try:
                total = int(connection.execute(f"SELECT COUNT(*) FROM review_records WHERE {condition}", params).fetchone()[0])
                rows = connection.execute(
                    f"""
                    SELECT summary_json FROM review_records
                    WHERE {condition}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, page_size, offset],
                ).fetchall()
                return [json.loads(row[0]) for row in rows], total
            finally:
                connection.close()

    def get_review_record(self, record_id: str, *, include_deleted: bool = False, summary: bool = False) -> dict[str, Any] | None:
        where = "record_id = ?" if include_deleted else "record_id = ? AND deleted = 0"
        column = "summary_json" if summary else "payload_json"
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(f"SELECT {column} FROM review_records WHERE {where}", (str(record_id),)).fetchone()
                return json.loads(row[0]) if row else None
            finally:
                connection.close()

    def get_review_record_by_task(self, task_id: str, *, include_archived: bool = True) -> dict[str, Any] | None:
        condition = "task_id = ? AND deleted = 0"
        params: list[Any] = [str(task_id)]
        if not include_archived:
            condition += " AND archived = 0"
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    f"SELECT payload_json FROM review_records WHERE {condition} ORDER BY updated_at DESC LIMIT 1",
                    params,
                ).fetchone()
                return json.loads(row[0]) if row else None
            finally:
                connection.close()

    def archive_review_record(self, record_id: str, archived: bool) -> dict[str, Any] | None:
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT payload_json FROM review_records WHERE record_id = ? AND deleted = 0",
                    (str(record_id),),
                ).fetchone()
                if not row:
                    return None
                record = json.loads(row[0])
                record["archived"] = bool(archived)
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_review_record(connection, record)
                connection.execute("COMMIT")
                return record
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def review_artifact_references(self, record_id: str) -> set[str]:
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT DISTINCT a.file_name
                    FROM review_record_artifacts a
                    JOIN review_records r ON r.record_id = a.record_id
                    WHERE a.record_id != ? AND r.deleted = 0
                    """,
                    (str(record_id),),
                ).fetchall()
                return {str(row[0]) for row in rows}
            finally:
                connection.close()

    def replace_review_tombstone(self, record_id: str, tombstone: dict[str, Any]) -> None:
        self.upsert_review_record(tombstone)

    def review_record_count(self) -> int:
        with self.lock:
            connection = self._connect()
            try:
                return int(connection.execute("SELECT COUNT(*) FROM review_records").fetchone()[0])
            finally:
                connection.close()

    # Scheduled tasks and runs -----------------------------------------
    def upsert_scheduled_task(self, task: dict[str, Any]) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_scheduled_task(connection, task)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def list_scheduled_tasks(self, *, summaries: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT task_id, item_count, payload_json FROM scheduled_tasks ORDER BY created_at DESC"
                ).fetchall()
                result = []
                for row in rows:
                    payload = json.loads(row[2])
                    if summaries:
                        payload.pop("items", None)
                        payload["items"] = []
                        payload["itemCount"] = int(row[1])
                    result.append(payload)
                return result
            finally:
                connection.close()

    def get_scheduled_task(self, task_id: str) -> dict[str, Any] | None:
        return self._fetch_payload("scheduled_tasks", "task_id", task_id)

    def delete_scheduled_task(self, task_id: str) -> bool:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (str(task_id),))
                connection.execute("DELETE FROM scheduled_runs WHERE task_id = ?", (str(task_id),))
                connection.execute("COMMIT")
                return cursor.rowcount > 0
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def upsert_scheduled_run(self, run: dict[str, Any]) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_scheduled_run(connection, run)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def update_scheduled_run(self, run_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT payload_json FROM scheduled_runs WHERE run_id = ?", (str(run_id),)).fetchone()
                if not row:
                    return None
                payload = json.loads(row[0])
                payload.update(patch)
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_scheduled_run(connection, payload)
                connection.execute("COMMIT")
                return payload
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def list_scheduled_runs(
        self,
        task_id: str | None = None,
        *,
        page: int | None = None,
        page_size: int = 20,
        summaries: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        where = []
        params: list[Any] = []
        if task_id:
            where.append("task_id = ?")
            params.append(str(task_id))
        condition = f"WHERE {' AND '.join(where)}" if where else ""
        column = "summary_json" if summaries else "payload_json"
        with self.lock:
            connection = self._connect()
            try:
                total = int(connection.execute(f"SELECT COUNT(*) FROM scheduled_runs {condition}", params).fetchone()[0])
                query = f"SELECT {column} FROM scheduled_runs {condition} ORDER BY COALESCE(started_at, created_at) DESC, created_at DESC"
                if page is not None:
                    normalized_page = max(1, int(page))
                    normalized_size = max(1, min(100, int(page_size)))
                    rows = connection.execute(
                        f"{query} LIMIT ? OFFSET ?",
                        [*params, normalized_size, (normalized_page - 1) * normalized_size],
                    ).fetchall()
                    items = []
                    for row in rows:
                        payload = json.loads(row[0])
                        if summaries:
                            payload["items"] = []
                        items.append(payload)
                    return {
                        "items": items,
                        "page": normalized_page,
                        "pageSize": normalized_size,
                        "total": total,
                    }
                rows = connection.execute(query, params).fetchall()
                result = []
                for row in rows:
                    payload = json.loads(row[0])
                    if summaries:
                        payload["items"] = []
                    result.append(payload)
                return result
            finally:
                connection.close()

    def get_scheduled_run(self, run_id: str) -> dict[str, Any] | None:
        return self._fetch_payload("scheduled_runs", "run_id", run_id)

    def latest_scheduled_runs_by_task(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT r.task_id, r.summary_json
                    FROM scheduled_runs r
                    WHERE NOT EXISTS (
                        SELECT 1 FROM scheduled_runs newer
                        WHERE newer.task_id = r.task_id
                          AND COALESCE(newer.started_at, newer.created_at) > COALESCE(r.started_at, r.created_at)
                    )
                    """
                ).fetchall()
                return {str(row[0]): json.loads(row[1]) for row in rows}
            finally:
                connection.close()

    def recover_scheduled_runs(self, now_text: str, error: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT payload_json FROM scheduled_runs WHERE status IN ('pending', 'running')"
                ).fetchall()
                recovered = []
                connection.execute("BEGIN IMMEDIATE")
                for row in rows:
                    payload = json.loads(row[0])
                    payload.update({"status": "failed", "finishedAt": now_text, "error": error, **metadata})
                    self._upsert_scheduled_run(connection, payload)
                    recovered.append(payload)
                connection.execute("COMMIT")
                return recovered
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    # Validation runs ---------------------------------------------------
    def upsert_validation_run(self, run: dict[str, Any]) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._upsert_validation_run(connection, run)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def replace_validation_runs(self, runs: list[dict[str, Any]], *, max_count: int = 1000) -> None:
        """Replace the validation index for maintenance/import callers."""
        ordered = sorted(
            runs,
            key=lambda item: item.get("finishedAt") or item.get("startedAt") or "",
            reverse=True,
        )[:max_count]
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM validation_runs")
                for run in ordered:
                    self._upsert_validation_run(connection, run)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def get_validation_run(self, run_id: str) -> dict[str, Any] | None:
        return self._fetch_payload("validation_runs", "run_id", run_id)

    def list_validation_runs(
        self,
        *,
        case_id: str | None = None,
        group_id: str | None = None,
        latest_by_case: bool = False,
        page: int | None = None,
        page_size: int = 20,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        where = []
        params: list[Any] = []
        if case_id:
            where.append("case_id = ?")
            params.append(str(case_id))
        if group_id:
            where.append("group_id = ?")
            params.append(str(group_id))
        if latest_by_case:
            where.append(
                "NOT EXISTS (SELECT 1 FROM validation_runs newer WHERE newer.case_id = validation_runs.case_id "
                "AND ("
                "COALESCE(newer.finished_at, newer.started_at) > COALESCE(validation_runs.finished_at, validation_runs.started_at) "
                "OR (COALESCE(newer.finished_at, newer.started_at) = COALESCE(validation_runs.finished_at, validation_runs.started_at) "
                "AND newer.run_id > validation_runs.run_id)"
                "))"
            )
        condition = f"WHERE {' AND '.join(where)}" if where else ""
        with self.lock:
            connection = self._connect()
            try:
                total = int(connection.execute(f"SELECT COUNT(*) FROM validation_runs {condition}", params).fetchone()[0])
                query = f"SELECT payload_json FROM validation_runs {condition} ORDER BY COALESCE(finished_at, started_at) DESC, run_id DESC"
                if page is not None:
                    normalized_page = max(1, int(page))
                    normalized_size = max(1, min(100, int(page_size)))
                    rows = connection.execute(
                        f"{query} LIMIT ? OFFSET ?",
                        [*params, normalized_size, (normalized_page - 1) * normalized_size],
                    ).fetchall()
                    return {
                        "items": [json.loads(row[0]) for row in rows],
                        "page": normalized_page,
                        "pageSize": normalized_size,
                        "total": total,
                    }
                rows = connection.execute(query, params).fetchall()
                return [json.loads(row[0]) for row in rows]
            finally:
                connection.close()

    def query_validation_groups(self, *, page: int, page_size: int) -> dict[str, Any]:
        normalized_page = max(1, int(page))
        normalized_size = max(1, min(100, int(page_size)))
        with self.lock:
            connection = self._connect()
            try:
                total = int(connection.execute("SELECT COUNT(DISTINCT group_id) FROM validation_runs").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT
                        group_id,
                        MIN(NULLIF(started_at, '')) AS started_at,
                        MAX(NULLIF(finished_at, '')) AS finished_at,
                        COUNT(*) AS total,
                        SUM(CASE WHEN status IN ('success', 'failed', 'cancelled') AND json_extract(payload_json, '$.matched') = 1 THEN 1 ELSE 0 END) AS matched,
                        SUM(CASE WHEN status = 'success' AND json_extract(payload_json, '$.matched') = 0 THEN 1 ELSE 0 END) AS mismatched,
                        SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                        SUM(CASE WHEN status IN ('pending', 'queued', 'running') THEN 1 ELSE 0 END) AS running
                    FROM validation_runs
                    GROUP BY group_id
                    ORDER BY COALESCE(MAX(NULLIF(finished_at, '')), MIN(NULLIF(started_at, ''))) DESC
                    LIMIT ? OFFSET ?
                    """,
                    (normalized_size, (normalized_page - 1) * normalized_size),
                ).fetchall()
                items = []
                for row in rows:
                    run_count = int(row[3] or 0)
                    matched = int(row[4] or 0)
                    mismatched = int(row[5] or 0)
                    failed = int(row[6] or 0)
                    running = int(row[7] or 0) > 0
                    items.append({
                        "groupId": str(row[0]),
                        "startedAt": row[1] or "",
                        "finishedAt": row[2] or "",
                        "total": run_count,
                        "matched": matched,
                        "mismatched": mismatched,
                        "failed": failed,
                        "status": "running" if running else "error" if failed == run_count else "done",
                        "agreementRate": round(matched / run_count * 100, 1) if run_count else 0,
                    })
                return {
                    "items": items,
                    "page": normalized_page,
                    "pageSize": normalized_size,
                    "total": total,
                }
            finally:
                connection.close()

    def delete_validation_groups(self, group_ids: set[str]) -> None:
        if not group_ids:
            return
        placeholders = ",".join("?" for _ in group_ids)
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(f"DELETE FROM validation_runs WHERE group_id IN ({placeholders})", tuple(group_ids))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def update_validation_runs(self, predicate_sql: str, predicate_params: tuple[Any, ...], updater) -> list[dict[str, Any]]:
        """Update selected validation payloads under one write transaction."""
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(f"SELECT payload_json FROM validation_runs WHERE {predicate_sql}", predicate_params).fetchall()
                updated: list[dict[str, Any]] = []
                connection.execute("BEGIN IMMEDIATE")
                for row in rows:
                    payload = json.loads(row[0])
                    next_payload = updater(payload)
                    if next_payload is None:
                        continue
                    self._upsert_validation_run(connection, next_payload)
                    updated.append(next_payload)
                connection.execute("COMMIT")
                return updated
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()


_DATABASES: dict[str, RuntimeDatabase] = {}
_DATABASES_LOCK = threading.Lock()


def get_runtime_database(path: Path = RUNTIME_DB_PATH) -> RuntimeDatabase:
    """Return one initialized database instance per absolute path."""
    key = str(Path(path).expanduser().resolve())
    with _DATABASES_LOCK:
        database = _DATABASES.get(key)
        if database is None:
            database = RuntimeDatabase(Path(key))
            _DATABASES[key] = database
        return database
