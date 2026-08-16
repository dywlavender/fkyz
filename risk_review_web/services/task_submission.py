from __future__ import annotations

import hashlib
import time
import uuid
from concurrent.futures import CancelledError
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from risk_review_web.domain.agent_result import AgentResult
from risk_review_web.domain.task import Task, TaskStatus
from risk_review_web.repositories.task_repository import TaskRepository


@dataclass(frozen=True, slots=True)
class TaskSubmissionDependencies:
    """Application services needed to create and dispatch top-level tasks."""

    task_repository: TaskRepository
    tasks: Mapping[str, Mapping[str, Any]]
    task_lock: Any
    task_pool: Any
    review_record_sink: Callable[[Any], Any]
    runtime_log: Callable[..., Any]
    attach_error_metadata: Callable[[dict, str], Any]
    new_trace_id: Callable[[], str]
    agent_result_payload: Callable[..., dict]
    task_log: Callable[[str, str], Any]
    finish_task: Callable[[str, str, dict | None], Any]
    task_view: Callable[[str], dict | None]
    decorate_task: Callable[[dict | Task], dict]
    gc_tasks: Callable[[], int]
    task_cancel_requested: Callable[[str], bool]
    run_agent_phase1: Callable[[dict, str], Any]
    run_agent_phase2: Callable[[dict, str], Any]
    run_agent_requirement_review: Callable[[dict, str], Any]
    start_batch_review: Callable[..., Any]
    batch_workflow_dependencies: Callable[[], Any]
    prepare_validation_runs: Callable[..., dict[str, str]]
    regression_workflow_dependencies: Callable[[], Any]
    run_regression_validation: Callable[..., Any]
    experiment_workflow_dependencies: Callable[[], Any]
    run_review_mode_experiment: Callable[..., Any]
    summarize_review_mode_experiment: Callable[[list[dict], int], dict]
    run_review_pair: Callable[..., dict]
    task_output_token: Callable[[str], str]
    get_runtime_settings: Callable[[], dict]
    iso_time: Callable[[], str]


