from __future__ import annotations

import re
from concurrent.futures import CancelledError
from copy import deepcopy
from typing import Any

from risk_review_web.workflows.dependencies import BatchWorkflowDependencies


def build_batch_draft(row: dict) -> str:
    """Assemble the canonical three-section review draft for one batch row."""
    parts = []
    param_list = batch_param_fields(row)
    if param_list:
        parts.append(f"【因子加工输入字段】\n{param_list}")
    if (row.get("procLogic") or "").strip():
        parts.append(f"【加工逻辑】\n{str(row.get('procLogic')).strip()}")
    if parts:
        parts.append("【默认值】\n字段缺失、格式错误或数据异常时返回默认值")
    return "\n\n".join(parts)


def batch_param_fields(row: dict) -> str:
    """Return the batch row's declared factor-processing input fields."""
    return str(row.get("paramFields") or "").strip()


def run_review_pipeline(
    parent_task_id: str,
    index: int,
    phase1_payload: dict,
    draft: str,
    *,
    dependencies: BatchWorkflowDependencies,
    child_suffix: str = "",
    mock: bool | None = None,
    should_stop=None,
    on_phase1_done=None,
) -> dict:
    """Run Phase 1 then Phase 2 in-process, honoring cancellation between stages."""
    factor_name = (phase1_payload.get("factorName") or f"row-{index + 1}").strip()
    api_names = phase1_payload.get("apiNames") or []
    if not factor_name or not phase1_payload.get("requirement") or not api_names or not draft:
        raise ValueError("缺少因子名、需求内容、接口文档或待评审内容")

    suffix = f"-{child_suffix}" if child_suffix else ""
    phase1_task_id = f"{parent_task_id}-{index}-p1{suffix}"
    if should_stop and should_stop():
        return {"stopped": True, "phase1": {}, "raw": "任务已取消，未启动 Phase 1"}
    dependencies.create_task_record(
        phase1_task_id,
        debug_agent=bool(phase1_payload.get("debugAgent")),
        parent_task_id=parent_task_id,
    )
    dependencies.run_agent_phase1(phase1_payload, phase1_task_id)
    phase1_task = dependencies.task_repository.snapshot(phase1_task_id) or {}
    if phase1_task.get("status") != "done":
        result = phase1_task.get("result") or {}
        raise RuntimeError(result.get("error") or "Phase 1 失败")
    phase1_result = phase1_task.get("result") or {}
    if on_phase1_done:
        on_phase1_done(phase1_result)

    if should_stop and should_stop():
        return {
            "stopped": True,
            "phase1": phase1_result,
            "raw": "已请求停止，Phase 1 完成后未继续执行 Phase 2",
        }

    phase2_payload = {
        "factorName": phase1_payload.get("factorName"),
        "requirement": phase1_payload.get("requirement"),
        "debugAgent": phase1_payload.get("debugAgent"),
        "draft": draft,
        "standardFileId": phase1_result.get("standardFileId"),
        "mock": mock,
    }
    phase2_task_id = f"{parent_task_id}-{index}-p2{suffix}"
    if should_stop and should_stop():
        return {"stopped": True, "phase1": phase1_result, "raw": "已请求停止，未启动 Phase 2"}
    dependencies.create_task_record(
        phase2_task_id,
        debug_agent=bool(phase2_payload.get("debugAgent")),
        parent_task_id=parent_task_id,
    )
    dependencies.run_agent_phase2(phase2_payload, phase2_task_id)
    phase2_task = dependencies.task_repository.snapshot(phase2_task_id) or {}
    if phase2_task.get("status") != "done":
        result = phase2_task.get("result") or {}
        raise RuntimeError(result.get("error") or "Phase 2 失败")
    phase2_result = phase2_task.get("result") or {}
    return {
        "stopped": False,
        "phase1": phase1_result,
        "phase2": phase2_result,
        "raw": phase2_result.get("content") or "",
        "reviewFileId": phase2_result.get("reviewFileId"),
    }


