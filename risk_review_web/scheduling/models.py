from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ScheduledTaskError(ValueError):
    """User-facing scheduling validation or lifecycle error."""


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    task_id: str
    task_name: str
    schedule_type: str
    mode: str
    enabled: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    run_id: str
    task_id: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
