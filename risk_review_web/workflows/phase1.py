from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .stage_executor import PreparedStage, StageDefinition


def mock_phase1_markdown() -> str:
    """Return the deterministic Phase 1 fixture used by local Mock runs."""
    return """# 标准加工规范

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `授信申请接口.applyTime` | 时间筛选锚点 |
| `外部接口.content[].mockDate` | 时间筛选 |
| `外部接口.content[].mockAmount` | 聚合求和 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | `time_filter` | 将 `授信申请接口.applyTime` 转换为月份粒度，作为窗口终点 `endMonth` |
| 2 | `time_filter` | 窗口起点 `startMonth = endMonth - 12个月` |
| 3 | `iteration` | 遍历 `外部接口.content[]` 中的全部明细 |
| 4 | `time_filter` | 保留满足 `startMonth ≤ mockDate ≤ endMonth` 的明细，起点和终点均包含 |
| 5 | `aggregation` | 对保留明细的 `mockAmount` 求和 |

## 默认值兜底

因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。
"""


@dataclass(frozen=True, slots=True)
class Phase1Definition:
    """Adapter for Phase 1-specific preparation, normalization and persistence."""

    prepare_fn: Callable[[Any, Any], PreparedStage]
    prompt_fn: Callable[[PreparedStage], Any]
    mock_fn: Callable[[PreparedStage], str]
    normalize_fn: Callable[[str, PreparedStage], str]
    validate_fn: Callable[[str, PreparedStage], None]
    persist_fn: Callable[[str, PreparedStage], Any]
    workspace_fn: Callable[[PreparedStage], Any]
    name: str = "Phase 1"

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
