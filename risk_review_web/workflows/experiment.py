from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from risk_review_web.domain.agent_result import AgentResult, AgentStatus
from risk_review_web.workflows.batch_review import run_review_pipeline
from risk_review_web.workflows.dependencies import ExperimentWorkflowDependencies


def experiment_mode_result(
    example: dict,
    report: str,
    *,
    dependencies: ExperimentWorkflowDependencies,
    duration_ms: int,
    **artifacts,
) -> dict:
    """Evaluate one experiment-mode report against the case's expectations."""
    verdict = dependencies.batch.detect_verdict_text(report)
    conclusion_matched = dependencies.expected_matches_verdict(example, verdict)
    issue_evaluation = dependencies.evaluate_issue_expectations(example, report)
    issue_configured = bool(issue_evaluation.get("configured"))
    issues_matched = bool(issue_evaluation.get("matched")) if issue_configured else None
    return {
        "status": "success",
        "actualConclusion": verdict,
        "conclusionMatched": conclusion_matched,
        "issuesMatched": issues_matched,
        "matched": conclusion_matched and bool(issue_evaluation.get("matched")),
        "issueEvaluation": issue_evaluation,
        "issueStats": dependencies.batch.collect_review_stats(report),
        "durationMs": duration_ms,
        **artifacts,
    }


def summarize_experiment_mode(items: list[dict], mode: str) -> dict:
    """Aggregate accuracy, errors, and latency for one experiment mode."""
    results = [
        item.get(mode) or {}
        for item in items
        if (item.get(mode) or {}).get("status") in ("success", "error", "cancelled")
    ]
    successes = [item for item in results if item.get("status") == "success"]
    issue_evaluated = [item for item in successes if item.get("issuesMatched") is not None]
    durations = [int(item.get("durationMs") or 0) for item in successes]
    return {
        "completed": len(results),
        "success": len(successes),
        "errors": len([item for item in results if item.get("status") == "error"]),
        "conclusionMatched": len([item for item in successes if item.get("conclusionMatched")]),
        "issueEvaluated": len(issue_evaluated),
        "issuesMatched": len([item for item in issue_evaluated if item.get("issuesMatched")]),
        "fullyMatched": len([item for item in successes if item.get("matched")]),
        "conclusionAccuracy": round(
            100 * len([item for item in successes if item.get("conclusionMatched")]) / len(successes),
            1,
        ) if successes else None,
        "issueAccuracy": round(
            100 * len([item for item in issue_evaluated if item.get("issuesMatched")]) / len(issue_evaluated),
            1,
        ) if issue_evaluated else None,
        "fullAccuracy": round(
            100 * len([item for item in successes if item.get("matched")]) / len(successes),
            1,
        ) if successes else None,
        "averageDurationMs": round(sum(durations) / len(durations)) if durations else None,
    }


def summarize_review_mode_experiment(items: list[dict], total: int) -> dict:
    """Combine direct and two-phase metrics into the experiment summary."""
    direct = summarize_experiment_mode(items, "direct")
    two_stage = summarize_experiment_mode(items, "twoStage")
    winner = "tie"
    if direct.get("fullAccuracy") is not None and two_stage.get("fullAccuracy") is not None:
        if direct["fullAccuracy"] > two_stage["fullAccuracy"]:
            winner = "direct"
        elif two_stage["fullAccuracy"] > direct["fullAccuracy"]:
            winner = "twoStage"
    return {
        "total": total,
        "completed": len([
            item for item in items
            if (item.get("direct") or {}).get("status") in ("success", "error", "cancelled")
            and (item.get("twoStage") or {}).get("status") in ("success", "error", "cancelled")
        ]),
        "direct": direct,
        "twoStage": two_stage,
        "winner": winner,
    }


def mock_direct_review_report(
    example: dict,
    *,
    dependencies: ExperimentWorkflowDependencies,
) -> str:
    """Generate a deterministic direct-review report for Mock experiments."""
    expected = dependencies.validation_expected_code(example.get("expected"))
    expected_patterns = dependencies.string_list(example.get("expectedIssuePatterns"))
    if expected == "fail":
        issue = expected_patterns[0] if expected_patterns else "加工逻辑与需求存在明确不一致"
        conclusion = "不通过，需修改"
        count = 1
        rows = (
            f"| I-001 | {issue} | 需求与加工逻辑直接对照 | 待评审逻辑存在对应差异 | "
            "导致加工结果偏离需求 | 按需求修正对应加工逻辑 |"
        )
    else:
        conclusion = "✅ 通过"
        count = 0
        rows = "无"
    return f"""# 直接评审报告

## 总体结论

| 项目 | 内容 |
|---|---|
| 总体结论 | {conclusion} |
| 问题统计 | 不符合 {count} 项 |

## 汇总问题清单

| 序号 | 核心问题 | 需求原文 | 待评审原文 | 影响 | 修改建议 |
|---|---|---|---|---|---|
{rows}
"""