def run_review_pair(
    row: dict,
    payload: dict,
    task_id: str,
    index: int,
    *,
    dependencies: BatchWorkflowDependencies,
) -> dict:
    """Execute the configured review stages for one batch or scheduled item."""
    factor_name = (row.get("factorName") or f"row-{index + 1}").strip()
    api_names = row.get("apiNames") or [x.strip() for x in re.split(r"[、,，]+", row.get("apiDocs") or "") if x.strip()]
    draft = row.get("draft") or build_batch_draft(row)
    param_fields = batch_param_fields(row)
    item = {
        "index": index,
        "factorName": factor_name,
        "requirement": row.get("requirement") or "",
        "apiNames": api_names,
        "paramFields": param_fields,
        "procLogic": row.get("procLogic") or "",
        "defaultValues": "字段缺失、格式错误或数据异常时返回默认值",
        "draft": draft,
        "status": "running",
        "raw": "",
        "standardText": row.get("standardText") or "",
        "standardFileId": row.get("standardFileId"),
        "standardMetadataFileId": row.get("standardMetadataFileId"),
        "cacheHit": False,
        "attempts": 0,
    }
    if dependencies.task_cancel_requested(task_id):
        item.update({"status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"})
        return item
    if row.get("status") == "done" and row.get("raw") and payload.get("resume"):
        item.update({"status": "skip", "raw": row.get("raw"), "skipReason": "已完成，断点续跑跳过"})
        return item
    if not row.get("requirement") or not api_names or not draft:
        item.update({"status": "skip", "raw": "缺少需求内容、接口文档或待评审内容"})
        return item
    retry = max(0, int(payload.get("retry") or 0))
    last_error = ""
    for attempt in range(retry + 1):
        if dependencies.task_cancel_requested(task_id):
            item.update({"status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"})
            return item
        item["attempts"] = attempt + 1
        try:
            phase1_payload = {
                "factorName": factor_name,
                "requirement": row.get("requirement"),
                "apiNames": api_names,
                "mock": payload.get("mock"),
            }
            pipeline = run_review_pipeline(
                task_id,
                index,
                phase1_payload,
                draft,
                dependencies=dependencies,
                child_suffix=f"a{attempt + 1}",
                mock=payload.get("mock"),
            )
            phase1_result = pipeline["phase1"]
            item["standardFileId"] = phase1_result.get("standardFileId")
            item["standardMetadataFileId"] = phase1_result.get("standardMetadataFileId")
            item["standardText"] = phase1_result.get("content") or ""
            item["cacheHit"] = bool(phase1_result.get("cacheHit"))
            report = str(pipeline.get("raw") or "")
            summary = dependencies.collect_review_stats(report)
            item.update({
                "status": "done",
                "raw": report,
                "reviewFileId": pipeline.get("reviewFileId"),
                "conclusion": dependencies.detect_verdict_text(report),
                "riskCount": int(summary.get("fail") or 0),
                "summary": summary,
            })
            return item
        except Exception as exc:
            last_error = str(exc)
            if dependencies.task_cancel_requested(task_id):
                item.update({"status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"})
                return item
            dependencies.task_log(task_id, f"{factor_name} 第 {attempt + 1} 次失败：{last_error}")
    item.update({"status": "error", "raw": last_error, **dependencies.classify_task_error(last_error)})
    return item


def run_batch_factor_job(
    row: dict,
    payload: dict,
    task_id: str,
    index: int,
    items: list[dict],
    *,
    dependencies: BatchWorkflowDependencies,
) -> dict:
    """Run one batch item and return its result without mutating sibling items."""
    with dependencies.task_lock:
        if dependencies.task_repository.snapshot(task_id) and index < len(items):
            running_item = deepcopy(items[index])
            running_item["status"] = "running"
            dependencies.task_repository.update_item(task_id, index, running_item)
    return run_review_pair(row, payload, task_id, index, dependencies=dependencies)


