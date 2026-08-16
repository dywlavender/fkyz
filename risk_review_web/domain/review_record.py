from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """Typed boundary for one persisted review-history item."""

    record_id: str
    task_id: str = ""
    task_type: str = ""
    source: str = "single"
    status: str = "done"
    title: str = ""
    factor_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "recordId": self.record_id,
            "taskId": self.task_id,
            "taskType": self.task_type,
            "source": self.source,
            "taskStatus": self.status,
            "title": self.title,
            "factorName": self.factor_name,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "archived": self.archived,
        }
