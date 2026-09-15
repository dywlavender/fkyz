from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from risk_review_web.paths import SCHEDULED_RUNS_PATH, SCHEDULED_TASKS_PATH
from risk_review_web.repositories.runtime_database import RuntimeDatabase, get_runtime_database
from risk_review_web.utils import atomic_write_text, read_text

JsonDict = dict[str, Any]


def _atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _read_json_list(path: Path) -> list[JsonDict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, [])
        return []
    try:
        data = json.loads(read_text(path))
    except Exception as exc:
        from risk_review_web.scheduling.models import ScheduledTaskError

        raise ScheduledTaskError(f"定时任务数据文件无法解析：{path.name}") from exc
    if not isinstance(data, list):
        from risk_review_web.scheduling.models import ScheduledTaskError

        raise ScheduledTaskError(f"定时任务数据文件格式错误：{path.name} 顶层必须是数组。")
    return data


class ScheduledTaskStore:
    """Thread-safe persistence for scheduled tasks and run history.

    The default runtime store is SQLite-backed.  Explicit paths remain on the
    JSON adapter so isolated unit tests and one-off migration tools keep their
    old deterministic behavior.
    """

    def __init__(
        self,
        tasks_path: Path = SCHEDULED_TASKS_PATH,
        runs_path: Path = SCHEDULED_RUNS_PATH,
    ) -> None:
        self.tasks_path = tasks_path
        self.runs_path = runs_path
        self.lock = threading.RLock()
        self.database: RuntimeDatabase | None = None
        if (
            Path(tasks_path).resolve() == SCHEDULED_TASKS_PATH.resolve()
            and Path(runs_path).resolve() == SCHEDULED_RUNS_PATH.resolve()
        ):
            self.database = get_runtime_database()
        self._ensure_files()

    def _ensure_files(self) -> None:
        if self.database is not None:
            return
        for path in (self.tasks_path, self.runs_path):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(path, [])

    def list_tasks(self, *, summaries: bool = False) -> list[JsonDict]:
        if self.database is not None:
            return self.database.list_scheduled_tasks(summaries=summaries)
        with self.lock:
            tasks = _read_json_list(self.tasks_path)
        tasks.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return tasks

    def get_task(self, task_id: str) -> JsonDict | None:
        if self.database is not None:
            return self.database.get_scheduled_task(task_id)
        return next((item for item in self.list_tasks() if item.get("taskId") == task_id), None)

    def save_tasks(self, tasks: list[JsonDict]) -> None:
        if self.database is not None:
            with self.lock:
                for task in tasks:
                    self.database.upsert_scheduled_task(task)
            return
        with self.lock:
            _atomic_write_json(self.tasks_path, tasks)

    def list_runs(
        self,
        task_id: str | None = None,
        *,
        page: int | None = None,
        page_size: int = 20,
        summaries: bool = False,
    ) -> list[JsonDict] | dict[str, Any]:
        if self.database is not None:
            return self.database.list_scheduled_runs(
                task_id,
                page=page,
                page_size=page_size,
                summaries=summaries,
            )
        with self.lock:
            runs = _read_json_list(self.runs_path)
        if task_id:
            runs = [item for item in runs if item.get("taskId") == task_id]
        runs.sort(key=lambda item: item.get("startedAt") or item.get("createdAt") or "", reverse=True)
        if page is None:
            return runs
        normalized_page = max(1, int(page))
        normalized_size = max(1, min(100, int(page_size)))
        start = (normalized_page - 1) * normalized_size
        return {
            "items": runs[start:start + normalized_size],
            "page": normalized_page,
            "pageSize": normalized_size,
            "total": len(runs),
        }

    def get_run(self, run_id: str) -> JsonDict | None:
        if self.database is not None:
            return self.database.get_scheduled_run(run_id)
        return next((item for item in self.list_runs() if item.get("runId") == run_id), None)

    def save_runs(self, runs: list[JsonDict]) -> None:
        if self.database is not None:
            with self.lock:
                for run in runs:
                    self.database.upsert_scheduled_run(run)
            return
        with self.lock:
            _atomic_write_json(self.runs_path, runs)

    def upsert_task(self, task: JsonDict) -> None:
        if self.database is not None:
            self.database.upsert_scheduled_task(task)
            return
        with self.lock:
            tasks = _read_json_list(self.tasks_path)
            for index, current in enumerate(tasks):
                if current.get("taskId") == task.get("taskId"):
                    tasks[index] = dict(task)
                    _atomic_write_json(self.tasks_path, tasks)
                    return
            tasks.append(dict(task))
            _atomic_write_json(self.tasks_path, tasks)

    def update_task(self, task_id: str, patch: JsonDict) -> JsonDict | None:
        current = self.get_task(task_id)
        if not current:
            return None
        current = {**current, **patch, "taskId": task_id}
        self.upsert_task(current)
        return current

    def delete_task(self, task_id: str) -> bool:
        if self.database is not None:
            return self.database.delete_scheduled_task(task_id)
        with self.lock:
            tasks = _read_json_list(self.tasks_path)
            filtered = [task for task in tasks if task.get("taskId") != task_id]
            if len(filtered) == len(tasks):
                return False
            _atomic_write_json(self.tasks_path, filtered)
            runs = _read_json_list(self.runs_path)
            _atomic_write_json(self.runs_path, [run for run in runs if run.get("taskId") != task_id])
            return True

    def upsert_run(self, run: JsonDict) -> None:
        if self.database is not None:
            self.database.upsert_scheduled_run(run)
            return
        with self.lock:
            runs = _read_json_list(self.runs_path)
            for index, current in enumerate(runs):
                if current.get("runId") == run.get("runId"):
                    runs[index] = dict(run)
                    _atomic_write_json(self.runs_path, runs)
                    return
            runs.append(dict(run))
            _atomic_write_json(self.runs_path, runs)

    def update_run(self, run_id: str, patch: JsonDict) -> JsonDict | None:
        if self.database is not None:
            return self.database.update_scheduled_run(run_id, patch)
        with self.lock:
            runs = _read_json_list(self.runs_path)
            for index, current in enumerate(runs):
                if current.get("runId") == run_id:
                    runs[index] = {**current, **patch}
                    _atomic_write_json(self.runs_path, runs)
                    return runs[index]
        return None

    def latest_runs_by_task(self) -> dict[str, JsonDict]:
        if self.database is not None:
            return self.database.latest_scheduled_runs_by_task()
        latest: dict[str, JsonDict] = {}
        for run in self.list_runs():
            task_id = str(run.get("taskId") or "")
            if task_id and task_id not in latest:
                latest[task_id] = run
        return latest

    def recover_runs(self, now_text: str, error: str, metadata: dict[str, Any]) -> list[JsonDict]:
        if self.database is not None:
            return self.database.recover_scheduled_runs(now_text, error, metadata)
        recovered: list[JsonDict] = []
        with self.lock:
            runs = _read_json_list(self.runs_path)
            for run in runs:
                if run.get("status") not in {"pending", "running"}:
                    continue
                run.update({"status": "failed", "finishedAt": now_text, "error": error, **metadata})
                recovered.append(dict(run))
            if recovered:
                _atomic_write_json(self.runs_path, runs)
        return recovered
