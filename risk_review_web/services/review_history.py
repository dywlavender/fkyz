from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from risk_review_web.paths import (
    OUTPUTS_DIR,
    ROOT,
)
from risk_review_web.services.artifact_catalog import list_outputs as list_output_artifacts
from risk_review_web.services.review_evaluation import collect_review_stats, detect_verdict_text
from risk_review_web.services.time_utils import iso_time as _iso_time
from risk_review_web.utils import read_text


def _is_terminal_status(status: str | None) -> bool:
    """Return whether an in-memory task can be persisted as historical data."""
    return status in ("done", "error", "cancelled")


def list_outputs():
    """List review artifacts from the configured output directory."""
    return list_output_artifacts(OUTPUTS_DIR, ROOT)


def _review_record_artifact_names(record: dict) -> set[str]:
    """Collect safe artifact basenames referenced by a history record."""
    names = set()
    for group_name in ("standardArtifacts", "reviewArtifacts"):
        for file_name in (record.get(group_name) or {}).values():
            if file_name:
                names.add(str(file_name))
    for item in record.get("items") or []:
        for key in ("standardFileId", "standardMetadataFileId", "reviewFileId"):
            if item.get(key):
                names.add(str(item[key]))
    return names


def _task_record_title(task: dict) -> str:
    """Choose the most useful human-readable title available on a task."""
    context = task.get("context") or {}
    task_type = str(task.get("taskType") or "")
    if context.get("taskName"):
        return str(context["taskName"])
    if context.get("factorName"):
        return str(context["factorName"])
    labels = {
        "batch": "批量评审",
        "regression": "质量验证",
        "review-mode-experiment": "模式对比实验",
        "phase1": "标准生成",
        "phase2": "单因子评审",
    }
    return f"{labels.get(task_type, '评审任务')} · {str(task.get('id') or '')[:8]}"


def normalize_review_item(item: dict) -> dict:
    """Fill detail-level verdict fields from the report while preserving source data."""
    normalized = dict(item)
    status = str(normalized.get("status") or "")
    raw = str(normalized.get("raw") or normalized.get("reportPreview") or "")
    conclusion = str(normalized.get("conclusion") or "").strip()
    risk_count = normalized.get("riskCount")

    if not conclusion and raw:
        conclusion = detect_verdict_text(raw)
    if risk_count is None and raw:
        risk_count = int(collect_review_stats(raw).get("fail") or 0)

    if (not conclusion or risk_count is None) and normalized.get("reviewFileId"):
        artifact_conclusion, artifact_risk_count = _artifact_conclusion(
            str(normalized.get("reviewFileId") or "")
        )
        conclusion = conclusion or artifact_conclusion
        if risk_count is None:
            risk_count = artifact_risk_count

    if not conclusion and status in {"done", "pass", "fail", "error", "skip", "cancelled"}:
        conclusion = "unreviewable"
    if risk_count is None and status in {"done", "pass", "fail", "error", "skip", "cancelled"}:
        risk_count = 0

    if conclusion:
        normalized["conclusion"] = conclusion
    if risk_count is not None:
        normalized["riskCount"] = int(risk_count or 0)
    return normalized


