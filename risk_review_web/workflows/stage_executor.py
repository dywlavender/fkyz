from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from risk_review_web.domain.agent_result import AgentResult, AgentStatus
from risk_review_web.domain.task import StageArtifacts, StageOutcome, Task, TaskError, TaskStatus


@dataclass(frozen=True, slots=True)
class PreparedStage:
    """Stage-specific prepared context passed through the common lifecycle."""

    value: Any = None
    workspace: Path | None = None
    cached_artifacts: StageArtifacts | None = None
    cached_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StageDefinition(Protocol):
    """Business-specific hooks consumed by :class:`StageExecutor`."""

    name: str

    def prepare(self, request: Any, task: Task) -> PreparedStage: ...

    def build_prompt(self, prepared: PreparedStage) -> Any: ...

    def mock(self, prepared: PreparedStage) -> str: ...

    def normalize(self, content: str, prepared: PreparedStage) -> str: ...

    def validate(self, content: str, prepared: PreparedStage) -> None: ...

    def persist(self, content: str, prepared: PreparedStage) -> StageArtifacts: ...

    def create_workspace(self, prepared: PreparedStage) -> Path | None: ...


AgentCall = Callable[[Any, Path | None, str], AgentResult]
CancelCheck = Callable[[str], bool]
LogFn = Callable[[str, str], None]
StartTaskFn = Callable[[str], None]
FinishTaskFn = Callable[[str, StageOutcome], None]


class StageExecutor:
    """Run common stage lifecycle without knowing any business Markdown rules."""

    def __init__(
        self,
        *,
        cancelled: CancelCheck,
        log: LogFn | None = None,
    ) -> None:
        self.cancelled = cancelled
        self.log = log or (lambda _task_id, _message: None)

    def run_agent_result(
        self,
        *,
        task_id: str,
        phase_label: str,
        prompt: Any,
        workspace: Path | None,
        agent_call: Callable[[], AgentResult],
        empty_error: str,
    ) -> AgentResult:
        """Shared cancellation, Agent invocation and empty-response boundary.

        This adapter deliberately returns only an ``AgentResult``; artifact
        IDs are added by each stage definition.
        """
        if self.cancelled(task_id):
            return AgentResult.cancelled(f"{phase_label} 已取消。")
        self.log(task_id, f"{phase_label} 开始生成")
        result = agent_call()
        if result.status is AgentStatus.CANCELLED:
            return result
        if result.status is AgentStatus.FAILED:
            return result
        content = result.content.strip()
        if not content:
            return AgentResult.failed(empty_error, metadata={"partial_content": result.content})
        if self.cancelled(task_id):
            return AgentResult.cancelled(
                f"{phase_label} 已取消。",
                metadata={"partial_content": content},
            )
        return AgentResult.succeeded(content, metadata=result.metadata)

    def execute(
        self,
        *,
        task: Task,
        request: Any,
        definition: StageDefinition,
        agent_call: AgentCall,
        use_mock: bool = False,
    ) -> StageOutcome:
        """Execute a complete definition-driven stage.

        This method is intentionally dependency-injected so workflows can be
        tested without importing the HTTP server or starting a model client.
        """
        if self.cancelled(task.id):
            return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
        prepared: PreparedStage | None = None
        workspace: Path | None = None
        try:
            self.log(task.id, f"{definition.name} 开始")
            prepared = definition.prepare(request, task)
            if prepared.cached_artifacts is not None:
                if self.cancelled(task.id):
                    return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
                return StageOutcome(TaskStatus.DONE, artifacts=prepared.cached_artifacts)
            if self.cancelled(task.id):
                return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
            workspace = (
                None
                if use_mock or prepared.cached_content is not None
                else definition.create_workspace(prepared)
            )
            if self.cancelled(task.id):
                return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
            if prepared.cached_content is not None:
                content = prepared.cached_content
            elif use_mock:
                content = definition.mock(prepared)
            else:
                result = agent_call(definition.build_prompt(prepared), workspace, definition.name)
                if result.status is AgentStatus.CANCELLED:
                    return StageOutcome(TaskStatus.CANCELLED, error=TaskError(result.error or "任务已取消"))
                if result.status is AgentStatus.FAILED:
                    return StageOutcome(TaskStatus.ERROR, error=TaskError(result.error or "Agent 调用失败"))
                content = result.content
            if not str(content or "").strip():
                return StageOutcome(TaskStatus.ERROR, error=TaskError(f"{definition.name} 输出为空。"))
            if self.cancelled(task.id):
                return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
            normalized = definition.normalize(str(content), prepared)
            definition.validate(normalized, prepared)
            if self.cancelled(task.id):
                return StageOutcome(TaskStatus.CANCELLED, error=TaskError(f"{definition.name} 已取消。"))
            artifacts = definition.persist(normalized, prepared)
            self.log(task.id, f"{definition.name} 完成")
            return StageOutcome(TaskStatus.DONE, artifacts=artifacts)
        except Exception as exc:
            self.log(task.id, f"{definition.name} 执行失败：{exc}")
            return StageOutcome(TaskStatus.ERROR, error=TaskError(f"{type(exc).__name__}: {exc}"))
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
                self.log(task.id, f"{definition.name} 隔离工作目录已清理")

    def run_task(
        self,
        *,
        task: Task,
        request: Any,
        definition: StageDefinition,
        agent_call: AgentCall,
        use_mock: bool,
        start_task: StartTaskFn,
        finish_task: FinishTaskFn,
    ) -> StageOutcome:
        """Own the stage-level task lifecycle around :meth:`execute`."""
        if self.cancelled(task.id):
            outcome = StageOutcome(
                TaskStatus.CANCELLED,
                error=TaskError(f"{definition.name} 已取消。"),
            )
            finish_task(task.id, outcome)
            return outcome
        start_task(task.id)
        outcome = self.execute(
            task=task,
            request=request,
            definition=definition,
            agent_call=agent_call,
            use_mock=use_mock,
        )
        finish_task(task.id, outcome)
        return outcome