def run_direct_experiment_review(
    parent_task_id: str,
    example: dict,
    item_index: int,
    *,
    dependencies: ExperimentWorkflowDependencies,
    mock: bool | None = None,
) -> tuple[str, str]:
    """Run one direct-review experiment item and persist its child-task state."""
    child_id = f"{parent_task_id}-{item_index}-direct"
    dependencies.create_task_record(child_id, task_type="comparison-direct", parent_task_id=parent_task_id)
    dependencies.task_repository.start(child_id)
    use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or mock is True
    if use_mock:
        time.sleep(0.05)
        status = "cancelled" if dependencies.task_cancel_requested(child_id) else "done"
        result = dependencies.agent_result_payload(
            AgentResult.cancelled("直接评审已取消")
            if status == "cancelled"
            else AgentResult.succeeded(mock_direct_review_report(example, dependencies=dependencies))
        )
    else:
        prompt = dependencies.build_direct_review_prompt(
            requirement=str(example.get("requirement") or ""),
            draft=str(example.get("draft") or example.get("reviewContent") or ""),
        )
        with tempfile.TemporaryDirectory(prefix="risk-review-direct-") as workspace:
            agent_result = dependencies.run_model_prompt(
                child_id,
                "Direct Review",
                prompt,
                isolated_workspace=Path(workspace),
            )
        result = dependencies.agent_result_payload(agent_result)
        status = "done" if agent_result.ok else "cancelled" if agent_result.status is AgentStatus.CANCELLED else "error"
    if status != "done":
        dependencies.finish_task(child_id, status, result)
        raise RuntimeError(result.get("error") or "直接评审失败")
    report = str(result.get("content") or "").strip()
    try:
        if not report.startswith("# 直接评审报告"):
            raise ValueError("直接评审输出格式错误：必须以“# 直接评审报告”开头")
        safe_case = re.sub(r"[^a-zA-Z0-9_-]", "-", str(example.get("id") or "case")).strip("-") or "case"
        file_id = f"direct-review-{safe_case}-{time.strftime('%Y-%m-%d')}-{dependencies.task_output_token(child_id)}.md"
        saved = "\n".join([
            "---",
            "mode: direct_review_experiment",
            f"case_id: {safe_case}",
            f"generated: {time.strftime('%Y-%m-%d')}",
            "---",
            report,
            "",
        ])
        dependencies.output_dir.mkdir(exist_ok=True)
        dependencies.atomic_write_text(dependencies.output_dir / file_id, saved)
    except Exception as exc:
        result["error"] = str(exc)
        dependencies.finish_task(child_id, "error", result)
        raise
    result["reviewFileId"] = file_id
    dependencies.finish_task(child_id, "done", result)
    return saved, file_id


