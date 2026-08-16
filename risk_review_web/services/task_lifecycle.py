from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from risk_review_web.domain.agent_result import AgentResult, AgentStatus
from risk_review_web.error_classification import attach_error_metadata
from risk_review_web.domain.task import InvalidTaskTransition, TaskStatus
from risk_review_web.repositories.task_repository import TaskRepository


def task_output_token(task_id: str) -> str:
    """Return a stable artifact token for the complete task id."""
    value = str(task_id or "task")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    readable_prefix = re.sub(r"[^a-zA-Z0-9]", "", value)[:8] or "task"
    return f"{readable_prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class TaskLifecycleDependencies:
    task_repository: TaskRepository
    task_registry: Any
    task_lock: Any
    task_ttl_seconds: int
    task_max_count: int
    task_pool: Any
    review_record_sink: Callable[[dict], Any]
    list_outputs: Callable[[], list[str]]
    runtime_log: Callable[..., Any]
    cancel_validation_runs_for_task: Callable[[str], Any]
    upsert_review_mode_experiment: Callable[[dict], dict]
    iso_time: Callable[[], str]


class TaskLifecycleService:
    """Own task logs, cancellation, terminal persistence, and collection."""

    def __init__(self, dependencies: TaskLifecycleDependencies):
        self.dependencies = dependencies

    def gc(self, force: bool = False) -> int:
        now = time.time()
        removed = 0
        with self.dependencies.task_lock:
            registry = self.dependencies.task_registry
            if force or len(registry) > self.dependencies.task_max_count:
                overflow = max(0, len(registry) - self.dependencies.task_max_count + 1)
                ordered = sorted(registry.items(), key=lambda item: item[1].get("created", 0))
                for task_id, task in ordered:
                    if overflow <= 0:
                        break
                    if self.is_terminal(str(task.get("status") or "")) and not task.get("unpersisted"):
                        self.dependencies.task_repository.remove(task_id)
                        removed += 1
                        overflow -= 1
            for task_id, task in list(registry.items()):
                if not self.is_terminal(str(task.get("status") or "")) or task.get("unpersisted"):
                    continue
                updated = task.get("updated") or task.get("created") or 0
                if now - updated > self.dependencies.task_ttl_seconds:
                    self.dependencies.task_repository.remove(task_id)
                    removed += 1
        return removed

    @staticmethod
    def is_terminal(status: str | None) -> bool:
        return status in {"done", "error", "cancelled"}

    def log(self, task_id: str, message: str) -> None:
        task = self.dependencies.task_repository.get(task_id)
        if not task:
            return
        self.dependencies.task_repository.append_log(task_id, {"time": time.time(), "message": message})
        self.dependencies.runtime_log(
            task.task_type or "task",
            message,
            event="task_execution",
            trace_id=task.trace_id,
            task_id=task_id,
        )

    def debug_log(
        self,
        task_id: str,
        stream: str,
        message: str,
        *,
        memory: bool = True,
        raw_message: bool = False,
    ) -> None:
        task = self.dependencies.task_repository.get(task_id)
        if not task:
            return
        if memory:
            self.dependencies.task_repository.append_log(task_id, {
                "time": time.time(),
                "stream": stream,
                "message": str(message or ""),
            }, debug=True)
        stream_name = str(stream or "model_io").lower()
        log_kwargs = {
            "level": "ERROR" if stream_name == "error" and str(message or "").strip() else "INFO",
            "event": stream_name if stream_name.startswith("agent_") else f"model_{stream_name}",
            "trace_id": task.trace_id,
            "task_id": task_id,
        }
        if raw_message:
            log_kwargs["raw_message"] = True
        self.dependencies.runtime_log(task.task_type or "task", str(message or ""), **log_kwargs)

    def cancel_requested(self, task_id: str) -> bool:
        with self.dependencies.task_lock:
            current_id = task_id
            visited = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                task = self.dependencies.task_registry.get(current_id) or {}
                if task.get("cancel_requested"):
                    return True
                current_id = str(task.get("parentTaskId") or "")
        return False

    @staticmethod
    def agent_result_payload(result: AgentResult, *, content: str | None = None, **fields) -> dict:
        payload = {
            "agentStatus": result.status.value,
            "content": result.content if content is None else content,
            "error": result.error,
        }
        payload.update(fields)
        return payload

    def finish(self, task_id: str, status: str, result: dict | None = None) -> None:
        normalized_status = str(status or "error")
        payload = dict(result or {})
        if normalized_status == "cancelled":
            payload.setdefault("agentStatus", AgentStatus.CANCELLED.value)
            payload.setdefault("content", "")
            payload.setdefault("error", "任务已取消")
        elif normalized_status != "done":
            normalized_status = "error"
            payload.setdefault("agentStatus", AgentStatus.FAILED.value)
            payload.setdefault("content", "")
            attach_error_metadata(payload, "任务执行失败")
        failure_task = None
        persistence_error = ""
        # Cancellation and terminal persistence form one serialized commit.
        # Holding the task lock across the history write prevents a concurrent
        # cancel request from changing RUNNING to CANCELLING after a stale DONE
        # record has already been persisted.
        with self.dependencies.task_lock:
            current = self.dependencies.task_repository.get(task_id)
            if current is None:
                raise KeyError(task_id)
            if normalized_status == "done" and (
                current.cancel_requested
                or current.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}
            ):
                normalized_status = "cancelled"
                payload = current.result.as_dict() if current.result is not None else {}
                payload["agentStatus"] = AgentStatus.CANCELLED.value
                payload.setdefault("content", "")
                payload["error"] = payload.get("error") or "任务已取消"
            if current.terminal and current.status.value != normalized_status:
                raise InvalidTaskTransition(
                    f"{task_id}: 已是 {current.status.value}，不能改为 {normalized_status}"
                )

            outputs = self.dependencies.list_outputs()
            candidate = current.to_dict()
            candidate.update({
                "status": normalized_status,
                "result": payload,
                "outputs": outputs,
                "persistenceError": None,
                "unpersisted": False,
                "persistenceTargetStatus": None,
            })
            try:
                self.dependencies.review_record_sink(candidate)
            except Exception as exc:
                persistence_error = f"{type(exc).__name__}: {exc}"
                failure_payload = dict(payload)
                failure_payload["persistenceError"] = persistence_error
                failure_payload["unpersisted"] = True
                failure_task = self.dependencies.task_repository.fail_unpersisted(
                    task_id,
                    failure_payload,
                    target_status=normalized_status,
                    persistence_error=persistence_error,
                    outputs=outputs,
                )
            else:
                if normalized_status == "done":
                    self.dependencies.task_repository.complete(task_id, payload, outputs=outputs)
                elif normalized_status == "cancelled":
                    self.dependencies.task_repository.cancel(task_id, payload)
                else:
                    self.dependencies.task_repository.fail(task_id, payload, outputs=outputs)

        if failure_task is not None:
            self.dependencies.runtime_log(
                failure_task.task_type or "task",
                f"终态历史记录写入失败，任务保留为未持久化错误：{persistence_error}",
                level="ERROR",
                event="review_history_persist_failed",
                trace_id=failure_task.trace_id,
                task_id=failure_task.id,
            )

    def retry_persistence(self, task_id: str) -> dict:
        task = self.dependencies.task_repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if not task.unpersisted:
            return task.to_dict()
        target_status = str(task.persistence_target_status or "error")
        candidate = task.to_dict()
        payload = dict(task.result or {})
        for key in ("persistenceError", "unpersisted", "persistenceTargetStatus"):
            payload.pop(key, None)
        candidate.update({"status": target_status, "result": payload, "unpersisted": False})
        self.dependencies.review_record_sink(candidate)
        return self.dependencies.task_repository.mark_persisted(task_id).to_dict()

    def mark_cancelled(self, task_id: str, message: str) -> None:
        task = self.dependencies.task_repository.get(task_id)
        if task:
            self.finish(
                task_id,
                "cancelled",
                task.result or self.agent_result_payload(AgentResult.cancelled(message), error=message),
            )

    def request_cancel(self, task_id: str) -> bool:
        with self.dependencies.task_lock:
            task = self.dependencies.task_registry.get(task_id)
            child_ids = {tid for tid in self.dependencies.task_registry if tid == task_id or tid.startswith(f"{task_id}-")}
            changed = True
            while changed:
                changed = False
                for tid, candidate in self.dependencies.task_registry.items():
                    if tid in child_ids:
                        continue
                    if candidate.get("parentTaskId") in child_ids:
                        child_ids.add(tid)
                        changed = True
            if not task and not child_ids:
                return False
            for child_id in child_ids:
                candidate = self.dependencies.task_repository.get(child_id)
                if candidate is None or candidate.terminal:
                    continue
                self.dependencies.task_repository.request_cancel(
                    child_id,
                    self.agent_result_payload(AgentResult.cancelled("任务已请求取消。"), error="任务已请求取消。"),
                )
        queued_single_cancelled = self.dependencies.task_pool.cancel(task_id)
        cancelled_pool_jobs = self.dependencies.task_pool.cancel_group(task_id)
        pool_group = self.dependencies.task_pool.group_info(task_id)
        with self.dependencies.task_lock:
            task = self.dependencies.task_registry.get(task_id)
            if task and queued_single_cancelled:
                self.finish(
                    task_id,
                    "cancelled",
                    task.get("result") or self.agent_result_payload(
                        AgentResult.cancelled("任务在全局线程池排队期间被取消。"),
                        error="任务在全局线程池排队期间被取消。",
                    ),
                )
        if task and not queued_single_cancelled and int(pool_group.get("poolActive") or 0) == 0:
            with self.dependencies.task_lock:
                current = self.dependencies.task_registry.get(task_id)
                if current and current.get("status") == "cancelling":
                    self.finish(
                        task_id,
                        "cancelled",
                        current.get("result") or self.agent_result_payload(
                            AgentResult.cancelled("任务已终止；当前没有仍在执行的线程池作业。"),
                            error="任务已终止；当前没有仍在执行的线程池作业。",
                        ),
                    )
        self.dependencies.cancel_validation_runs_for_task(task_id)
        experiment_id = str((task or {}).get("experimentId") or "")
        if experiment_id:
            self.dependencies.upsert_review_mode_experiment({
                "experimentId": experiment_id,
                "status": "cancelled" if queued_single_cancelled else "cancelling",
                **({
                    "finishedAt": self.dependencies.iso_time(),
                    "error": "实验在全局线程池排队期间被取消",
                } if queued_single_cancelled else {}),
            })
        if task:
            self.log(
                task_id,
                f"已请求停止任务：取消 {len(cancelled_pool_jobs) + int(queued_single_cancelled)} 个排队作业；运行中的 Agent 请求返回后停止后续步骤",
            )
        return True
