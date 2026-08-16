from __future__ import annotations

import time
import uuid

from risk_review_web.workflows.batch_review import build_batch_draft, run_review_pipeline
from risk_review_web.workflows.dependencies import RegressionWorkflowDependencies


def validation_run_for_example(
    task_id: str,
    example: dict,
    *,
    dependencies: RegressionWorkflowDependencies,
    run_id: str | None = None,
    status: str = "pending",
) -> dict:
    """Create the initial persisted run record for one validation case."""
    return {
        "groupId": task_id,
        "taskId": task_id,
        "runId": run_id or f"validation-{dependencies.task_output_token(task_id)}-{uuid.uuid4().hex[:8]}",
        "caseId": str(example.get("id") or ""),
        "caseName": str(example.get("title") or example.get("id") or ""),
        "status": status,
        "expectedConclusion": dependencies.validation_expected_code(example.get("expected")),
        "expectedIssuePatterns": dependencies.string_list(example.get("expectedIssuePatterns")),
        "forbiddenIssuePatterns": dependencies.string_list(example.get("forbiddenIssuePatterns")),
        "maxIssueCount": dependencies.optional_nonnegative_int(example.get("maxIssueCount")),
        "actualConclusion": None,
        "conclusionMatched": None,
        "issuesMatched": None,
        "matched": None,
        "standardFile": None,
        "standardMetadataFile": None,
        "reviewFile": None,
        "startedAt": dependencies.iso_time(),
        "finishedAt": None,
        "error": None,
    }


def prepare_validation_runs(
    task_id: str,
    examples: list[dict],
    *,
    dependencies: RegressionWorkflowDependencies,
) -> dict[str, str]:
    """Pre-create run records and index their IDs by validation case."""
    run_ids = {}
    for example in examples:
        run = validation_run_for_example(task_id, example, dependencies=dependencies)
        dependencies.upsert_validation_run(run)
        run_ids[str(example.get("id") or "")] = run["runId"]
    return run_ids