def run_review_mode_experiment(
    payload: dict,
    task_id: str,
    *,
    dependencies: ExperimentWorkflowDependencies,
) -> None:
    """Compare direct review with the two-phase pipeline across selected cases."""
    experiment_id = str(payload.get("experimentId") or task_id)
    requested_ids = {str(value) for value in (payload.get("caseIds") or []) if str(value).strip()}
    repetitions = max(1, min(3, int(payload.get("repetitions") or 1)))
    with dependencies.examples_lock:
        examples = [
            item for item in dependencies.parse_examples()
            if not requested_ids or str(item.get("id") or "") in requested_ids
        ]
    total = len(examples) * repetitions
    items: list[dict] = []
    started_at = dependencies.iso_time()
    dependencies.task_repository.start(task_id)
    dependencies.upsert_review_mode_experiment({
        "experimentId": experiment_id,
        "taskId": task_id,
        "status": "running",
        "startedAt": started_at,
        "items": items,
        "summary": summarize_review_mode_experiment(items, total),
    })
    dependencies.task_log(task_id, f"评审模式对比实验开始：{len(examples)} 个案例 × {repetitions} 次")
    for round_index in range(repetitions):
        for case_index, example in enumerate(examples):
            if dependencies.task_cancel_requested(task_id):
                break
            item_index = round_index * len(examples) + case_index
            item = {
                "itemId": f"{example.get('id')}-r{round_index + 1}",
                "caseId": str(example.get("id") or ""),
                "caseName": str(example.get("title") or example.get("id") or ""),
                "round": round_index + 1,
                "expectedConclusion": dependencies.validation_expected_code(example.get("expected")),
                "issueBaselineConfigured": bool(
                    dependencies.string_list(example.get("expectedIssuePatterns"))
                    or dependencies.string_list(example.get("forbiddenIssuePatterns"))
                    or dependencies.optional_nonnegative_int(example.get("maxIssueCount")) is not None
                ),
                "direct": {"status": "running"},
                "twoStage": {"status": "pending"},
            }
            items.append(item)
            dependencies.upsert_review_mode_experiment({
                "experimentId": experiment_id,
                "items": items,
                "summary": summarize_review_mode_experiment(items, total),
            })
            direct_started = time.monotonic()
            try:
                report, file_id = run_direct_experiment_review(
                    task_id,
                    example,
                    item_index,
                    dependencies=dependencies,
                    mock=payload.get("mock"),
                )
                item["direct"] = experiment_mode_result(
                    example,
                    report,
                    dependencies=dependencies,
                    duration_ms=round((time.monotonic() - direct_started) * 1000),
                    reviewFile=file_id,
                )
            except Exception as exc:
                item["direct"] = {
                    "status": "cancelled" if dependencies.task_cancel_requested(task_id) else "error",
                    "durationMs": round((time.monotonic() - direct_started) * 1000),
                    "error": str(exc),
                }
            if dependencies.task_cancel_requested(task_id):
                item["twoStage"] = {"status": "cancelled", "error": "实验已终止"}
                break
            item["twoStage"] = {"status": "running"}
            dependencies.upsert_review_mode_experiment({
                "experimentId": experiment_id,
                "items": items,
                "summary": summarize_review_mode_experiment(items, total),
            })
            two_stage_started = time.monotonic()
            try:
                phase1_payload = {
                    "factorName": example.get("factorName"),
                    "requirement": example.get("requirement"),
                    "apiNames": example.get("apiNames") or [],
                    "mock": payload.get("mock"),
                    "forceRefresh": True,
                }
                pipeline = run_review_pipeline(
                    task_id,
                    item_index,
                    phase1_payload,
                    str(example.get("draft") or ""),
                    dependencies=dependencies.batch,
                    child_suffix="comparison",
                    mock=payload.get("mock"),
                    should_stop=lambda: dependencies.task_cancel_requested(task_id),
                )
                if pipeline.get("stopped"):
                    raise RuntimeError(str(pipeline.get("raw") or "两阶段评审已终止"))
                phase1_result = pipeline.get("phase1") or {}
                report = str(pipeline.get("raw") or "")
                item["twoStage"] = experiment_mode_result(
                    example,
                    report,
                    dependencies=dependencies,
                    duration_ms=round((time.monotonic() - two_stage_started) * 1000),
                    standardFile=phase1_result.get("standardFileId"),
                    standardMetadataFile=phase1_result.get("standardMetadataFileId"),
                    reviewFile=pipeline.get("reviewFileId"),
                )
            except Exception as exc:
                item["twoStage"] = {
                    "status": "cancelled" if dependencies.task_cancel_requested(task_id) else "error",
                    "durationMs": round((time.monotonic() - two_stage_started) * 1000),
                    "error": str(exc),
                }
            dependencies.upsert_review_mode_experiment({
                "experimentId": experiment_id,
                "items": items,
                "summary": summarize_review_mode_experiment(items, total),
            })
            dependencies.task_repository.set_result(
                task_id,
                {
                    "experimentId": experiment_id,
                    "items": items,
                    "summary": summarize_review_mode_experiment(items, total),
                },
                outputs=dependencies.list_outputs(),
            )
        if dependencies.task_cancel_requested(task_id):
            break
    final_status = "cancelled" if dependencies.task_cancel_requested(task_id) else "done"
    summary = summarize_review_mode_experiment(items, total)
    experiment = dependencies.upsert_review_mode_experiment({
        "experimentId": experiment_id,
        "status": final_status,
        "finishedAt": dependencies.iso_time(),
        "items": items,
        "summary": summary,
        "error": None,
    })
    dependencies.finish_task(task_id, final_status, experiment)
    dependencies.task_log(task_id, "评审模式对比实验已终止" if final_status == "cancelled" else "评审模式对比实验完成")


__all__ = [
    "experiment_mode_result",
    "summarize_experiment_mode",
    "summarize_review_mode_experiment",
    "mock_direct_review_report",
    "run_direct_experiment_review",
    "run_review_mode_experiment",
]
