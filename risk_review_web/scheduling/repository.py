from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from risk_review_web.paths import SCHEDULED_RUNS_PATH, SCHEDULED_TASKS_PATH
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
    """Thread-safe JSON persistence for scheduled tasks and run history."""

    def __init__(
        self,
        tasks_path: Path = SCHEDULED_TASKS_PATH,
        runs_path: Path = SCHEDULED_RUNS_PATH,
    ) -> None:
        self.tasks_path = tasks_path
        self.runs_path = runs_path
        self.lock = threading.RLock()
        self._ensure_files()

    def _ensure_files(self) -> None:
        for path in (self.tasks_path, self.runs_path):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(path, [])

    def list_tasks(self) -> list[JsonDict]:
        with self.lock:
            tasks = _read_json_list(self.tasks_path)
        tasks.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return tasks

    def get_task(self, task_id: str) -> JsonDict | None:
        return next((item for item in self.list_tasks() if item.get("taskId") == task_id), None)

    def save_tasks(self, tasks: list[JsonDict]) -> None:
        with self.lock:
            _atomic_write_json(self.tasks_path, tasks)

    def list_runs(self, task_id: str | None = None) -> list[JsonDict]:
        with self.lock:
            runs = _read_json_list(self.runs_path)
        if task_id:
            runs = [item for item in runs if item.get("taskId") == task_id]
        runs.sort(key=lambda item: item.get("startedAt") or item.get("createdAt") or "", reverse=True)
        return runs

    def get_run(self, run_id: str) -> JsonDict | None:
        return next((item for item in self.list_runs() if item.get("runId") == run_id), None)

    def save_runs(self, runs: list[JsonDict]) -> None:
        with self.lock:
            _atomic_write_json(self.runs_path, runs)