def run_regression_validation(
    payload: dict,
    task_id: str,
    *,
    dependencies: RegressionWorkflowDependencies,
) -> None:
    """Execute selected validation cases sequentially inside one global-pool worker."""
    requested_ids = {str(x) for x in (payload.get("exampleIds") or []) if str(x).strip()}
    examples = dependencies.parse_examples()
    if requested_ids:
        examples = [item for item in examples if item.get("id") in requested_ids]
    if dependencies.task_cancel_requested(task_id):
        dependencies.mark_task_cancelled(task_id, "回归验证启动前已取消。")
        dependencies.cancel_validation_runs_for_task(task_id)
        return
    dependencies.task_repository.start(task_id)
    dependencies.task_repository.set_result(task_id, {"items": [], "summary": {}, "content": "", "error": None})
    dependencies.task_log(task_id, f"回归验证开始：{len(examples)} 条")
    items = []
    for index, example in enumerate(examples):
        if dependencies.task_cancel_requested(task_id):
            dependencies.task_log(task_id, "回归验证已停止：跳过剩余用例")
            break
        item = {
            "index": index,
            "id": example.get("id"),
            "title": example.get("title"),
            "factorName": example.get("factorName"),
            "status": "running",
            "verdict": "unreviewable",
            "expectedOk": False,
            "summary": {},
            "raw": "",
            "standardText": "",
            "standardFileId": None,
            "cacheHit": False,
        }
        run_id = str((payload.get("_validationRunIds") or {}).get(str(example.get("id") or "")) or "")
        run = validation_run_for_example(
            task_id,
            example,
            dependencies=dependencies,
            run_id=run_id or None,
            status="running",
        )
        run_id = run["runId"]
        item["runId"] = run_id
        dependencies.upsert_validation_run(run)
        items.append(item)
        dependencies.task_repository.set_result(
            task_id,
            {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "content": "", "error": None},
        )
        try:
            draft = build_batch_draft(example) or example.get("draft")
            phase1_payload = {
                "factorName": example.get("factorName"),
                "requirement": example.get("requirement"),
                "apiNames": example.get("apiNames") or [],
                "mock": payload.get("mock"),
            }

            def on_phase1_done(phase1_result: dict):
                item["standardFileId"] = phase1_result.get("standardFileId")
                item["standardMetadataFileId"] = phase1_result.get("standardMetadataFileId")
                item["standardText"] = phase1_result.get("content") or ""
                item["cacheHit"] = bool(phase1_result.get("cacheHit"))
                dependencies.upsert_validation_run({
                    "runId": run_id,
                    "status": "running",
                    "standardFile": phase1_result.get("standardFileId"),
                    "standardMetadataFile": phase1_result.get("standardMetadataFileId"),
                })
                dependencies.task_repository.set_result(
                    task_id,
                    {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "content": "", "error": None},
                    outputs=dependencies.list_outputs(),
                )

            pipeline = run_review_pipeline(
                task_id,
                index,
                phase1_payload,
                draft,
                dependencies=dependencies.batch,
                mock=payload.get("mock"),
                should_stop=lambda: dependencies.task_cancel_requested(task_id),
                on_phase1_done=on_phase1_done,
            )
            if pipeline.get("stopped"):
                item.update({"status": "skip", "raw": "已请求停止，Phase 1 完成后未继续执行 Phase 2", "time": int(time.time() * 1000)})
                dependencies.upsert_validation_run({
                    "runId": run_id,
                    "status": "cancelled",
                    "finishedAt": dependencies.iso_time(),
                    "error": "验证任务已终止",
                })
                dependencies.task_log(task_id, f"停止：{example.get('title') or example.get('id')} Phase 1 后停止")
                break
            report = pipeline.get("raw") or ""
            review_file_id = pipeline.get("reviewFileId")
            verdict = dependencies.detect_verdict_text(report)
            conclusion_matched = dependencies.expected_matches_verdict(example, verdict)
            issue_evaluation = dependencies.evaluate_issue_expectations(example, report)
            issues_matched = issue_evaluation["matched"]
            expected_ok = conclusion_matched and issues_matched
            item.update({
                "status": "pass" if expected_ok else "fail",
                "verdict": verdict,
                "expectedOk": expected_ok,
                "conclusionMatched": conclusion_matched,
                "issuesMatched": issues_matched,
                "issueEvaluation": issue_evaluation,
                "expected": example.get("expected") or "",
                "summary": dependencies.collect_review_stats(report),
                "raw": report,
                "reviewFileId": review_file_id,
                "reportPreview": report[:600],
                "time": int(time.time() * 1000),
            })
            dependencies.upsert_validation_run({
                "runId": run_id,
                "status": "success",
                "actualConclusion": verdict,
                "conclusionMatched": conclusion_matched,
                "issuesMatched": issues_matched,
                "issueEvaluation": issue_evaluation,
                "matched": expected_ok,
                "standardFile": item.get("standardFileId"),
                "standardMetadataFile": item.get("standardMetadataFileId"),
                "reviewFile": review_file_id,
                "finishedAt": dependencies.iso_time(),
                "error": None,
            })
            dependencies.task_log(task_id, f"验证完成：{example.get('title') or example.get('id')}（{'通过' if expected_ok else '未通过'}）")
        except Exception as exc:
            if dependencies.task_cancel_requested(task_id):
                item.update({"status": "skip", "raw": "已停止：当前模型调用已中止", "reportPreview": "已停止：当前模型调用已中止", "time": int(time.time() * 1000)})
                dependencies.upsert_validation_run({
                    "runId": run_id,
                    "status": "cancelled",
                    "finishedAt": dependencies.iso_time(),
                    "error": "验证任务已终止",
                })
                dependencies.task_log(task_id, f"验证停止：{example.get('title') or example.get('id')}")
            else:
                item.update({"status": "error", "raw": str(exc), "reportPreview": str(exc), "time": int(time.time() * 1000)})
                item.update(dependencies.classify_task_error(str(exc)))
                dependencies.upsert_validation_run({
                    "runId": run_id,
                    "status": "failed",
                    "finishedAt": dependencies.iso_time(),
                    "error": str(exc),
                })
                dependencies.task_log(task_id, f"验证失败：{example.get('title') or example.get('id')}：{exc}")
        dependencies.task_repository.set_result(
            task_id,
            {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "content": "", "error": None},
            outputs=dependencies.list_outputs(),
        )
    final_status = "cancelled" if dependencies.task_cancel_requested(task_id) else "done"
    processed_case_ids = {str(item.get("id") or "") for item in items}
    for case_id, pending_run_id in (payload.get("_validationRunIds") or {}).items():
        if case_id in processed_case_ids:
            continue
        dependencies.upsert_validation_run({
            "runId": pending_run_id,
            "status": "cancelled" if final_status == "cancelled" else "failed",
            "finishedAt": dependencies.iso_time(),
            "error": "验证任务已终止" if final_status == "cancelled" else "验证案例未执行",
        })
    result = {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "content": "", "error": None}
    dependencies.finish_task(task_id, final_status, result)
    dependencies.task_log(task_id, "回归验证已停止" if final_status == "cancelled" else "回归验证完成")


def summarize_regression_items(items: list[dict], total: int | None = None) -> dict:
    """Build progress counters and current-item metadata for a regression task."""
    running_items = [item for item in items if item.get("status") == "running"]
    current = running_items[-1] if running_items else None
    return {
        "total": len(items) if total is None else total,
        "pass": len([item for item in items if item.get("status") == "pass"]),
        "fail": len([item for item in items if item.get("status") == "fail"]),
        "error": len([item for item in items if item.get("status") == "error"]),
        "skip": len([item for item in items if item.get("status") == "skip"]),
        "cacheHit": len([item for item in items if item.get("cacheHit")]),
        "running": len(running_items),
        "currentIndex": (int(current.get("index") or 0) + 1) if current else None,
        "currentTitle": (current.get("title") or current.get("factorName") or current.get("id")) if current else "",
    }


__all__ = ["validation_run_for_example", "prepare_validation_runs", "run_regression_validation", "summarize_regression_items"]