def finish_batch_factor(
    task_id: str,
    index: int,
    items: list[dict],
    future,
    *,
    dependencies: BatchWorkflowDependencies,
) -> None:
    """Merge a completed batch future and finalize the parent when all items end."""
    try:
        item = future.result()
    except CancelledError:
        with dependencies.task_lock:
            base_item = deepcopy(items[index])
        item = {**base_item, "status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"}
    except Exception as exc:
        with dependencies.task_lock:
            base_item = deepcopy(items[index])
        item = {**base_item, "status": "error", "raw": str(exc), **dependencies.classify_task_error(str(exc))}

    with dependencies.task_lock:
        task_before_update = dependencies.task_repository.get(task_id)
        if not task_before_update or task_before_update.terminal:
            return
        dependencies.task_repository.update_item(task_id, index, item)
        stored_task = dependencies.task_repository.get(task_id)
        stored_result = stored_task.result.as_dict() if stored_task and stored_task.result else {}
        stored_items = stored_result.get("items") if isinstance(stored_result.get("items"), list) else []
        if item.get("status") == "done":
            message = f"完成：{item.get('factorName')}"
        elif item.get("status") == "skip":
            message = f"跳过：{item.get('factorName')}：{item.get('skipReason') or item.get('raw')}"
        else:
            message = f"失败：{item.get('factorName')}：{item.get('raw')}"
        terminal = {"done", "error", "skip"}
        all_finished = all(value.get("status") in terminal for value in stored_items)
        task = stored_task
        if not task:
            return
        result = {"items": stored_items, "content": "", "error": None}
        if all_finished:
            final_status = "cancelled" if task.cancel_requested else "done"
            dependencies.finish_task(task_id, final_status, result)
        else:
            dependencies.task_repository.set_result(task_id, result, outputs=dependencies.list_outputs())
    dependencies.task_log(task_id, message)
    if all_finished:
        dependencies.task_log(task_id, "批量评审已停止" if final_status == "cancelled" else "批量评审完成")


def start_batch_review(
    payload: dict,
    task_id: str,
    *,
    dependencies: BatchWorkflowDependencies,
) -> None:
    """Submit each batch item fairly through the shared global task pool."""
    from risk_review_web.task_pool import GLOBAL_TASK_POOL

    rows = payload.get("rows") or []
    concurrency = dependencies.task_repository.get(task_id).group_concurrency if dependencies.task_repository.get(task_id) else 1
    concurrency = concurrency or 1
    dependencies.task_repository.start(task_id)
    dependencies.task_repository.set_result(task_id, {"items": [], "content": "", "error": None})
    dependencies.task_log(task_id, f"批量评审进入全局线程池：{len(rows)} 条，本任务并发上限 {concurrency}")
    items = [
        {
            "index": index,
            "factorName": (row.get("factorName") or f"row-{index + 1}").strip(),
            "requirement": row.get("requirement") or "",
            "apiNames": row.get("apiNames") or [x.strip() for x in re.split(r"[、,，]+", row.get("apiDocs") or "") if x.strip()],
            "paramFields": batch_param_fields(row),
            "procLogic": row.get("procLogic") or "",
            "defaultValues": "字段缺失、格式错误或数据异常时返回默认值",
            "draft": row.get("draft") or build_batch_draft(row),
            "status": "queued",
            "raw": "",
            "standardText": "",
            "standardFileId": None,
            "standardMetadataFileId": None,
        }
        for index, row in enumerate(rows)
    ]
    dependencies.task_repository.set_result(task_id, {"items": items, "content": "", "error": None})
    for index, row in enumerate(rows):
        future = GLOBAL_TASK_POOL.submit(
            task_id,
            f"{task_id}-factor-{index}",
            run_batch_factor_job,
            row,
            payload,
            task_id,
            index,
            items,
            dependencies=dependencies,
            group_limit=concurrency,
        )
        future.add_done_callback(
            lambda completed, item_index=index: finish_batch_factor(
                task_id,
                item_index,
                items,
                completed,
                dependencies=dependencies,
            )
        )


__all__ = [
    "build_batch_draft",
    "batch_param_fields",
    "run_review_pipeline",
    "run_review_pair",
    "run_batch_factor_job",
    "finish_batch_factor",
    "start_batch_review",
]
