from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .stage_executor import PreparedStage


def mock_phase2_markdown(factor_name: str) -> str:
    """Return the deterministic Phase 2 fixture used by local Mock runs."""
    return f"""# 评审报告 — {factor_name}

## 总体结论
| 项目 | 内容 |
|---|---|
| 总体结论 | ✅ 通过 |
| 问题统计 | 不符合 0 项 |

## 汇总问题清单
| 序号 | 违反规范 | 核心问题 | 需求原文 | 标准加工逻辑 | 待评审原文 | 影响 | 修改建议 |
|---|---|---|---|---|---|---|---|
无
"""


@dataclass(frozen=True, slots=True)
class Phase2Definition:
    """Adapter for Phase 2's frozen-standard review behavior."""

    prepare_fn: Callable[[Any, Any], PreparedStage]
    prompt_fn: Callable[[PreparedStage], Any]
    mock_fn: Callable[[PreparedStage], str]
    normalize_fn: Callable[[str, PreparedStage], str]
    validate_fn: Callable[[str, PreparedStage], None]
    persist_fn: Callable[[str, PreparedStage], Any]
    workspace_fn: Callable[[PreparedStage], Any]
    name: str = "Phase 2"

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
