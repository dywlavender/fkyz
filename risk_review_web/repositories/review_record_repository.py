from __future__ import annotations

import json
import threading
import time
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from risk_review_web.paths import OUTPUTS_DIR, REVIEW_RECORDS_PATH, ROOT
from risk_review_web.repositories.runtime_database import RuntimeDatabase, get_runtime_database
from risk_review_web.services.artifact_catalog import list_outputs as list_output_artifacts
from risk_review_web.services.review_history import (
    _review_record_artifact_names,
    _task_to_review_record,
    normalize_review_item,
)
from risk_review_web.utils import atomic_write_text, read_text


class ReviewRecordRepositoryError(RuntimeError):
    """Base error for an unavailable or invalid review-history index."""


class ReviewRecordCorruptError(ReviewRecordRepositoryError):
    """The history index exists but cannot be safely interpreted."""


class ReviewRecordRepository:
    """The sole runtime persistence boundary for review history."""

    def __init__(self, path: Path = REVIEW_RECORDS_PATH, outputs_dir: Path = OUTPUTS_DIR):
        self.path = Path(path)
        self.outputs_dir = Path(outputs_dir)
        self.lock = threading.RLock()
        self.database: RuntimeDatabase | None = None
        if self.path.resolve() == REVIEW_RECORDS_PATH.resolve():
            self.database = get_runtime_database()

    def list(self, *, include_deleted: bool = False, include_archived: bool = True) -> list[dict[str, Any]]:
        if self.database is not None:
            return [
                self._normalize(record)
                for record in self.database.list_review_records(
                    include_deleted=include_deleted,
                    include_archived=include_archived,
                )
            ]
        with self.lock:
            records = self._read()
        return [
            self._normalize(record)
            for record in records
            if (include_deleted or not record.get("deleted"))
            and (include_archived or not record.get("archived"))
        ]

    def list_summaries(self, *, include_deleted: bool = False, include_archived: bool = True) -> list[dict[str, Any]]:
        """Return list-safe records without the potentially large ``items`` array."""
        if self.database is not None:
            return [
                self._normalize(record)
                for record in self.database.list_review_records(
                    include_deleted=include_deleted,
                    include_archived=include_archived,
                    summaries=True,
                )
            ]
        return [
            {key: value for key, value in self._normalize(record).items() if key != "items"}
            for record in self.list(include_deleted=include_deleted, include_archived=include_archived)
        ]

    def query_summaries(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        if self.database is not None:
            records, total = self.database.query_review_records(filters)
            return [self._normalize(record) for record in records], total
        records = self.list(include_archived=True)
        archived = bool(filters.get("archived"))
        records = [record for record in records if bool(record.get("archived")) == archived]
        keyword = str(filters.get("keyword") or "").strip().lower()
        if keyword:
            records = [
                record for record in records
                if keyword in " ".join(
                    str(record.get(key) or "")
                    for key in ("title", "factorName", "recordId", "taskId")
                ).lower()
            ]
        for key, aliases in (
            ("source", ("source",)),
            ("taskStatus", ("taskStatus", "task_status")),
            ("conclusion", ("conclusion",)),
        ):
            value = next((str(filters.get(alias) or "").strip() for alias in aliases if filters.get(alias)), "")
            if value:
                records = [record for record in records if str(record.get(key) or record.get("status") or "") == value]
        date_from = str(filters.get("date_from") or "")
        date_to = str(filters.get("date_to") or "")
        if date_from:
            records = [record for record in records if str(record.get("updatedAt") or record.get("createdAt") or "") >= date_from]
        if date_to:
            records = [record for record in records if str(record.get("updatedAt") or record.get("createdAt") or "") <= date_to]
        records.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_limit") or 100), int(filters.get("page_size") or 20)))
        start = (page - 1) * page_size
        return [
            {key: value for key, value in record.items() if key != "items"}
            for record in records[start:start + page_size]
        ], len(records)

    def get(self, record_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        if self.database is not None:
            record = self.database.get_review_record(record_id, include_deleted=include_deleted)
            return self._normalize(record) if record else None
        return next((item for item in self.list(include_deleted=include_deleted) if str(item.get("recordId") or "") == str(record_id)), None)

    def get_by_task_id(self, task_id: str, *, include_archived: bool = True) -> dict[str, Any] | None:
        if self.database is not None:
            record = self.database.get_review_record_by_task(task_id, include_archived=include_archived)
            return self._normalize(record) if record else None
        return next(
            (
                item for item in self.list(include_archived=include_archived)
                if str(item.get("taskId") or "") == str(task_id)
            ),
            None,
        )

    def health(self) -> dict[str, Any]:
        """Check the index without changing or repairing it."""
        if self.database is not None:
            try:
                return {
                    "ok": True,
                    "path": str(self.database.path),
                    "records": self.database.review_record_count(),
                    "error": "",
                }
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "path": str(self.database.path),
                    "records": 0,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
        with self.lock:
            try:
                records = self._read()
            except ReviewRecordRepositoryError as exc:
                return {
                    "ok": False,
                    "path": str(self.path),
                    "records": 0,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
        return {"ok": True, "path": str(self.path), "records": len(records), "error": ""}

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        record_id = str(record.get("recordId") or "").strip()
        if not record_id:
            raise ValueError("历史记录缺少 recordId。")
        if self.database is not None:
            with self.lock:
                existing = self.database.get_review_record(record_id, include_deleted=True)
                if existing and existing.get("deleted"):
                    return existing
                saved = (
                    {**existing, **record, "archived": bool(record.get("archived", existing.get("archived")))}
                    if existing
                    else dict(record)
                )
                self.database.upsert_review_record(saved)
            return dict(saved)
        with self.lock:
            records = self._read()
            by_id = {str(item.get("recordId") or ""): index for index, item in enumerate(records)}
            index = by_id.get(record_id)
            if index is None:
                records.append(dict(record))
            else:
                existing = records[index]
                if existing.get("deleted"):
                    return existing
                records[index] = {**existing, **record, "archived": bool(record.get("archived", existing.get("archived")))}
            self._write(records)
        return dict(record)

    def upsert_task(self, task: Any) -> dict[str, Any] | None:
        payload = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        record = _task_to_review_record(payload)
        return self.upsert(record) if record else None

    def archive(self, record_id: str, archived: bool) -> dict[str, Any]:
        if self.database is not None:
            updated = self.database.archive_review_record(record_id, archived)
            if updated is None:
                raise FileNotFoundError("评审记录不存在")
            return updated
        with self.lock:
            records = self._read()
            for index, record in enumerate(records):
                if str(record.get("recordId") or "") == str(record_id) and not record.get("deleted"):
                    updated = {**record, "archived": bool(archived)}
                    records[index] = updated
                    self._write(records)
                    return updated
        raise FileNotFoundError("评审记录不存在")

    def delete(self, record_id: str) -> dict[str, Any]:
        if self.database is not None:
            with self.lock:
                target = self.database.get_review_record(record_id)
                if target is None:
                    raise FileNotFoundError("评审历史记录不存在")
                if str(target.get("taskStatus") or target.get("status") or "") in {"queued", "running", "cancelling"}:
                    raise ValueError("执行中的评审记录不能删除，请先终止任务")
                references = self.database.review_artifact_references(record_id)
                candidates = sorted(_review_record_artifact_names(target) - references)
                for file_name in candidates:
                    safe_name = Path(file_name)
                    if safe_name.is_absolute() or len(safe_name.parts) != 1 or safe_name.name != file_name:
                        raise ValueError(f"非法产物文件名：{file_name}")
                deleted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                tombstone = {
                    "recordId": str(record_id),
                    "deleted": True,
                    "deletedAt": deleted_at,
                    "deletedFiles": [],
                    "pendingArtifactCleanup": candidates,
                }
                self.database.replace_review_tombstone(record_id, tombstone)

                deleted_files: list[str] = []
                pending: list[str] = []
                cleanup_errors: dict[str, str] = {}
                for file_name in candidates:
                    artifact = self.outputs_dir / file_name
                    try:
                        if not artifact.exists():
                            continue
                        if not artifact.is_file():
                            pending.append(file_name)
                            cleanup_errors[file_name] = "关联路径不是文件"
                            continue
                        artifact.unlink()
                        deleted_files.append(file_name)
                    except OSError as exc:
                        pending.append(file_name)
                        cleanup_errors[file_name] = str(exc)
                updated_tombstone = {
                    **tombstone,
                    "deletedFiles": deleted_files,
                    "pendingArtifactCleanup": pending,
                }
                try:
                    self.database.replace_review_tombstone(record_id, updated_tombstone)
                except RuntimeError:
                    pending = sorted(set(pending) | set(candidates))
                return {
                    "recordId": str(record_id),
                    "deletedFiles": deleted_files,
                    "cleanupPending": pending,
                    **({"cleanupErrors": cleanup_errors} if cleanup_errors else {}),
                }
        with self.lock:
            records = self._read()
            target = next((item for item in records if str(item.get("recordId") or "") == str(record_id) and not item.get("deleted")), None)
            if target is None:
                raise FileNotFoundError("评审历史记录不存在")
            if str(target.get("taskStatus") or target.get("status") or "") in {"queued", "running", "cancelling"}:
                raise ValueError("执行中的评审记录不能删除，请先终止任务")
            references = {
                file_name
                for item in records
                if not item.get("deleted") and str(item.get("recordId") or "") != str(record_id)
                for file_name in _review_record_artifact_names(item)
            }
            candidates = sorted(_review_record_artifact_names(target) - references)
            for file_name in candidates:
                safe_name = Path(file_name)
                if safe_name.is_absolute() or len(safe_name.parts) != 1 or safe_name.name != file_name:
                    raise ValueError(f"非法产物文件名：{file_name}")

            # The tombstone is the durable source of truth.  It must exist
            # before any artifact is removed, otherwise a failed atomic write
            # can leave history pointing at a file that no longer exists.
            records = [item for item in records if str(item.get("recordId") or "") != str(record_id)]
            records.append({
                "recordId": str(record_id),
                "deleted": True,
                "deletedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "deletedFiles": [],
                "pendingArtifactCleanup": candidates,
            })
            self._write(records)

            deleted_files: list[str] = []
            pending: list[str] = []
            cleanup_errors: dict[str, str] = {}
            for file_name in candidates:
                artifact = self.outputs_dir / file_name
                try:
                    if not artifact.exists():
                        continue
                    if not artifact.is_file():
                        pending.append(file_name)
                        cleanup_errors[file_name] = "关联路径不是文件"
                        continue
                    artifact.unlink()
                    deleted_files.append(file_name)
                except OSError as exc:
                    pending.append(file_name)
                    cleanup_errors[file_name] = str(exc)

            # Best effort only: the initial tombstone already preserves the
            # complete cleanup set if this bookkeeping write itself fails.
            updated_tombstone = {
                "recordId": str(record_id),
                "deleted": True,
                "deletedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "deletedFiles": deleted_files,
                "pendingArtifactCleanup": pending,
            }
            try:
                current = self._read()
                current = [item for item in current if str(item.get("recordId") or "") != str(record_id)]
                current.append(updated_tombstone)
                self._write(current)
            except (OSError, ReviewRecordRepositoryError):
                # Keep the original tombstone.  It intentionally lists every
                # candidate, so a future cleanup can safely retry missing or
                # failed deletions.
                pending = sorted(set(pending) | set(candidates))
            return {
                "recordId": str(record_id),
                "deletedFiles": deleted_files,
                "cleanupPending": pending,
                **({"cleanupErrors": cleanup_errors} if cleanup_errors else {}),
            }

    def migrate_from_legacy_outputs(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Report legacy orphan artifacts without making runtime queries scan them."""
        records = self.list(include_deleted=True)
        referenced = {name for record in records for name in _review_record_artifact_names(record)}
        files = list_output_artifacts(self.outputs_dir, ROOT)
        orphaned = [item["name"] for item in files if item.get("name") not in referenced]
        report = {"records": len(records), "artifacts": len(files), "orphanedArtifacts": orphaned, "dryRun": dry_run}
        if not dry_run:
            # Migration requires explicit record construction by the command so
            # that no normal history read silently mutates the index.
            report["migrated"] = 0
        return report

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = read_text(self.path)
        except (OSError, UnicodeError) as exc:
            raise ReviewRecordRepositoryError(f"无法读取评审历史文件：{self.path}") from exc
        try:
            value = json.loads(raw)
        except JSONDecodeError as exc:
            raise ReviewRecordCorruptError(
                f"评审历史文件损坏，未进行自动覆盖：{self.path}（第 {exc.lineno} 行，第 {exc.colno} 列）"
            ) from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ReviewRecordCorruptError(
                f"评审历史文件格式错误，顶层必须是对象数组，未进行自动覆盖：{self.path}"
            )
        return value

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(records[-5000:], ensure_ascii=False, indent=2))

    @staticmethod
    def _normalize(record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        if isinstance(result.get("items"), list):
            result["items"] = [normalize_review_item(item) for item in result["items"] if isinstance(item, dict)]
        return result
