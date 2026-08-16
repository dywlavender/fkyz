from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    DONE = "done"
    ERROR = "error"


class InvalidTaskTransition(RuntimeError):
    """Raised when a task is asked to move across an illegal state edge."""


@dataclass(frozen=True, slots=True)
class TaskError:
    message: str
    error_code: str | None = None
    error_category: str | None = None
    suggested_action: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"message": self.message}
        if self.error_code:
            result["errorCode"] = self.error_code
        if self.error_category:
            result["errorCategory"] = self.error_category
        if self.suggested_action:
            result["suggestedAction"] = self.suggested_action
        return result


@dataclass(frozen=True, slots=True)
class TaskResult(Mapping[str, Any]):
    """Immutable task result envelope.

    The payload intentionally remains mapping-shaped at the HTTP boundary, but
    the repository stores a defensive copy and only exposes copies of nested
    values.  This keeps existing ``result.get(...)`` consumers working without
    allowing a callback to mutate repository state by retaining a list alias.
    """

    data: Mapping[str, Any] = field(default_factory=dict)
    error: TaskError | None = None

    def __post_init__(self) -> None:
        raw = self.data if isinstance(self.data, Mapping) else {"value": self.data}
        object.__setattr__(self, "data", MappingProxyType(deepcopy(dict(raw))))

    @classmethod
    def from_value(cls, value: Any) -> "TaskResult":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if isinstance(value, Mapping):
            return cls(value)
        return cls({"value": value})

    def __getitem__(self, key: str) -> Any:
        return deepcopy(self.data[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def as_dict(self) -> dict[str, Any]:
        result = deepcopy(dict(self.data))
        if self.error is not None:
            result["error"] = self.error.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class StageArtifacts:
    content: str
    file_ids: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: TaskStatus
    artifacts: StageArtifacts | None = None
    error: TaskError | None = None


@dataclass(slots=True)
class Task:
    id: str
    task_type: str
    status: TaskStatus
    client_id: str = ""
    parent_task_id: str | None = None
    group_concurrency: int | None = None
    context: dict[str, Any] = field(default_factory=dict)
    result: TaskResult | None = None
    output_ids: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    logs: list[dict[str, Any]] = field(default_factory=list)
    debug_logs: list[dict[str, Any]] = field(default_factory=list)
    debug_agent: bool = False
    trace_id: str = ""
    case_ids: list[str] = field(default_factory=list)
    experiment_id: str | None = None
    persistence_error: str | None = None
    unpersisted: bool = False
    persistence_target_status: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED}

    def to_dict(self) -> dict[str, Any]:
        """Expose the existing HTTP-facing names from a typed task."""
        return {
            "id": self.id,
            "traceId": self.trace_id,
            "taskType": self.task_type,
            "parentTaskId": self.parent_task_id,
            "clientId": self.client_id,
            "groupConcurrency": self.group_concurrency,
            "context": dict(self.context),
            "status": self.status.value,
            "cancel_requested": self.cancel_requested,
            "result": self.result.as_dict() if self.result is not None else None,
            "outputs": list(self.output_ids),
            "logs": list(self.logs),
            "debugLogs": list(self.debug_logs),
            "debug_agent": self.debug_agent,
            "created": self.created_at,
            "updated": self.updated_at,
            "caseIds": list(self.case_ids),
            "experimentId": self.experiment_id,
            "persistenceError": self.persistence_error,
            "unpersisted": self.unpersisted,
            "persistenceTargetStatus": self.persistence_target_status,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Task":
        return cls(
            id=str(value.get("id") or value.get("taskId") or ""),
            task_type=str(value.get("taskType") or "child"),
            status=TaskStatus(str(value.get("status") or TaskStatus.QUEUED)),
            client_id=str(value.get("clientId") or ""),
            parent_task_id=value.get("parentTaskId"),
            group_concurrency=value.get("groupConcurrency"),
            context=dict(value.get("context") or {}),
            result=TaskResult.from_value(value.get("result")) if value.get("result") is not None else None,
            output_ids=list(value.get("outputs") or []),
            cancel_requested=bool(value.get("cancel_requested")),
            created_at=float(value.get("created") or value.get("createdAt") or 0),
            updated_at=float(value.get("updated") or value.get("updatedAt") or 0),
            logs=list(value.get("logs") or []),
            debug_logs=list(value.get("debugLogs") or []),
            debug_agent=bool(value.get("debug_agent")),
            trace_id=str(value.get("traceId") or ""),
            case_ids=list(value.get("caseIds") or []),
            experiment_id=value.get("experimentId"),
            persistence_error=value.get("persistenceError"),
            unpersisted=bool(value.get("unpersisted")),
            persistence_target_status=value.get("persistenceTargetStatus"),
        )


LEGAL_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.CANCELLING, TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED}),
    TaskStatus.CANCELLING: frozenset({TaskStatus.CANCELLED, TaskStatus.ERROR}),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.DONE: frozenset(),
    TaskStatus.ERROR: frozenset(),
}


def transition_status(task: Task, target: TaskStatus) -> None:
    """Apply one legal state transition in place."""
    target = TaskStatus(target)
    if target is task.status:
        return
    if target not in LEGAL_TASK_TRANSITIONS[task.status]:
        raise InvalidTaskTransition(f"{task.id}: {task.status.value} -> {target.value} 不合法")
    task.status = target