class TaskSubmissionService:
    """Own top-level task creation and submission adapters.

    The service deliberately receives workflow factories instead of importing
    the application context.  Each submitted job therefore carries an
    explicit dependency bundle and still runs inside the one global pool.
    """

    def __init__(self, dependencies: TaskSubmissionDependencies):
        self.dependencies = dependencies

    def create_task_record(
        self,
        task_id: str,
        debug_agent: bool = False,
        *,
        task_type: str = "child",
        parent_task_id: str | None = None,
        client_id: str = "",
        group_concurrency: int | None = None,
        context: dict | None = None,
    ) -> None:
        with self.dependencies.task_lock:
            parent = self.dependencies.task_repository.get(parent_task_id) if parent_task_id else None
            trace_id = parent.trace_id if parent else ""
            if not trace_id and parent_task_id:
                trace_id = f"tr_{hashlib.sha256(parent_task_id.encode('utf-8')).hexdigest()[:16]}"
            if not trace_id:
                trace_id = self.dependencies.new_trace_id()
            parent_cancelled = bool(parent_task_id and parent and parent.cancel_requested)
            task = Task(
                id=task_id,
                task_type=task_type,
                status=TaskStatus.CANCELLED if parent_cancelled else TaskStatus.QUEUED,
                client_id=client_id,
                parent_task_id=parent_task_id,
                group_concurrency=group_concurrency,
                context=dict(context or {}),
                result=(self.dependencies.agent_result_payload(
                    AgentResult.cancelled("父任务已取消，子任务不再启动。"),
                    error="父任务已取消，子任务不再启动。",
                ) if parent_cancelled else None),
                cancel_requested=parent_cancelled,
                debug_agent=bool(debug_agent),
                trace_id=trace_id,
            )
            stored_task = self.dependencies.task_repository.create(task)
            if parent_cancelled:
                self.dependencies.review_record_sink(stored_task)
        self.dependencies.runtime_log(
            task_type,
            "任务已创建并进入队列。" if not parent_cancelled else "父任务已取消，子任务不再启动。",
            level="WARN" if parent_cancelled else "INFO",
            event="task_created",
            trace_id=trace_id,
            task_id=task_id,
        )
        if len(self.dependencies.tasks) % 16 == 0:
            self.dependencies.gc_tasks()

    def track_task_future(self, task_id: str, future, label: str):
        def complete(completed) -> None:
            try:
                completed.result()
            except CancelledError:
                message, status = f"{label}在全局线程池排队期间被取消。", "cancelled"
            except BaseException as exc:
                message, status = f"{label}异常终止：{type(exc).__name__}: {exc}", "error"
            else:
                with self.dependencies.task_lock:
                    current = self.dependencies.tasks.get(task_id) or {}
                    current_status = str(current.get("status") or "")
                    cancel_requested = bool(current.get("cancel_requested"))
                if current_status in {"done", "error", "cancelled"}:
                    return
                status = "cancelled" if cancel_requested else "error"
                message = f"{label}已取消。" if cancel_requested else f"{label}后台作业已结束，但未写入终态。"
            result = self.dependencies.agent_result_payload(
                AgentResult.cancelled(message) if status == "cancelled" else AgentResult.failed(message),
                error=message,
            )
            if status == "error":
                self.dependencies.attach_error_metadata(result, message)
            with self.dependencies.task_lock:
                task = self.dependencies.tasks.get(task_id)
                if not task or task.get("status") in {"done", "error", "cancelled"}:
                    return
                self.dependencies.finish_task(task_id, status, result)
            self.dependencies.task_log(task_id, message)

        future.add_done_callback(complete)
        return future

    def submit_requirement_review(self, payload: dict) -> tuple[str, dict]:
        task_id = f"requirement-review-{uuid.uuid4().hex[:12]}"
        self.create_task_record(
            task_id,
            debug_agent=True,
            task_type="requirement-review",
            client_id=str(payload.get("clientId") or ""),
            group_concurrency=1,
            context={"apiNames": list(payload.get("apiNames") or [])},
        )
        self.track_task_future(
            task_id,
            self.dependencies.task_pool.submit(
                task_id,
                task_id,
                self.dependencies.run_agent_requirement_review,
                payload,
                task_id,
                group_limit=1,
            ),
            "需求评审",
        )
        return task_id, self.dependencies.task_view(task_id) or {}

    def submit_phase1(self, payload: dict) -> tuple[str, dict]:
        task_id = str(uuid.uuid4())
        self.create_task_record(
            task_id,
            debug_agent=True,
            task_type="phase1",
            client_id=str(payload.get("clientId") or ""),
            group_concurrency=1,
            context={
                "factorName": str(payload.get("factorName") or "").strip(),
                "requirement": str(payload.get("requirement") or ""),
            },
        )
        self.track_task_future(
            task_id,
            self.dependencies.task_pool.submit(
                task_id,
                task_id,
                self.dependencies.run_agent_phase1,
                {**payload, "debugAgent": True},
                task_id,
                group_limit=1,
            ),
            "Phase 1",
        )
        return task_id, self.dependencies.task_view(task_id) or {}

    def submit_phase2(self, payload: dict) -> tuple[str, dict]:
        task_id = str(uuid.uuid4())
        self.create_task_record(
            task_id,
            debug_agent=True,
            task_type="phase2",
            client_id=str(payload.get("clientId") or ""),
            group_concurrency=1,
            context={
                "factorName": str(payload.get("factorName") or "").strip(),
                "requirement": str(payload.get("requirement") or ""),
                "standardFileId": str(payload.get("standardFileId") or ""),
            },
        )
        self.track_task_future(
            task_id,
            self.dependencies.task_pool.submit(
                task_id,
                task_id,
                self.dependencies.run_agent_phase2,
                {**payload, "debugAgent": True},
                task_id,
                group_limit=1,
            ),
            "Phase 2",
        )
        return task_id, self.dependencies.task_view(task_id) or {}

    def submit_batch_review(self, payload: dict) -> tuple[str, dict]:
        rows = payload.get("rows") or []
        if not isinstance(rows, list) or not rows:
            raise ValueError("批量评审缺少有效数据行。")
        task_id = str(uuid.uuid4())
        concurrency = self.dependencies.get_runtime_settings()["batchConcurrency"]
        self.create_task_record(
            task_id,
            task_type="batch",
            client_id=str(payload.get("clientId") or ""),
            group_concurrency=concurrency,
            context={
                "taskName": str(payload.get("taskName") or "").strip()
                or f"批量评审 · {time.strftime('%Y-%m-%d %H:%M')}"
            },
        )
        self.dependencies.task_repository.set_result(task_id, {"items": []})
        self.dependencies.start_batch_review(
            payload,
            task_id,
            dependencies=self.dependencies.batch_workflow_dependencies(),
        )
        return task_id, self.dependencies.task_view(task_id) or {}

    def submit_regression_task(self, payload: dict, examples: list[dict]) -> tuple[str, dict, dict[str, str]]:
        task_id = str(uuid.uuid4())
        self.create_task_record(
            task_id,
            task_type="regression",
            client_id=str(payload.get("clientId") or ""),
            group_concurrency=1,
            context={
                "taskName": str(payload.get("taskName") or "").strip()
                or f"质量验证 · {time.strftime('%Y-%m-%d %H:%M')}"
            },
        )
        dependencies = self.dependencies.regression_workflow_dependencies()
        run_ids = self.dependencies.prepare_validation_runs(task_id, examples, dependencies=dependencies)
        task_payload = {
            **payload,
            "exampleIds": [str(item.get("id") or "") for item in examples],
            "_validationRunIds": run_ids,
        }
        self.dependencies.task_repository.update(task_id, caseIds=list(run_ids.keys()))
        self.dependencies.task_repository.set_result(
            task_id,
            {"items": [], "summary": {"total": len(examples), "pass": 0, "fail": 0, "error": 0, "skip": 0, "running": 0}},
        )
        self.track_task_future(
            task_id,
            self.dependencies.task_pool.submit(
                task_id,
                task_id,
                self.dependencies.run_regression_validation,
                task_payload,
                task_id,
                dependencies=dependencies,
                group_limit=1,
            ),
            "质量验证",
        )
        return task_id, self.dependencies.decorate_task(self.dependencies.tasks[task_id]), run_ids

    def submit_review_mode_experiment(self, payload: dict, examples: list[dict]) -> tuple[str, dict]:
        task_id = str(uuid.uuid4())
        experiment_id = f"experiment-{self.dependencies.task_output_token(task_id)}"
        repetitions = max(1, min(3, int(payload.get("repetitions") or 1)))
        self.create_task_record(
            task_id,
            task_type="review-mode-experiment",
            group_concurrency=1,
            context={
                "taskName": str(payload.get("taskName") or "").strip()
                or f"模式对比实验 · {time.strftime('%Y-%m-%d %H:%M')}"
            },
        )
        task_payload = {
            **payload,
            "experimentId": experiment_id,
            "caseIds": [str(item.get("id") or "") for item in examples],
            "repetitions": repetitions,
        }
        initial = {
            "experimentId": experiment_id,
            "taskId": task_id,
            "status": "queued",
            "caseIds": task_payload["caseIds"],
            "caseCount": len(examples),
            "repetitions": repetitions,
            "createdAt": self.dependencies.iso_time(),
            "startedAt": None,
            "finishedAt": None,
            "items": [],
            "summary": self.dependencies.summarize_review_mode_experiment([], len(examples) * repetitions),
            "error": None,
        }
        self.dependencies.upsert_review_mode_experiment(initial)
        self.dependencies.task_repository.update(task_id, caseIds=task_payload["caseIds"], experimentId=experiment_id)
        self.dependencies.task_repository.set_result(task_id, initial)
        dependencies = self.dependencies.experiment_workflow_dependencies()
        self.track_task_future(
            task_id,
            self.dependencies.task_pool.submit(
                task_id,
                task_id,
                self.dependencies.run_review_mode_experiment,
                task_payload,
                task_id,
                dependencies=dependencies,
                group_limit=1,
            ),
            "评审模式对比实验",
        )
        return experiment_id, self.dependencies.decorate_task(self.dependencies.tasks[task_id])

    def run_scheduled_phase1(self, payload: dict, task_id: str) -> dict:
        self.create_task_record(task_id, task_type="scheduled-phase1", parent_task_id=task_id.rsplit("-p1-", 1)[0])
        self.dependencies.run_agent_phase1(payload, task_id)
        task = self.dependencies.task_repository.get(task_id)
        return {"status": task.status.value if task else "error", "result": dict(task.result or {}) if task else {}}

    def run_scheduled_phase2(self, payload: dict, task_id: str) -> dict:
        self.create_task_record(task_id, task_type="scheduled-phase2", parent_task_id=task_id.rsplit("-p2-", 1)[0])
        self.dependencies.run_agent_phase2(payload, task_id)
        task = self.dependencies.task_repository.get(task_id)
        return {"status": task.status.value if task else "error", "result": dict(task.result or {}) if task else {}}

    def run_scheduled_review_pair(self, row: dict, payload: dict, task_id: str, index: int) -> dict:
        return self.dependencies.run_review_pair(
            row,
            payload,
            task_id,
            index,
            dependencies=self.dependencies.batch_workflow_dependencies(),
        )
