from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .stage_executor import PreparedStage


@dataclass(frozen=True, slots=True)
class RequirementReviewDefinition:
    """Adapter for the standalone requirement-admission review."""

    prepare_fn: Callable[[Any, Any], PreparedStage]
    prompt_fn: Callable[[PreparedStage], Any]
    mock_fn: Callable[[PreparedStage], str]
    normalize_fn: Callable[[str, PreparedStage], str]
    validate_fn: Callable[[str, PreparedStage], None]
    persist_fn: Callable[[str, PreparedStage], Any]
    workspace_fn: Callable[[PreparedStage], Any]
    name: str = "需求评审"

    def prepare(self, request: Any, task: Any) -> PreparedStage:
        return self.prepare_fn(request, task)

    def build_prompt(self, prepared: PreparedStage) -> Any:
        return self.prompt_fn(prepared)

    def mock(self, prepared: PreparedStage) -> str:
        return self.mock_fn(prepared)

    def normalize(self, content: str, prepared: PreparedStage) -> str:
        return self.normalize_fn(content, prepared)

    def validate(self, content: str, prepared: PreparedStage) -> None:
        self.validate_fn(content, prepared)

    def persist(self, content: str, prepared: PreparedStage):
        return self.persist_fn(content, prepared)

    def create_workspace(self, prepared: PreparedStage):
        return self.workspace_fn(prepared)
