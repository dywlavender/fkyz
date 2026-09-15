from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from risk_review_web.domain.task import Task
from risk_review_web.repositories.review_record_repository import ReviewRecordRepository
from risk_review_web.repositories.task_repository import TaskRepository
from risk_review_web.services.review_history import normalize_review_item


@dataclass(frozen=True, slots=True)
class ReviewHistoryDependencies:
    repository: ReviewRecordRepository
    task_repository: TaskRepository
    task_registry: Any
    task_lock: Any
    task_pool: Any
    gc_tasks: Callable[[], int]


class ReviewHistoryService:
    """Read and delete the single persisted review-history index."""

    def __init__(self, dependencies: ReviewHistoryDependencies):
        self.dependencies = dependencies

    @property
    def repository(self) -> ReviewRecordRepository:
        return self.dependencies.repository

    def records(self, include_archived: bool = False, *, summaries: bool = False) -> list[dict]:
        records = self.repository.list_summaries(include_archived=include_archived) if summaries else self.repository.list(include_archived=include_archived)
        return sorted(records, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)

    @staticmethod
    def normalize_task_record_view(record: dict) -> dict:
        source = str(record.get("source") or "single")
        task_type = str(record.get("taskType") or {
            "single": "phase2",
            "batch": "batch",
            "scheduled": "scheduled",
            "validation": "regression",
            "experiment": "review-mode-experiment",
        }.get(source, source))
        task_id = str(record.get("taskId") or record.get("id") or record.get("recordId") or "")
        status = str(record.get("status") or record.get("taskStatus") or "done")
        progress = record.get("progress") or record.get("summary") or {
            "total": 1,
            "completed": int(status == "done"),
            "failed": int(status == "error"),
        }
        context = dict(record.get("context") or {})
        context.setdefault("taskName", record.get("title") or record.get("factorName") or "评审任务")
        context.setdefault("factorName", record.get("factorName") or record.get("title") or "")
        items = record.get("items")
        normalized_items = (
            [normalize_review_item(item) for item in items if isinstance(item, dict)]
            if isinstance(items, list)
            else items
        )
        return {
            **record,
            "id": task_id,
            "taskId": task_id,
            "taskType": task_type,
            "parentTaskId": record.get("parentTaskId"),
            "status": status,
            "taskStatus": status,
            "context": context,
            "progress": progress,
            "summary": progress,
            "items": normalized_items,
        }

    @staticmethod
    def _timestamp_from_iso(value: object) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def record_to_task(self, record: dict, *, summary: bool = False) -> dict:
        normalized = self.normalize_task_record_view(record)
        standard_artifacts = normalized.get("standardArtifacts") or {}
        review_artifacts = normalized.get("reviewArtifacts") or {}
        context = normalized.get("context") or {}
        if summary:
            context = {
                key: context[key]
                for key in ("taskName", "factorName", "standardFileId")
                if key in context
            }
        result = {
            "items": normalized.get("items") or [],
            **({"standardFileId": standard_artifacts.get("md")} if standard_artifacts.get("md") else {}),
            **({"standardMetadataFileId": standard_artifacts.get("json")} if standard_artifacts.get("json") else {}),
            **({"reviewFileId": review_artifacts.get("md")} if review_artifacts.get("md") else {}),
        }
        return {
            "id": normalized["taskId"],
            "taskId": normalized["taskId"],
            "recordId": normalized.get("recordId"),
            "taskType": normalized.get("taskType"),
            "parentTaskId": normalized.get("parentTaskId"),
            "clientId": normalized.get("clientId") or "",
            "status": normalized.get("status"),
            "context": context,
            "progress": normalized.get("progress") or {},
            "result": result,
            "created": self._timestamp_from_iso(normalized.get("createdAt")),
            "updated": self._timestamp_from_iso(normalized.get("updatedAt")),
            "createdAt": normalized.get("createdAt"),
            "updatedAt": normalized.get("updatedAt"),
            "source": normalized.get("source"),
            "conclusion": normalized.get("conclusion"),
            "riskCount": normalized.get("riskCount") or 0,
            "persisted": True,
        }

    def find_persisted_task(self, task_id: str) -> dict | None:
        record = self.repository.get_by_task_id(task_id, include_archived=True)
        if record:
            return self.record_to_task(record)
        return None

    def record_detail(self, record_id: str) -> dict | None:
        """Load one history record with its detail items on demand."""
        return self.repository.get(record_id, include_deleted=False)

    def query(self, query: dict[str, list[str]]) -> dict:
        archived_only = (query.get("archived") or ["false"])[0].lower() == "true"
        keyword = (query.get("keyword") or [""])[0].strip().lower()
        source = (query.get("source") or [""])[0].strip()
        task_status = (query.get("taskStatus") or [""])[0].strip()
        conclusion = (query.get("conclusion") or [""])[0].strip()
        date_from = (query.get("dateFrom") or [""])[0].strip()
        date_to = (query.get("dateTo") or [""])[0].strip()
        try:
            page = max(1, int((query.get("page") or ["1"])[0]))
            page_size_limit = 5000 if (query.get("export") or ["false"])[0].lower() == "true" else 100
            page_size = max(1, min(page_size_limit, int((query.get("pageSize") or ["20"])[0])))
        except ValueError:
            page, page_size = 1, 20
        records, total = self.repository.query_summaries({
            "archived": archived_only,
            "keyword": keyword,
            "source": source,
            "task_status": task_status,
            "conclusion": conclusion,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "page_size": page_size,
            "page_limit": page_size_limit,
        })
        return {"items": records, "page": page, "pageSize": page_size, "total": total}

    def archive(self, record_id: str, archived: bool) -> dict:
        return self.repository.archive(record_id, archived)

    def delete_record(self, record_id: str) -> dict:
        return self.repository.delete(record_id)

    @staticmethod
    def _is_terminal(status: str | None) -> bool:
        return status in {"done", "error", "cancelled"}

    def decorate_task(self, task: dict | Task) -> dict:
        snapshot = task.to_dict() if isinstance(task, Task) else dict(task)
        task_id = str(snapshot.get("id") or "")
        snapshot.update(self.dependencies.task_pool.group_info(task_id))
        job_info = self.dependencies.task_pool.job_info(task_id)
        if job_info.get("poolStatus") is not None:
            snapshot.update(job_info)
        result = snapshot.get("result") or {}
        items = result.get("items") if isinstance(result, dict) else None
        if isinstance(items, list):
            terminal = {"done", "pass", "fail", "error", "skip", "cancelled"}
            snapshot["progress"] = {
                "total": len(items),
                "completed": sum(1 for item in items if str(item.get("status") or "") in terminal),
                "failed": sum(1 for item in items if str(item.get("status") or "") in {"error", "skip"}),
            }
        else:
            status = str(snapshot.get("status") or "")
            if not isinstance(snapshot.get("progress"), dict):
                snapshot["progress"] = {
                    "total": 1,
                    "completed": int(status in {"done", "error", "cancelled"}),
                    "failed": int(status == "error"),
                }
        snapshot["taskId"] = task_id
        snapshot["persisted"] = False
        return snapshot

    def history(self, *, include_history: bool = False, summary: bool = False) -> list[dict]:
        self.dependencies.gc_tasks()
        with self.dependencies.task_lock:
            if summary and hasattr(self.dependencies.task_registry, "summary_values"):
                tasks = [self.decorate_task(task) for task in self.dependencies.task_registry.summary_values()]
            else:
                tasks = [self.decorate_task(task) for task in self.dependencies.task_registry.values()]
        if include_history:
            known_ids = {str(task.get("id") or "") for task in tasks}
            tasks.extend(
                self.record_to_task(record, summary=summary)
                for record in self.repository.list_summaries(include_archived=False)
                if str(record.get("taskId") or "") not in known_ids
            )
        return sorted(tasks, key=lambda item: item.get("created", 0), reverse=True)[:50]

    def delete_task(self, task_id: str) -> dict:
        with self.dependencies.task_lock:
            task = self.dependencies.task_registry.get(task_id)
            if task and not self._is_terminal(str(task.get("status") or "")):
                raise ValueError("执行中的任务不能删除，请先终止任务")
        record = self.repository.get_by_task_id(task_id, include_archived=True)
        if not record:
            raise FileNotFoundError("任务记录不存在")
        deleted = self.delete_record(str(record.get("recordId") or ""))
        with self.dependencies.task_lock:
            self.dependencies.task_repository.remove(task_id)
        return {**deleted, "taskId": task_id}

    def task_view(self, task_id: str) -> dict | None:
        task = self.dependencies.task_repository.get(task_id)
        return self.decorate_task(task) if task is not None else self.find_persisted_task(task_id)