def _task_to_review_record(task: dict, *, include_active: bool = False) -> dict | None:
    """Project an in-memory task into the stable review-history schema."""
    status = str(task.get("status") or "")
    if not include_active and not _is_terminal_status(status):
        return None
    task_type = str(task.get("taskType") or "")
    if task_type in {
        "child",
        "comparison-direct",
        "scheduled-phase1",
        "scheduled-phase2",
        "requirement-review",
    }:
        return None
    result = task.get("result") or {}
    context = task.get("context") or {}
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        items = []
    else:
        items = [normalize_review_item(item) for item in items if isinstance(item, dict)]
    standard_file = result.get("standardFileId") or context.get("standardFileId")
    standard_metadata = result.get("standardMetadataFileId")
    if standard_file and not standard_metadata:
        standard_stem = Path(str(standard_file)).stem
        for metadata_candidate in (
            f"{standard_stem}.metadata.json",
            f"{standard_stem}.summary.json",
        ):
            if (OUTPUTS_DIR / metadata_candidate).is_file():
                standard_metadata = metadata_candidate
                break
    review_file = result.get("reviewFileId")
    conclusion = "unreviewable"
    risk_count = 0
    if review_file:
        try:
            review_text = read_text(OUTPUTS_DIR / Path(str(review_file)).name)
            conclusion = detect_verdict_text(review_text)
            risk_count = int(collect_review_stats(review_text).get("fail") or 0)
        except OSError:
            pass
    done_items = [item for item in items if item.get("status") in {"done", "pass", "fail"}]
    failed_items = [item for item in items if item.get("status") in {"error", "skip"}]
    if items:
        conclusions = [
            str(item.get("conclusion") or detect_verdict_text(str(item.get("raw") or "")))
            for item in done_items
            if item.get("conclusion") or item.get("raw")
        ]
        if conclusions and all(value == "pass" for value in conclusions):
            conclusion = "pass"
        elif any(value == "fail" for value in conclusions):
            conclusion = "fail"
        elif failed_items:
            conclusion = "unreviewable"
        risk_count = sum(
            int(
                item.get("riskCount")
                if item.get("riskCount") is not None
                else (item.get("summary") or {}).get("fail") or 0
            )
            for item in items
        )
    source = {
        "batch": "batch",
        "regression": "validation",
        "review-mode-experiment": "experiment",
        "phase1": "single",
        "phase2": "single",
    }.get(task_type, task_type or "single")
    created = float(task.get("created") or time.time())
    updated = float(task.get("updated") or created)
    progress = {
        "total": len(items) if items else 1,
        "completed": len(done_items) if items else int(task.get("status") == "done"),
        "failed": len(failed_items) if items else int(task.get("status") == "error"),
    }
    return {
        "recordId": f"task-{task.get('id')}",
        "id": task.get("id"),
        "taskId": task.get("id"),
        "taskType": task_type,
        "parentTaskId": task.get("parentTaskId"),
        "clientId": task.get("clientId") or "",
        "status": status,
        "context": context,
        "progress": progress,
        "title": _task_record_title(task),
        "factorName": context.get("factorName") or _task_record_title(task),
        "source": source,
        "taskStatus": status,
        "conclusion": conclusion,
        "riskCount": risk_count,
        "createdAt": datetime.fromtimestamp(created, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "updatedAt": datetime.fromtimestamp(updated, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "archived": False,
        "summary": progress,
        "standardArtifacts": {
            **({"md": standard_file} if standard_file else {}),
            **({"json": standard_metadata} if standard_metadata else {}),
        },
        "reviewArtifacts": {"md": review_file} if review_file else {},
        "items": items,
    }


def scheduled_run_to_review_record(run: dict, task: dict | None = None) -> dict:
    """Convert one terminal scheduled run into its durable history record.

    This is an event-time projection used by ``ScheduledTaskService``.  It is
    intentionally not a query-time scan of ``scheduled_runs.json``.
    """
    task = task or {}
    items: list[dict] = []
    conclusions: list[str] = []
    risk_count = 0
    for raw_item in run.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        review_file = item.get("reviewFile") or item.get("reviewFileId")
        conclusion = str(item.get("reviewConclusion") or item.get("conclusion") or "")
        item_risk = int(item.get("riskCount") or 0)
        if review_file and not conclusion:
            conclusion, item_risk = _artifact_conclusion(str(review_file))
        if not conclusion:
            conclusion = "unreviewable"
        if review_file:
            conclusions.append(conclusion)
        item.update({
            "standardFileId": item.get("standardFileId") or item.get("standardFile"),
            "reviewFileId": review_file,
            "conclusion": conclusion,
            "riskCount": item_risk,
        })
        items.append(normalize_review_item(item))
        risk_count += item_risk

    run_status = str(run.get("status") or "failed")
    task_status = "cancelled" if run_status == "cancelled" else "error" if run_status == "failed" else "done"
    conclusion = (
        "fail" if "fail" in conclusions
        else "pass" if conclusions and all(value == "pass" for value in conclusions)
        else "unreviewable"
    )
    task_name = str(run.get("taskName") or task.get("taskName") or "定时评审")
    created_at = run.get("createdAt") or run.get("startedAt") or _iso_time()
    updated_at = run.get("finishedAt") or run.get("startedAt") or created_at
    summary = {
        "total": int(run.get("total") or len(items)),
        "completed": int(run.get("success") or 0),
        "failed": int(run.get("failed") or 0),
    }
    return {
        "recordId": f"scheduled-{run.get('runId')}",
        "id": run.get("runId"),
        "taskId": run.get("runId"),
        "parentTaskId": run.get("taskId"),
        "taskType": "scheduled",
        "status": task_status,
        "taskStatus": task_status,
        "runStatus": run_status,
        "title": task_name,
        "factorName": task_name,
        "source": "scheduled",
        "context": {
            "taskName": task_name,
            "scheduleType": task.get("scheduleType"),
            "mode": task.get("mode"),
            "runtimeMode": task.get("runtimeMode"),
        },
        "conclusion": conclusion,
        "riskCount": risk_count,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "archived": False,
        "summary": summary,
        "progress": summary,
        "standardArtifacts": {},
        "reviewArtifacts": {},
        "items": items,
        "persistenceError": run.get("persistenceError"),
    }


def _artifact_conclusion(file_name: str | None) -> tuple[str, int]:
    """Read a report artifact and return its verdict and hard-issue count."""
    if not file_name:
        return "unreviewable", 0
    try:
        text = read_text(OUTPUTS_DIR / Path(str(file_name)).name)
    except OSError:
        return "unreviewable", 0
    return detect_verdict_text(text), int(collect_review_stats(text).get("fail") or 0)
