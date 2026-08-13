from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from risk_review_web.api_docs import get_api_doc
from risk_review_web.paths import (
    REQUIREMENT_REVIEW_SKILL_PATH,
    REQUIREMENT_REVIEW_SPEC_PATH,
    ROOT,
)


def normalize_requirement_review_payload(payload: dict) -> dict:
    """Normalize and validate the only business inputs accepted by requirement review."""
    requirement = str(payload.get("requirement") or "").strip()
    api_names = list(dict.fromkeys(
        str(item).strip()
        for item in payload.get("apiNames") or []
        if str(item).strip()
    ))
    if not requirement:
        raise ValueError("请输入需求内容。")
    if not api_names:
        raise ValueError("至少选择一个接口文档。")
    missing = [name for name in api_names if get_api_doc(name) is None]
    if missing:
        raise ValueError("接口文档不存在：" + "、".join(missing))
    return {
        "requirement": requirement,
        "apiNames": api_names,
        "clientId": str(payload.get("clientId") or ""),
        "debugAgent": True,
        "mock": payload.get("mock") is True,
    }


def selected_api_documents(api_names: list[str]) -> list[dict]:
    """Resolve the exact interface documents selected for one task."""
    documents = []
    for name in api_names:
        document = get_api_doc(name)
        if document is None:
            raise ValueError(f"接口文档不存在：{name}")
        documents.append(document)
    return documents


def create_requirement_review_workspace(
    task_id: str,
    api_names: list[str],
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, list[dict]]:
    """Stage only the standalone Skill, specification, and selected API documents."""
    workspace = Path(tempfile.mkdtemp(prefix=f"risk-requirement-review-{task_id[:8]}-"))
    try:
        documents = selected_api_documents(api_names)
        sources = [REQUIREMENT_REVIEW_SKILL_PATH, REQUIREMENT_REVIEW_SPEC_PATH]
        for document in documents:
            source_path = (ROOT / str(document.get("path") or "")).resolve()
            try:
                source_path.relative_to(ROOT.resolve())
            except ValueError as exc:
                raise ValueError(f"接口文档路径越界：{document.get('name') or ''}") from exc
            sources.append(source_path)

        staged_files: list[str] = []
        for source in sources:
            source_path = source.resolve()
            if not source_path.is_file():
                raise FileNotFoundError(f"需求评审输入文件不存在：{source_path.name}")
            relative_path = source_path.relative_to(ROOT.resolve())
            target_path = workspace / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            staged_files.append(relative_path.as_posix())
        if log:
            log("需求评审隔离工作目录已创建，仅包含：" + "、".join(staged_files))
        return workspace, documents
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def mock_requirement_review_markdown(api_names: list[str]) -> str:
    """Return a deterministic complete report for the Mock smoke path."""
    return """# 需求评审报告

## 总体结论

| 项目 | 内容 |
|---|---|
| 总体结论 | ✅ 通过 |
| 问题统计 | 不符合 0 项 |

## 问题清单

| 序号 | 核心问题 | 需求原文 | 修改建议 |
|---|---|---|---|

无
"""
