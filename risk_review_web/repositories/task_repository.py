from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Callable

from risk_review_web.domain.task import (
    InvalidTaskTransition,
    Task,
    TaskResult,
    TaskStatus,
    transition_status,
)


class TaskRepository:
    """Single in-memory access point for typed task state and transitions.

    Public reads always return snapshots.  Code that needs to change a task
    must go through a repository method, so callbacks cannot retain a mutable
    ``Task`` or result list outside the repository lock.
    """

    def __init__(self, *, on_terminal: Callable[[Task], None] | None = None):
        self.lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._on_terminal = on_terminal

    @staticmethod
    def _clone(task: Task) -> Task:
        return Task.from_dict(task.to_dict())

    def _get_locked(self, task_id: str) -> Task | None:
        return self._tasks.get(str(task_id))

    def _require_locked(self, task_id: str) -> Task:
        task = self._get_locked(task_id)
        if task is None:
            raise KeyError(str(task_id))
        return task

    def create(self, task: Task) -> Task:
        with self.lock:
            if task.id in self._tasks:
                raise ValueError(f"任务已存在：{task.id}")
            stored = Task.from_dict(task.to_dict())
            now = time.time()
            if not stored.created_at:
                stored.created_at = now
            if not stored.updated_at:
                stored.updated_at = stored.created_at
            self._tasks[stored.id] = stored
            return self._clone(stored)

    def get(self, task_id: str) -> Task | None:
        with self.lock:
            task = self._get_locked(task_id)
            return self._clone(task) if task is not None else None

    def require(self, task_id: str) -> Task:
        with self.lock:
            return self._clone(self._require_locked(task_id))

    def snapshots(self) -> list[dict[str, Any]]:
        with self.lock:
            return [task.to_dict() for task in self._tasks.values()]

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        return task.to_dict() if task else None

    def update(self, task_id: str, **fields: Any) -> Task:
        """Update non-state fields; status must use a transition method."""
        with self.lock:
            task = self._require_locked(task_id)
            supplied = dict(fields)
            if task.terminal and any(key in supplied for key in {"status", "result", "outputs", "output_ids"}):
                raise InvalidTaskTransition(f"{task.id}: 终态任务不能覆盖结果或产物")
            if "status" in supplied:
                transition_status(task, TaskStatus(supplied.pop("status")))
            for key, value in supplied.items():
                self._apply_field(task, key, value)
            if "updated" not in supplied and "updated_at" not in supplied:
                task.updated_at = time.time()
            if task.terminal:
                self._notify_terminal(task)
            return self._clone(task)

    @staticmethod
    def _apply_field(task: Task, key: str, value: Any) -> None:
        if key == "result":
            task.result = TaskResult.from_value(value)
        elif key in {"outputs", "output_ids"}:
            task.output_ids = list(value or [])
        elif key == "logs":
            task.logs = deepcopy(list(value or []))
        elif key in {"debugLogs", "debug_logs"}:
            task.debug_logs = deepcopy(list(value or []))
        elif key == "context":
            task.context = deepcopy(dict(value or {}))
        elif key == "caseIds":
            task.case_ids = list(value or [])
        elif key == "traceId":
            task.trace_id = str(value or "")
        elif key == "taskType":
            task.task_type = str(value or "child")
        elif key == "parentTaskId":
            task.parent_task_id = value
        elif key == "clientId":
            task.client_id = str(value or "")
        elif key == "groupConcurrency":
            task.group_concurrency = value
        elif key == "cancel_requested":
            task.cancel_requested = bool(value)
        elif key == "experimentId":
            task.experiment_id = value
        elif key in {"debug_agent", "debugAgent"}:
            task.debug_agent = bool(value)
        elif key in {"created", "created_at"}:
            task.created_at = float(value or 0)
        elif key in {"updated", "updated_at"}:
            task.updated_at = float(value or 0)
        elif key in {"persistenceError", "persistence_error"}:
            task.persistence_error = str(value) if value else None
        elif key == "unpersisted":
            task.unpersisted = bool(value)
        elif key in {"persistenceTargetStatus", "persistence_target_status"}:
            task.persistence_target_status = str(value) if value else None
        else:
            raise AttributeError(f"Task 不支持字段：{key}")

    def set_result(self, task_id: str, result: Any, *, outputs: list[str] | None = None) -> Task:
        return self.update(task_id, result=result, **({"outputs": outputs} if outputs is not None else {}))

    def update_item(self, task_id: str, index: int, item: Mapping[str, Any]) -> Task:
        """Replace one result item while holding the repository lock."""
        with self.lock:
            task = self._require_locked(task_id)
            if task.terminal:
                raise InvalidTaskTransition(f"{task.id}: 终态任务不能更新项目")
            result = task.result.as_dict() if task.result is not None else {}
            items = result.get("items")
            if not isinstance(items, list) or index < 0 or index >= len(items):
                raise IndexError(f"任务项目索引越界：{task.id}[{index}]")
            items[index] = deepcopy(dict(item))
            task.result = TaskResult.from_value(result)
            task.updated_at = time.time()
            return self._clone(task)

    def start(self, task_id: str) -> Task:
        return self.update(task_id, status=TaskStatus.RUNNING)

    def request_cancel(self, task_id: str, result: Any = None) -> Task:
        with self.lock:
            task = self._require_locked(task_id)
            task.cancel_requested = True
            if task.status is TaskStatus.QUEUED:
                transition_status(task, TaskStatus.CANCELLED)
            elif task.status is TaskStatus.RUNNING:
                transition_status(task, TaskStatus.CANCELLING)
            if result is not None and task.result is None:
                task.result = TaskResult.from_value(result)
            task.updated_at = time.time()
            if task.terminal:
                self._notify_terminal(task)
            return self._clone(task)

    def complete(self, task_id: str, result: Any = None, *, outputs: list[str] | None = None) -> Task:
        with self.lock:
            task = self._require_locked(task_id)
            if task.terminal:
                if task.status is TaskStatus.DONE:
                    return self._clone(task)
                raise InvalidTaskTransition(f"{task.id}: {task.status.value} 不能完成")
            if task.status is TaskStatus.QUEUED:
                transition_status(task, TaskStatus.RUNNING)
            transition_status(task, TaskStatus.DONE)
            task.result = TaskResult.from_value(result)
            if outputs is not None:
                task.output_ids = list(outputs)
            task.updated_at = time.time()
            self._notify_terminal(task)
            return self._clone(task)

    def fail(self, task_id: str, result: Any = None, *, outputs: list[str] | None = None) -> Task:
        with self.lock:
            task = self._require_locked(task_id)
            if task.terminal:
                if task.status is TaskStatus.ERROR:
                    return self._clone(task)
                raise InvalidTaskTransition(f"{task.id}: {task.status.value} 不能标记失败")
            if task.status is TaskStatus.QUEUED:
                transition_status(task, TaskStatus.RUNNING)
            if task.status is TaskStatus.CANCELLING:
                transition_status(task, TaskStatus.ERROR)
            elif task.status is TaskStatus.RUNNING:
                transition_status(task, TaskStatus.ERROR)
            task.result = TaskResult.from_value(result)
            if outputs is not None:
                task.output_ids = list(outputs)
            task.updated_at = time.time()
            self._notify_terminal(task)
            return self._clone(task)

    def fail_unpersisted(
        self,
        task_id: str,
        result: Any,
        *,
        target_status: str,
        persistence_error: str,
        outputs: list[str] | None = None,
    ) -> Task:
        """Record a terminal execution failure without claiming history exists."""
        with self.lock:
            task = self._require_locked(task_id)
            if not task.terminal:
                if task.status is TaskStatus.QUEUED:
                    transition_status(task, TaskStatus.RUNNING)
                if task.status is TaskStatus.CANCELLING:
                    transition_status(task, TaskStatus.ERROR)
                elif task.status is TaskStatus.RUNNING:
                    transition_status(task, TaskStatus.ERROR)
            elif task.status is not TaskStatus.ERROR:
                # A queued cancellation may already have reached CANCELLED
                # before its history write is attempted.  Reconcile any such
                # terminal state to the explicit unpersisted ERROR marker; the
                # original target is restored only after a successful retry.
                task.status = TaskStatus.ERROR
            payload = TaskResult.from_value(result).as_dict()
            payload.update({
                "persistenceError": persistence_error,
                "unpersisted": True,
                "persistenceTargetStatus": str(target_status),
            })
            task.result = TaskResult.from_value(payload)
            task.persistence_error = persistence_error
            task.unpersisted = True
            task.persistence_target_status = str(target_status)
            if outputs is not None:
                task.output_ids = list(outputs)
            task.updated_at = time.time()
            self._notify_terminal(task)
            return self._clone(task)

    def mark_persisted(self, task_id: str) -> Task:
        """Complete an explicitly retried history write and clear its marker."""
        with self.lock:
            task = self._require_locked(task_id)
            if not task.unpersisted:
                return self._clone(task)
            target = TaskStatus(task.persistence_target_status or TaskStatus.ERROR)
            if task.status is not TaskStatus.ERROR:
                raise InvalidTaskTransition(f"{task.id}: 只能从 error 重试未持久化任务")
            payload = task.result.as_dict() if task.result is not None else {}
            for key in ("persistenceError", "unpersisted", "persistenceTargetStatus"):
                payload.pop(key, None)
            # This is a controlled reconciliation after the history write has
            # succeeded; it is intentionally not a general state transition.
            task.status = target
            task.result = TaskResult.from_value(payload)
            task.persistence_error = None
            task.unpersisted = False
            task.persistence_target_status = None
            task.updated_at = time.time()
            self._notify_terminal(task)
            return self._clone(task)

    def cancel(self, task_id: str, result: Any = None) -> Task:
        with self.lock:
            task = self._require_locked(task_id)
            if task.terminal:
                if task.status is TaskStatus.CANCELLED:
                    if result is not None and task.result is None:
                        task.result = TaskResult.from_value(result)
                        task.updated_at = time.time()
                        self._notify_terminal(task)
                    return self._clone(task)
                raise InvalidTaskTransition(f"{task.id}: {task.status.value} 不能取消")
            if task.status is TaskStatus.QUEUED:
                transition_status(task, TaskStatus.CANCELLED)
            elif task.status is TaskStatus.RUNNING:
                transition_status(task, TaskStatus.CANCELLED)
            elif task.status is TaskStatus.CANCELLING:
                transition_status(task, TaskStatus.CANCELLED)
            task.cancel_requested = True
            if result is not None:
                task.result = TaskResult.from_value(result)
            task.updated_at = time.time()
            self._notify_terminal(task)
            return self._clone(task)

    def append_log(self, task_id: str, entry: dict[str, Any], *, debug: bool = False) -> Task:
        with self.lock:
            task = self._require_locked(task_id)
            (task.debug_logs if debug else task.logs).append(deepcopy(dict(entry)))
            task.updated_at = time.time()
            return self._clone(task)

    def remove(self, task_id: str) -> Task | None:
        with self.lock:
            task = self._tasks.pop(str(task_id), None)
            return self._clone(task) if task is not None else None

    def gc(self, *, ttl_seconds: float, max_count: int, force: bool = False) -> int:
        now = time.time()
        removed = 0
        with self.lock:
            if force or len(self._tasks) > max_count:
                overflow = max(0, len(self._tasks) - max_count + 1)
                for task in sorted(self._tasks.values(), key=lambda item: item.created_at):
                    if overflow <= 0:
                        break
                    if task.terminal and not task.unpersisted:
                        self._tasks.pop(task.id, None)
                        removed += 1
                        overflow -= 1
            for task_id, task in list(self._tasks.items()):
                if task.terminal and not task.unpersisted and now - (task.updated_at or task.created_at) > ttl_seconds:
                    self._tasks.pop(task_id, None)
                    removed += 1
        return removed

    def _notify_terminal(self, task: Task) -> None:
        if self._on_terminal and task.terminal:
            self._on_terminal(self._clone(task))


class TaskRegistryView(Mapping[str, Mapping[str, Any]]):
    """Read-only snapshot view for legacy read paths.

    It deliberately has no ``__setitem__``, ``__delitem__`` or ``pop``.  New
    code must use ``TaskRepository`` mutation methods, and old readers receive
    an isolated mapping rather than a writable task proxy.
    """

    def __init__(self, repository: TaskRepository):
        self.repository = repository

    def __getitem__(self, key: str) -> Mapping[str, Any]:
        snapshot = self.repository.snapshot(key)
        if snapshot is None:
            raise KeyError(key)
        return MappingProxyType(snapshot)

    def __iter__(self) -> Iterator[str]:
        return iter([item["id"] for item in self.repository.snapshots()])

    def __len__(self) -> int:
        return len(self.repository.snapshots())

    def values(self):
        return (self[key] for key in self)

    def items(self):
        return ((key, self[key]) for key in self)
