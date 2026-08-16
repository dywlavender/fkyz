from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    """The only outcomes an in-process Agent call can have."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Pure model output, deliberately independent from task/artifact state.

    ``content`` is the model's final Markdown response.  Tool events and
    diagnostics are carried in ``metadata``/the task event log, not mixed into
    the response body.
    """

    status: AgentStatus
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is AgentStatus.SUCCEEDED

    @classmethod
    def succeeded(cls, content: str, *, metadata: dict[str, Any] | None = None) -> "AgentResult":
        return cls(AgentStatus.SUCCEEDED, str(content or ""), metadata=dict(metadata or {}))

    @classmethod
    def failed(cls, error: str, *, metadata: dict[str, Any] | None = None) -> "AgentResult":
        return cls(AgentStatus.FAILED, error=str(error or "Agent 调用失败"), metadata=dict(metadata or {}))

    @classmethod
    def cancelled(cls, error: str = "Agent 调用已取消。", *, metadata: dict[str, Any] | None = None) -> "AgentResult":
        return cls(AgentStatus.CANCELLED, error=str(error), metadata=dict(metadata or {}))

    def as_dict(self) -> dict[str, Any]:
        """Serialize the new internal contract without process-style fields."""
        return {
            "status": self.status.value,
            "content": self.content,
            "error": self.error,
            "metadata": dict(self.metadata),
        }
