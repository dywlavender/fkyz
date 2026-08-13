#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import CancelledError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from risk_review_web.environment import load_project_env

load_project_env()

from risk_review_web.artifacts import (
    STANDARD_SCHEMA_VERSION,
    compute_phase1_checksum,
    compute_review_checksum,
    find_cached_phase1_standard,
    normalize_phase1_output,
    normalize_review_output,
    read_phase1_standard,
    save_standard_metadata,
    strip_yaml_frontmatter,
    validate_phase1_standard_basic_output,
    validate_phase2_review_basic_output,
    normalize_requirement_review_output,
    save_requirement_review_artifacts,
    validate_requirement_review_basic_output,
)
from risk_review_web.api_docs import get_api_doc, list_api_docs
from risk_review_web.business_rules import (
    BUSINESS_RULES_LOCK,
    normalize_business_rule,
    parse_business_rules,
    save_business_rules,
    validate_business_rule,
)
from risk_review_web.paths import (
    BUSINESS_RULES_PATH,
    EXAMPLES_PATH,
    VALIDATION_RUNS_PATH,
    REVIEW_RECORDS_PATH,
    SCHEDULED_RUNS_PATH,
    OUTPUTS_DIR,
    PHASE1_SKILL_PATH,
    PHASE1_PROCESSING_SPEC_PATH,
    PHASE2_SKILL_PATH,
    PHASE2_REVIEW_SPEC_PATH,
    RULE_ENGINE_BUILD_PATH,
    REQUIREMENT_REVIEW_SKILL_PATH,
    REQUIREMENT_REVIEW_SPEC_PATH,
    ROOT,
    STATIC_DIR,
)
from risk_review_web.prompt_builder import (
    ModelPrompt,
    build_phase1_prompt,
    build_phase2_prompt,
    build_direct_review_prompt,
    build_requirement_review_prompt,
)
from risk_review_web.scheduled_tasks import build_service as build_scheduled_task_service
from risk_review_web.runtime_settings import get_runtime_settings, update_runtime_settings
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text, parse_frontmatter, read_text
from risk_review_web.error_classification import attach_error_metadata, classify_task_error
from risk_review_web.services.review_evaluation import (
    collect_review_stats,
    detect_verdict_text,
    evaluate_issue_expectations,
    expected_matches_verdict,
    optional_nonnegative_int as _optional_nonnegative_int,
    string_list as _string_list,
    validation_expected_code,
)
from risk_review_web.services.tabular_import import (
    normalize_batch_rows_from_table,
    parse_csv_rows,
    parse_xlsx,
)
from risk_review_web.services.http_responses import (
    error_response,
    json_response,
    success_response,
)
from risk_review_web.services.review_history import (
    REVIEW_RECORDS_LOCK,
    _external_review_records,
    _review_record_artifact_names,
    _task_to_review_record,
    artifact_review_history_records,
    list_outputs,
    parse_review_records,
    save_review_records,
    normalize_review_item,
)
from risk_review_web.services.time_utils import iso_time as _iso_time
from risk_review_web.services.validation_store import (
    EXAMPLES_LOCK,
    cancel_validation_runs_for_task,
    normalize_example,
    parse_examples,
    recover_stale_review_mode_experiments,
    recover_stale_validation_runs,
    save_examples,
    upsert_review_mode_experiment,
    upsert_validation_run,
    validate_example,
)
from risk_review_web.routes import scheduled as scheduled_routes
from risk_review_web.routes import static_files as static_routes
from risk_review_web.routes import system as system_routes
from risk_review_web.routes import validation as validation_routes
from risk_review_web.routes import requirement_review as requirement_review_routes
from risk_review_web.services.requirement_review import (
    create_requirement_review_workspace,
    mock_requirement_review_markdown,
    selected_api_documents,
)
from risk_review_web.services.runtime_logging import new_trace_id, runtime_log
from risk_review_web.services.model_runtime import agent_health, run_agent_call

TASKS = {}
TASK_LOCK = threading.Lock()
SCHEDULED_TASK_SERVICE = None

# 任务记录回收：终态任务保留 N 分钟后删除；TASKS 总量不超过上限。
TASK_TTL_SECONDS = int(os.environ.get("RISK_REVIEW_TASK_TTL", "1800"))
TASK_MAX_COUNT = int(os.environ.get("RISK_REVIEW_TASK_MAX", "400"))


def task_output_token(task_id: str) -> str:
    """Return a stable file token unique to the complete task id.

    Regression and batch child tasks share their parent UUID prefix, so using
    only task_id[:8] makes different cases overwrite one another.
    """
    value = str(task_id or "task")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    readable_prefix = re.sub(r"[^a-zA-Z0-9]", "", value)[:8] or "task"
    return f"{readable_prefix}-{digest}"


def _is_terminal_status(status: str | None) -> bool:
    """Return whether a task has reached a non-resumable terminal state."""
    return status in ("done", "error", "cancelled")


def gc_tasks(force: bool = False) -> int:
    """回收终态且超过 TTL 的任务记录；超容量时按最早创建顺序清理。返回清理数量。"""
    now = time.time()
    removed = 0
    with TASK_LOCK:
        if force or len(TASKS) > TASK_MAX_COUNT:
            overflow = max(0, len(TASKS) - TASK_MAX_COUNT + 1)
            ordered = sorted(TASKS.items(), key=lambda kv: kv[1].get("created", 0))
            for tid, task in ordered:
                if overflow <= 0:
                    break
                if _is_terminal_status(task.get("status")):
                    TASKS.pop(tid, None)
                    removed += 1
                    overflow -= 1
        for tid, task in list(TASKS.items()):
            if not _is_terminal_status(task.get("status")):
                continue
            updated = task.get("updated") or task.get("created") or 0
            if now - updated > TASK_TTL_SECONDS:
                TASKS.pop(tid, None)
                removed += 1
    return removed


def sync_terminal_tasks_to_review_records() -> None:
    """Persist newly terminal in-memory tasks without duplicating existing records."""
    with TASK_LOCK:
        snapshots = [dict(task) for task in TASKS.values()]
    generated = [
        record
        for task in snapshots
        if (record := _task_to_review_record(task)) is not None
    ]
    generated.extend(_external_review_records())
    if not generated:
        return
    with REVIEW_RECORDS_LOCK:
        records = parse_review_records()
        by_id = {str(item.get("recordId") or ""): index for index, item in enumerate(records)}
        changed = False
        for record in generated:
            record_id = str(record["recordId"])
            if record_id in by_id:
                existing = records[by_id[record_id]]
                if existing.get("deleted"):
                    continue
                record["archived"] = bool(existing.get("archived"))
                if existing != record:
                    records[by_id[record_id]] = record
                    changed = True
            else:
                by_id[record_id] = len(records)
                records.append(record)
                changed = True
        if changed:
            save_review_records(records)


def review_history_records(include_archived: bool = False) -> list[dict]:
    """Merge live tasks, persisted records, and orphan artifacts into one history view."""
    sync_terminal_tasks_to_review_records()
    with TASK_LOCK:
        active_tasks = [
            dict(task)
            for task in TASKS.values()
            if not _is_terminal_status(str(task.get("status") or ""))
        ]
    active_records = [
        record
        for task in active_tasks
        if (record := _task_to_review_record(task, include_active=True)) is not None
    ]
    with REVIEW_RECORDS_LOCK:
        stored_records = parse_review_records()
    deleted_ids = {
        str(record.get("recordId") or "")
        for record in stored_records
        if record.get("deleted")
    }
    persisted = [
        record
        for record in stored_records
        if not record.get("deleted")
    ]
    referenced_files = {
        file_name
        for record in persisted
        for file_name in _review_record_artifact_names(record)
    }
    historical = [
        record
        for record in artifact_review_history_records()
        if record.get("recordId") not in deleted_ids
        and not (_review_record_artifact_names(record) & referenced_files)
    ]
    persisted_ids = {str(record.get("recordId") or "") for record in persisted}
    live = [
        record
        for record in active_records
        if str(record.get("recordId") or "") not in deleted_ids
        and str(record.get("recordId") or "") not in persisted_ids
    ]
    records = [normalize_task_record_view(record) for record in [*live, *persisted, *historical]]
    if not include_archived:
        records = [record for record in records if not record.get("archived")]
    return sorted(
        records,
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )


def normalize_task_record_view(record: dict) -> dict:
    """Expose one stable task shape in both task-center and history APIs."""
    source = str(record.get("source") or "single")
    task_type = str(record.get("taskType") or {
        "single": "phase2",
        "batch": "batch",
        "scheduled": "scheduled",
        "validation": "regression",
        "experiment": "review-mode-experiment",
    }.get(source, source))
    task_id = str(record.get("taskId") or record.get("id") or record.get("recordId") or "")
    status = str(record.get("status") or record.get("taskStatus") or "done")
    progress = record.get("progress") or record.get("summary") or {
        "total": 1,
        "completed": int(status == "done"),
        "failed": int(status == "error"),
    }
    context = dict(record.get("context") or {})
    context.setdefault("taskName", record.get("title") or record.get("factorName") or "评审任务")
    context.setdefault("factorName", record.get("factorName") or record.get("title") or "")
    items = record.get("items")
    normalized_items = (
        [normalize_review_item(item) for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else items
    )
    return {
        **record,
        "id": task_id,
        "taskId": task_id,
        "taskType": task_type,
        "parentTaskId": record.get("parentTaskId"),
        "status": status,
        "taskStatus": status,
        "context": context,
        "progress": progress,
        "summary": progress,
        "items": normalized_items,
    }


def _timestamp_from_iso(value: object) -> float:
    """Convert an ISO timestamp to epoch seconds for stable cross-source sorting."""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def review_record_to_task(record: dict) -> dict:
    """Convert a persisted review record to the canonical task API shape."""
    normalized = normalize_task_record_view(record)
    standard_artifacts = normalized.get("standardArtifacts") or {}
    review_artifacts = normalized.get("reviewArtifacts") or {}
    result = {
        "items": normalized.get("items") or [],
        **({"standardFileId": standard_artifacts.get("md")} if standard_artifacts.get("md") else {}),
        **({"standardMetadataFileId": standard_artifacts.get("json")} if standard_artifacts.get("json") else {}),
        **({"reviewFileId": review_artifacts.get("md")} if review_artifacts.get("md") else {}),
    }
    return {
        "id": normalized["taskId"],
        "taskId": normalized["taskId"],
        "recordId": normalized.get("recordId"),
        "taskType": normalized.get("taskType"),
        "parentTaskId": normalized.get("parentTaskId"),
        "clientId": normalized.get("clientId") or "",
        "status": normalized.get("status"),
        "context": normalized.get("context") or {},
        "progress": normalized.get("progress") or {},
        "result": result,
        "created": _timestamp_from_iso(normalized.get("createdAt")),
        "updated": _timestamp_from_iso(normalized.get("updatedAt")),
        "createdAt": normalized.get("createdAt"),
        "updatedAt": normalized.get("updatedAt"),
        "source": normalized.get("source"),
        "conclusion": normalized.get("conclusion"),
        "riskCount": normalized.get("riskCount") or 0,
        "persisted": True,
    }


def find_persisted_task(task_id: str) -> dict | None:
    """Find a task that has already been evicted from the in-memory registry."""
    for record in review_history_records(include_archived=True):
        if str(record.get("taskId") or "") == task_id:
            return review_record_to_task(record)
    return None


def query_review_records(query: dict[str, list[str]]) -> dict:
    """Filter and paginate persisted review records for the history page."""
    archived_only = (query.get("archived") or ["false"])[0].lower() == "true"
    records = review_history_records(include_archived=True)
    records = [item for item in records if bool(item.get("archived")) == archived_only]
    keyword = (query.get("keyword") or [""])[0].strip().lower()
    source = (query.get("source") or [""])[0].strip()
    task_status = (query.get("taskStatus") or [""])[0].strip()
    conclusion = (query.get("conclusion") or [""])[0].strip()
    date_from = (query.get("dateFrom") or [""])[0].strip()
    date_to = (query.get("dateTo") or [""])[0].strip()
    if keyword:
        records = [
            item for item in records
            if keyword in " ".join([
                str(item.get("title") or ""),
                str(item.get("factorName") or ""),
                str(item.get("recordId") or ""),
                str(item.get("taskId") or ""),
            ]).lower()
        ]
    if source:
        records = [item for item in records if item.get("source") == source]
    if task_status:
        records = [item for item in records if item.get("taskStatus") == task_status]
    if conclusion:
        records = [item for item in records if item.get("conclusion") == conclusion]
    if date_from:
        records = [
            item for item in records
            if str(item.get("updatedAt") or item.get("createdAt") or "") >= date_from
        ]
    if date_to:
        records = [
            item for item in records
            if str(item.get("updatedAt") or item.get("createdAt") or "") <= date_to
        ]
    try:
        page = max(1, int((query.get("page") or ["1"])[0]))
        page_size_limit = 5000 if (query.get("export") or ["false"])[0].lower() == "true" else 100
        page_size = max(1, min(page_size_limit, int((query.get("pageSize") or ["20"])[0])))
    except ValueError:
        page, page_size = 1, 20
    total = len(records)
    start = (page - 1) * page_size
    return {
        "items": records[start:start + page_size],
        "page": page,
        "pageSize": page_size,
        "total": total,
    }


def archive_review_record(record_id: str, archived: bool) -> dict:
    """Toggle a record's archived state without touching its artifacts."""
    sync_terminal_tasks_to_review_records()
    with REVIEW_RECORDS_LOCK:
        records = parse_review_records()
        for index, record in enumerate(records):
            if str(record.get("recordId") or "") != record_id:
                continue
            records[index] = {**record, "archived": bool(archived), "updatedAt": _iso_time()}
            save_review_records(records)
            return records[index]
    historical = next(
        (item for item in artifact_review_history_records() if item.get("recordId") == record_id),
        None,
    )
    if historical:
        historical["archived"] = bool(archived)
        historical["updatedAt"] = _iso_time()
        with REVIEW_RECORDS_LOCK:
            records = parse_review_records()
            records.append(historical)
            save_review_records(records)
        return historical
    raise FileNotFoundError("评审记录不存在")


def delete_review_history_record(record_id: str) -> dict:
    """Delete a terminal history record and artifacts not referenced elsewhere."""
    record = next(
        (
            item
            for item in review_history_records(include_archived=True)
            if item.get("recordId") == record_id
        ),
        None,
    )
    if not record:
        raise FileNotFoundError("评审历史记录不存在")
    if str(record.get("taskStatus") or "") in {"queued", "running", "cancelling"}:
        raise ValueError("执行中的评审记录不能删除，请先终止任务")
    file_names = _review_record_artifact_names(record)
    other_references = {
        file_name
        for item in review_history_records(include_archived=True)
        if str(item.get("recordId") or "") != record_id
        for file_name in _review_record_artifact_names(item)
    }
    deleted_files = []
    for file_name in sorted(file_names - other_references):
        safe_name = Path(file_name)
        if safe_name.is_absolute() or len(safe_name.parts) != 1 or safe_name.name != file_name:
            raise ValueError(f"非法产物文件名：{file_name}")
        artifact_path = OUTPUTS_DIR / safe_name
        if artifact_path.exists() and artifact_path.is_file():
            artifact_path.unlink()
            deleted_files.append(file_name)
    with REVIEW_RECORDS_LOCK:
        records = [
            item
            for item in parse_review_records()
            if str(item.get("recordId") or "") != record_id
        ]
        records.append({
            "recordId": record_id,
            "deleted": True,
            "deletedAt": _iso_time(),
        })
        save_review_records(records)
    return {"recordId": record_id, "deletedFiles": deleted_files}


def decorate_task(task: dict) -> dict:
    """Add queue and progress metadata to an in-memory task snapshot."""
    snapshot = dict(task)
    task_id = str(snapshot.get("id") or "")
    snapshot.update(GLOBAL_TASK_POOL.group_info(task_id))
    job_info = GLOBAL_TASK_POOL.job_info(task_id)
    if job_info.get("poolStatus") is not None:
        snapshot.update(job_info)
    result = snapshot.get("result") or {}
    items = result.get("items") if isinstance(result, dict) else None
    if isinstance(items, list):
        terminal = {"done", "pass", "fail", "error", "skip", "cancelled"}
        snapshot["progress"] = {
            "total": len(items),
            "completed": sum(1 for item in items if str(item.get("status") or "") in terminal),
            "failed": sum(1 for item in items if str(item.get("status") or "") in {"error", "skip"}),
        }
    else:
        status = str(snapshot.get("status") or "")
        snapshot["progress"] = {
            "total": 1,
            "completed": int(status in {"done", "error", "cancelled"}),
            "failed": int(status == "error"),
        }
    snapshot["taskId"] = task_id
    snapshot["persisted"] = False
    return snapshot


def task_history(*, include_history: bool = False):
    """Return recent live tasks and, optionally, persisted historical tasks."""
    sync_terminal_tasks_to_review_records()
    gc_tasks()
    with TASK_LOCK:
        tasks = [decorate_task(task) for task in TASKS.values()]
    if include_history:
        known_ids = {str(task.get("id") or "") for task in tasks}
        with REVIEW_RECORDS_LOCK:
            persisted_records = [
                normalize_task_record_view(record)
                for record in parse_review_records()
                if not record.get("deleted") and not record.get("archived")
            ]
        tasks.extend(
            review_record_to_task(record)
            for record in persisted_records
            if str(record.get("taskId") or "") not in known_ids
        )
    return sorted(tasks, key=lambda x: x.get("created", 0), reverse=True)[:50]


def delete_task_record(task_id: str) -> dict:
    """Delete a terminal task through its corresponding review-history record."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task and not _is_terminal_status(str(task.get("status") or "")):
            raise ValueError("执行中的任务不能删除，请先终止任务")
    record = next(
        (
            item
            for item in review_history_records(include_archived=True)
            if str(item.get("taskId") or "") == task_id
        ),
        None,
    )
    if not record:
        raise FileNotFoundError("任务记录不存在")
    deleted = delete_review_history_record(str(record.get("recordId") or ""))
    with TASK_LOCK:
        TASKS.pop(task_id, None)
    return {**deleted, "taskId": task_id}


def task_log(task_id: str, message: str):
    """Append a user-facing execution log in memory and to the daily file."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.setdefault("logs", []).append({"time": time.time(), "message": message})
        task["updated"] = time.time()
        trace_id = str(task.get("traceId") or "")
        task_type = str(task.get("taskType") or "task")
    runtime_log(
        task_type,
        message,
        event="task_execution",
        trace_id=trace_id,
        task_id=task_id,
    )


def task_debug_log(
    task_id: str,
    stream: str,
    message: str,
    *,
    memory: bool = True,
    raw_message: bool = False,
):
    """Append model interaction data in memory and to the same daily log file."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        if memory:
            task.setdefault("debugLogs", []).append({
                "time": time.time(),
                "stream": stream,
                "message": str(message or ""),
            })
            task["updated"] = time.time()
        trace_id = str(task.get("traceId") or "")
        task_type = str(task.get("taskType") or "task")
    stream_name = str(stream or "model_io").lower()
    log_kwargs = {
        "level": "ERROR" if stream_name == "stderr" and str(message or "").strip() else "INFO",
        "event": stream_name if stream_name.startswith("agent_") else f"model_{stream_name}",
        "trace_id": trace_id,
        "task_id": task_id,
    }
    if raw_message:
        log_kwargs["raw_message"] = True
    runtime_log(task_type, str(message or ""), **log_kwargs)


def task_cancel_requested(task_id: str) -> bool:
    """Return whether cancellation was requested in the registry or global pool."""
    with TASK_LOCK:
        current_id = task_id
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            task = TASKS.get(current_id) or {}
            if task.get("cancel_requested"):
                return True
            current_id = str(task.get("parentTaskId") or "")
        return False


def mark_task_cancelled(task_id: str, message: str):
    """Move a task to its terminal cancelled state and record the reason."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task["cancel_requested"] = True
        task["status"] = "cancelled"
        task["result"] = task.get("result") or {
            "stdout": "",
            "stderr": message,
            "returncode": 130,
        }
        task["updated"] = time.time()


def request_task_cancel(task_id: str) -> bool:
    """Cancel queued work or signal a running task and its validation records."""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        child_ids = {tid for tid in TASKS if tid == task_id or tid.startswith(f"{task_id}-")}
        changed = True
        while changed:
            changed = False
            for tid, candidate in TASKS.items():
                if tid in child_ids:
                    continue
                if candidate.get("parentTaskId") in child_ids:
                    child_ids.add(tid)
                    changed = True
        if not task and not child_ids:
            return False
        if task:
            task["cancel_requested"] = True
            if task.get("status") in ("queued", "running"):
                task["status"] = "cancelling"
            task["updated"] = time.time()
        for child_id in child_ids:
            TASKS[child_id]["cancel_requested"] = True
            if TASKS[child_id].get("status") in ("queued", "running"):
                TASKS[child_id]["status"] = "cancelling"
                TASKS[child_id]["updated"] = time.time()
    queued_single_cancelled = GLOBAL_TASK_POOL.cancel(task_id)
    cancelled_pool_jobs = GLOBAL_TASK_POOL.cancel_group(task_id)
    pool_group = GLOBAL_TASK_POOL.group_info(task_id)
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task and queued_single_cancelled:
            task["status"] = "cancelled"
            task["result"] = task.get("result") or {
                "stdout": "",
                "stderr": "任务在全局线程池排队期间被取消。",
                "returncode": 130,
            }
    if task and not queued_single_cancelled and int(pool_group.get("poolActive") or 0) == 0:
        with TASK_LOCK:
            current = TASKS.get(task_id)
            if current and current.get("status") == "cancelling":
                current["status"] = "cancelled"
                current["result"] = current.get("result") or {
                    "stdout": "",
                    "stderr": "任务已终止；当前没有仍在执行的线程池作业。",
                    "returncode": 130,
                }
                current["updated"] = time.time()
    cancel_validation_runs_for_task(task_id)
    experiment_id = str((task or {}).get("experimentId") or "")
    if experiment_id:
        upsert_review_mode_experiment({
            "experimentId": experiment_id,
            "status": "cancelled" if queued_single_cancelled else "cancelling",
            **({
                "finishedAt": _iso_time(),
                "error": "实验在全局线程池排队期间被取消",
            } if queued_single_cancelled else {}),
        })
    if task:
        task_log(
            task_id,
            f"已请求停止任务：取消 {len(cancelled_pool_jobs) + int(queued_single_cancelled)} 个排队作业；运行中的 Agent 请求返回后停止后续步骤",
        )
    return True


def model_runtime_health() -> dict:
    """Report the in-process Agent runtime used by all model tasks."""
    agent = agent_health()
    agent_available = bool(
        agent.get("langchain_version")
        and agent.get("model")
        and agent.get("api_key_configured")
    )
    return {
        "available": agent_available,
        "agent": {**agent, "available": agent_available},
    }


def file_health(path: Path) -> dict:
    """Return existence and basic metadata for a required project file."""
    try:
        display_path = path.relative_to(ROOT).as_posix()
    except ValueError:
        display_path = str(path)
    return {
        "path": display_path,
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
    }


def outputs_health() -> dict:
    """Check output-directory readability, writability, and artifact count."""
    try:
        display_path = OUTPUTS_DIR.relative_to(ROOT).as_posix()
    except ValueError:
        display_path = str(OUTPUTS_DIR)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    probe = OUTPUTS_DIR / f".health-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"path": display_path, "exists": True, "writable": True, "note": ""}
    except Exception as exc:
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        return {"path": display_path, "exists": OUTPUTS_DIR.exists(), "writable": False, "note": str(exc)}


def api_docs_health() -> dict:
    """Summarize whether configured API documents can be discovered."""
    try:
        docs = list_api_docs()
    except Exception as exc:
        return {"count": 0, "by_category": {}, "note": str(exc)}
    by_category = {}
    for doc in docs:
        category = doc.get("category") or "其他"
        by_category[category] = by_category.get(category, 0) + 1
    return {"count": len(docs), "by_category": by_category, "note": ""}


def system_health() -> dict:
    """Build the aggregate backend self-check response used by the UI."""
    runtime_settings = get_runtime_settings()
    required = [
        PHASE1_SKILL_PATH,
        PHASE2_SKILL_PATH,
        PHASE1_PROCESSING_SPEC_PATH,
        PHASE2_REVIEW_SPEC_PATH,
        REQUIREMENT_REVIEW_SKILL_PATH,
        REQUIREMENT_REVIEW_SPEC_PATH,
        RULE_ENGINE_BUILD_PATH,
        STATIC_DIR / "index.html",
        STATIC_DIR / "app.js",
        STATIC_DIR / "style.css",
        STATIC_DIR / "dist" / "index.html",
        EXAMPLES_PATH,
        BUSINESS_RULES_PATH,
    ]
    files = {path.relative_to(ROOT).as_posix(): file_health(path) for path in required}
    model_runtime = model_runtime_health()
    checks = {
        "required_files": all(item["exists"] for item in files.values()),
        "outputs_writable": outputs_health()["writable"],
        "model_available": bool(model_runtime.get("available"))
        or os.environ.get("RISK_REVIEW_MOCK") == "1",
        "api_docs_loaded": api_docs_health()["count"] > 0,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "working_dir": str(ROOT),
        "mock_mode": os.environ.get("RISK_REVIEW_MOCK") == "1",
        "modelRuntime": model_runtime,
        "files": files,
        "outputs": outputs_health(),
        "api_docs": api_docs_health(),
        "task_pool": GLOBAL_TASK_POOL.snapshot(),
        "runtime_settings": runtime_settings,
    }


def test_model_runtime():
    """Test the in-process Agent without creating business artifacts."""
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="risk-review-agent-test-") as workspace:
        status, result = run_agent_call(
            prompt="只回复 OK",
            phase_label="Model Test",
            workspace=Path(workspace),
            cancelled=lambda: False,
        )
    return {
        "ok": status == "done" and str(result.get("content") or "").strip() == "OK",
        "seconds": round(time.time() - started, 2),
        **result,
    }
def build_prompt_phase1(payload: dict) -> ModelPrompt:
    """Build prompt for Phase 1 only (standard spec, no draft)."""
    requirement = (payload.get("requirement") or "").strip()
    api_names = payload.get("apiNames") or []
    api_names = [str(x).strip() for x in api_names if str(x).strip()]
    return build_phase1_prompt(
        requirement=requirement,
        api_names=api_names,
    )


def build_prompt_phase2(payload: dict) -> ModelPrompt:
    """Build prompt for Phase 2 only (review against existing standard)."""
    draft = (payload.get("draft") or "").strip()
    phase2_context = payload.get("_phase2_context") or prepare_phase2_context(payload)
    return build_phase2_prompt(
        standard_text=phase2_context["standardText"],
        requirement=(payload.get("requirement") or "").strip(),
        draft=draft,
    )


def build_prompt_requirement_review(payload: dict) -> str:
    """Build the standalone requirement-review prompt."""
    return build_requirement_review_prompt(
        requirement=str(payload.get("requirement") or "").strip(),
        api_names=[str(item) for item in payload.get("apiNames") or []],
    )


def create_phase1_isolated_workspace(task_id: str, payload: dict) -> Path:
    """Create a Phase 1 workspace containing only files allowed by the stage boundary."""
    workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase1-{task_id[:8]}-"))
    try:
        allowed_sources = [PHASE1_SKILL_PATH, PHASE1_PROCESSING_SPEC_PATH, BUSINESS_RULES_PATH]
        for identifier in payload.get("apiNames") or []:
            doc = get_api_doc(str(identifier))
            if doc is None:
                continue
            source_path = (ROOT / str(doc.get("path") or "")).resolve()
            try:
                source_path.relative_to(ROOT.resolve())
            except ValueError as exc:
                raise ValueError(f"接口文档路径越界：{identifier}") from exc
            allowed_sources.append(source_path)

        staged_files = []
        for source_path in allowed_sources:
            source_path = source_path.resolve()
            if not source_path.is_file():
                continue
            relative_path = source_path.relative_to(ROOT.resolve())
            target_path = workspace / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            staged_files.append(relative_path.as_posix())

        task_log(
            task_id,
            "Phase 1 隔离工作目录已创建，仅包含：" + "、".join(staged_files),
        )
        return workspace
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def create_phase2_isolated_workspace(task_id: str) -> Path:
    """Create a Phase 2 workspace containing only its Skill and review specification."""
    workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase2-{task_id[:8]}-"))
    try:
        staged_files = []
        for source_path in (PHASE2_SKILL_PATH, PHASE2_REVIEW_SPEC_PATH):
            source_path = source_path.resolve()
            if not source_path.is_file():
                continue
            relative_path = source_path.relative_to(ROOT.resolve())
            target_path = workspace / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            staged_files.append(relative_path.as_posix())
        task_log(
            task_id,
            "Phase 2 隔离工作目录已创建，仅包含：" + "、".join(staged_files),
        )
        return workspace
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def run_agent_prompt(
    task_id: str,
    phase_label: str,
    prompt: str | ModelPrompt,
    *,
    isolated_workspace: Path | None = None,
) -> tuple[str, dict]:
    """Execute the LangChain Agent in the current global-pool worker thread."""
    if isolated_workspace is None:
        return "error", {
            "stdout": "",
            "stderr": "Agent 调用必须提供隔离工作目录。",
            "returncode": 1,
        }
    workspace = isolated_workspace.resolve()
    if isinstance(prompt, ModelPrompt):
        system_prompt = prompt.system
        user_prompt = prompt.user
    else:
        system_prompt = ""
        user_prompt = str(prompt)
    with TASK_LOCK:
        debug_enabled = bool((TASKS.get(task_id) or {}).get("debug_agent"))
    task_log(task_id, f"调用 LangChain Agent（{phase_label}）")
    task_log(
        task_id,
        f"Prompt 字符数：system={len(system_prompt)}，user={len(user_prompt)}",
    )
    if system_prompt:
        task_debug_log(task_id, "system_prompt", system_prompt, memory=debug_enabled)
    task_debug_log(task_id, "user_prompt", user_prompt, memory=debug_enabled)

    def agent_event(event: str, payload: object) -> None:
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
        if event in {"model_input", "model_output"}:
            rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            task_debug_log(
                task_id,
                f"agent_{event}",
                rendered,
                memory=debug_enabled,
                raw_message=True,
            )
            return
        task_debug_log(task_id, f"agent_{event}", rendered, memory=debug_enabled)

    status, result = run_agent_call(
        prompt=user_prompt,
        system_prompt=system_prompt,
        phase_label=phase_label,
        workspace=workspace,
        cancelled=lambda: task_cancel_requested(task_id),
        event_callback=agent_event,
    )
    task_debug_log(task_id, "response", str(result.get("content") or ""), memory=debug_enabled)
    task_debug_log(task_id, "stderr", str(result.get("stderr") or ""), memory=debug_enabled)
    task_log(task_id, f"LangChain Agent 返回，状态：{status}")
    return status, result


def run_model_prompt(
    task_id: str,
    phase_label: str,
    prompt: str | ModelPrompt,
    *,
    isolated_workspace: Path | None = None,
) -> tuple[str, dict]:
    """Run one prompt through the platform's sole in-process Agent backend."""
    return run_agent_prompt(
        task_id,
        phase_label,
        prompt,
        isolated_workspace=isolated_workspace,
    )


def prepare_phase2_context(payload: dict) -> dict:
    """Load and sanitize the frozen Phase 1 standard required by Phase 2."""
    standard_text, std_path = read_phase1_standard(payload)
    requirement = (payload.get("requirement") or "").strip()
    if not requirement:
        raise ValueError("Phase 2 缺少原始需求，无法提供原文依据。")
    model_standard_text = re.sub(
        r"^# 标准加工规范[^\n]*",
        "# 标准加工规范",
        strip_yaml_frontmatter(standard_text),
        count=1,
    )
    return {
        "standardText": model_standard_text,
        "standardPath": std_path,
        "standardFileId": std_path.name,
    }


def model_response_content(result: dict) -> str:
    """Read normalized model content without conflating Agent responses with stdout."""
    if "content" in result:
        return str(result.get("content") or "")
    return str(result.get("stdout") or "")


def run_generation(
    *,
    task_id: str,
    phase_label: str,
    empty_error: str,
    prompt: str | ModelPrompt,
    isolated_workspace: Path | None = None,
) -> tuple[str, dict, str]:
    """Run one generation attempt and enforce non-empty output and cancellation checks."""
    raw_output = ""
    if task_cancel_requested(task_id):
        return "cancelled", {"stdout": "", "stderr": f"{phase_label} 已取消。", "returncode": 130}, raw_output

    task_log(task_id, f"{phase_label} 开始生成")
    status, result = run_model_prompt(
        task_id,
        phase_label,
        prompt,
        isolated_workspace=isolated_workspace,
    )
    if status != "done":
        return status, result, raw_output

    raw_output = model_response_content(result).strip()
    if not raw_output:
        result.update({"stderr": empty_error, "returncode": 1})
        return "error", result, raw_output
    if task_cancel_requested(task_id):
        return "cancelled", {
            "stdout": raw_output,
            "stderr": f"{phase_label} 已取消。",
            "returncode": 130,
        }, raw_output
    return status, result, raw_output


def run_agent_phase1(payload: dict, task_id: str):
    """Run Phase 1: generate standard spec only."""
    if task_cancel_requested(task_id):
        mark_task_cancelled(task_id, "Phase 1 启动前已取消。")
        return
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
    task_log(task_id, "Phase 1 开始")
    checksum = compute_phase1_checksum(payload)
    use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True
    if not payload.get("forceRefresh"):
        cached = find_cached_phase1_standard(checksum, allow_mock=use_mock)
        if cached:
            if task_cancel_requested(task_id):
                mark_task_cancelled(task_id, "Phase 1 已取消。")
                return
            current_factor_name = (payload.get("factorName") or "factor").strip()
            cached_text = str(cached.get("stdout") or "")
            cached_meta = parse_frontmatter(cached_text)
            if str(cached_meta.get("factor_name") or "") != current_factor_name:
                slug = re.sub(r"[^a-zA-Z0-9]", "-", current_factor_name.lower()).strip("-") or "factor"
                output_name = f"standard-{slug}-{time.strftime('%Y-%m-%d')}-{task_output_token(task_id)}.md"
                cached_text = normalize_phase1_output(
                    cached_text,
                    payload,
                    slug,
                    current_factor_name,
                    checksum,
                    mock=use_mock,
                )
                OUTPUTS_DIR.mkdir(exist_ok=True)
                (OUTPUTS_DIR / output_name).write_text(cached_text, encoding="utf-8")
                metadata_name = save_standard_metadata(output_name, cached_text, payload)
                cached = {
                    **cached,
                    "standardFileId": output_name,
                    "standardMetadataFileId": metadata_name,
                    "stdout": cached_text,
                }
            task_log(task_id, f"Phase 1 命中缓存：{cached['standardFileId']}")
            with TASK_LOCK:
                TASKS[task_id]["status"] = "done"
                TASKS[task_id]["result"] = {
                    "returncode": 0,
                "stdout": cached["stdout"],
                "standardFileId": cached["standardFileId"],
                "standardMetadataFileId": cached.get("standardMetadataFileId"),
                "checksum": checksum,
                "stderr": "",
                "cacheHit": True,
                }
                TASKS[task_id]["outputs"] = list_outputs()
                TASKS[task_id]["updated"] = time.time()
            return
    if use_mock:
        task_log(task_id, "使用 Mock 模式（Phase 1）")
        time.sleep(0.3)
        if task_cancel_requested(task_id):
            mark_task_cancelled(task_id, "Phase 1 Mock 已取消。")
            return
        factor_name = (payload.get("factorName") or "mockFactor").strip()
        slug = re.sub(r'[^a-zA-Z0-9]', '-', factor_name.lower()).strip('-') or 'factor'
        output_name = f"standard-{slug}-{time.strftime('%Y-%m-%d')}-{task_output_token(task_id)}.md"
        mock_body = """# 标准加工规范

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
        output_text = normalize_phase1_output(
            mock_body,
            payload,
            slug,
            factor_name,
            checksum,
            mock=True,
        )
        OUTPUTS_DIR.mkdir(exist_ok=True)
        (OUTPUTS_DIR / output_name).write_text(output_text, encoding="utf-8")
        try:
            metadata_name = save_standard_metadata(output_name, output_text, payload)
        except Exception as exc:
            with TASK_LOCK:
                TASKS[task_id]["status"] = "error"
                TASKS[task_id]["result"] = {
                    "returncode": 1,
                    "stdout": output_text,
                    "stderr": str(exc),
                }
                TASKS[task_id]["outputs"] = list_outputs()
                TASKS[task_id]["updated"] = time.time()
            task_log(task_id, f"Phase 1 Mock 输出校验失败：{exc}")
            return
        output_text = read_text(OUTPUTS_DIR / output_name)
        with TASK_LOCK:
            TASKS[task_id]["status"] = "done"
            TASKS[task_id]["result"] = {
                "returncode": 0,
                "stdout": output_text,
                "standardFileId": output_name,
                "standardMetadataFileId": metadata_name,
                "checksum": checksum,
                "stderr": "",
                "cacheHit": False,
            }
            TASKS[task_id]["outputs"] = list_outputs()
            TASKS[task_id]["updated"] = time.time()
        task_log(task_id, f"Phase 1 Mock 完成：{output_name}")
        return

    isolated_workspace = create_phase1_isolated_workspace(task_id, payload)
    try:
        status, result, raw_output = run_generation(
            task_id=task_id,
            phase_label="Phase 1",
            empty_error="模型未输出 Phase 1 标准加工规范正文",
            prompt=build_prompt_phase1(payload),
            isolated_workspace=isolated_workspace,
        )
    finally:
        shutil.rmtree(isolated_workspace, ignore_errors=True)
        task_log(task_id, "Phase 1 隔离工作目录已清理")

    if status == "done":
        if not raw_output:
            status = "error"
            result.update({"stderr": "模型未输出 Phase 1 标准加工规范正文", "returncode": 1})
        else:
            factor_name = (payload.get("factorName") or "factor").strip()
            slug = re.sub(r'[^a-zA-Z0-9]', '-', factor_name.lower()).strip('-') or 'factor'
            output_name = f"standard-{slug}-{time.strftime('%Y-%m-%d')}-{task_output_token(task_id)}.md"
            output_text = normalize_phase1_output(
                raw_output,
                payload,
                slug,
                factor_name,
                checksum,
                mock=False,
            )
            try:
                validate_phase1_standard_basic_output(output_text)
                OUTPUTS_DIR.mkdir(exist_ok=True)
                (OUTPUTS_DIR / output_name).write_text(output_text, encoding="utf-8")
                metadata_name = save_standard_metadata(output_name, output_text, payload)
                output_text = read_text(OUTPUTS_DIR / output_name)
                result.update({
                    "returncode": 0,
                    "stdout": output_text,
                    "standardFileId": output_name,
                    "standardMetadataFileId": metadata_name,
                    "checksum": checksum,
                    "stderr": "",
                    "cacheHit": False,
                })
                task_log(task_id, f"Phase 1 文件已保存：outputs/{output_name}")
            except Exception as exc:
                status = "error"
                result.update({
                    "returncode": 1,
                    "stdout": raw_output,
                    "stderr": str(exc),
                })
                task_log(task_id, f"Phase 1 输出校验失败：{exc}")
    with TASK_LOCK:
        if status == "error":
            attach_error_metadata(result, "Phase 1 执行失败")
        TASKS[task_id]["status"] = status
        TASKS[task_id]["result"] = result
        TASKS[task_id]["outputs"] = list_outputs()
        TASKS[task_id]["updated"] = time.time()


def run_agent_requirement_review(payload: dict, task_id: str):
    """Run one isolated, single-stage requirement review."""
    if task_cancel_requested(task_id):
        mark_task_cancelled(task_id, "需求评审启动前已取消。")
        return
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
    task_log(task_id, "需求评审开始")

    use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True
    status = "done"
    result: dict = {"returncode": 0, "stdout": "", "stderr": ""}
    raw_output = ""
    documents: list[dict] = []
    isolated_workspace: Path | None = None
    try:
        if use_mock:
            task_log(task_id, "使用 Mock 模式（需求评审）")
            time.sleep(0.3)
            if task_cancel_requested(task_id):
                mark_task_cancelled(task_id, "需求评审 Mock 已取消。")
                return
            documents = selected_api_documents(payload.get("apiNames") or [])
            raw_output = mock_requirement_review_markdown(payload.get("apiNames") or [])
        else:
            isolated_workspace, documents = create_requirement_review_workspace(
                task_id,
                payload.get("apiNames") or [],
                log=lambda message: task_log(task_id, message),
            )
            status, result, raw_output = run_generation(
                task_id=task_id,
                phase_label="需求评审",
                empty_error="模型未输出需求评审报告正文",
                prompt=build_prompt_requirement_review(payload),
                isolated_workspace=isolated_workspace,
            )
            if status not in {"done", "cancelled"}:
                status = "error"
    except Exception as exc:
        status = "error"
        result = {"returncode": 1, "stdout": raw_output, "stderr": str(exc)}
        task_log(task_id, f"需求评审执行失败：{exc}")
    finally:
        if isolated_workspace is not None:
            shutil.rmtree(isolated_workspace, ignore_errors=True)
            task_log(task_id, "需求评审隔离工作目录已清理")

    if status == "done":
        try:
            report = normalize_requirement_review_output(raw_output)
            validate_requirement_review_basic_output(report)
            artifact_ids = save_requirement_review_artifacts(
                task_id=task_id,
                review_text=report,
                requirement=payload.get("requirement") or "",
                api_documents=documents,
                mock=use_mock,
            )
            result.update({
                "returncode": 0,
                "stdout": report,
                "stderr": "",
                **artifact_ids,
            })
            task_log(task_id, f"需求评审报告已保存：outputs/{artifact_ids['reviewFileId']}")
            task_log(task_id, "需求评审完成")
        except Exception as exc:
            status = "error"
            result.update({"returncode": 1, "stdout": raw_output, "stderr": str(exc)})
            task_log(task_id, f"需求评审输出校验失败：{exc}")

    with TASK_LOCK:
        if status == "error":
            attach_error_metadata(result, "需求评审执行失败")
        TASKS[task_id]["status"] = status
        TASKS[task_id]["result"] = result
        TASKS[task_id]["updated"] = time.time()


def run_agent_phase2(payload: dict, task_id: str):
    """Run Phase 2: generate review report using existing standard."""
    if task_cancel_requested(task_id):
        mark_task_cancelled(task_id, "Phase 2 启动前已取消。")
        return
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
    task_log(task_id, "Phase 2 开始")
    
    try:
        phase2_context = prepare_phase2_context(payload)
        payload = {**payload, "_phase2_context": phase2_context}
    except (FileNotFoundError, ValueError) as e:
        task_log(task_id, str(e))
        with TASK_LOCK:
            TASKS[task_id]["status"] = "error"
            TASKS[task_id]["result"] = attach_error_metadata(
                {"stdout": "", "stderr": str(e), "returncode": 1},
                "Phase 2 输入校验失败",
            )
            TASKS[task_id]["outputs"] = list_outputs()
            TASKS[task_id]["updated"] = time.time()
        return
    
    use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or payload.get("mock") is True
    
    if use_mock:
        task_log(task_id, "使用 Mock 模式（Phase 2）")
        time.sleep(0.3)
        if task_cancel_requested(task_id):
            mark_task_cancelled(task_id, "Phase 2 Mock 已取消。")
            return
        factor_name = (payload.get("factorName") or "mockFactor").strip()
        slug = re.sub(r'[^a-zA-Z0-9]', '-', factor_name.lower()).strip('-') or 'factor'
        output_name = f"review-{slug}-{time.strftime('%Y-%m-%d')}-{task_output_token(task_id)}.md"
        output_text = f"""# 评审报告 — {factor_name}

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
        OUTPUTS_DIR.mkdir(exist_ok=True)
        (OUTPUTS_DIR / output_name).write_text(output_text, encoding="utf-8")
        with TASK_LOCK:
            TASKS[task_id]["status"] = "done"
            TASKS[task_id]["result"] = {
                "returncode": 0,
                "stdout": output_text,
                "stderr": "",
                "reviewFileId": output_name,
            }
            TASKS[task_id]["outputs"] = list_outputs()
            TASKS[task_id]["updated"] = time.time()
        task_log(task_id, f"Phase 2 Mock 完成：{output_name}")
        return
    
    standard_file_id = phase2_context["standardFileId"]
    isolated_workspace = create_phase2_isolated_workspace(task_id)
    try:
        status, result, raw_output = run_generation(
            task_id=task_id,
            phase_label="Phase 2",
            empty_error="模型未输出 Phase 2 评审报告正文",
            prompt=build_prompt_phase2(payload),
            isolated_workspace=isolated_workspace,
        )
    finally:
        shutil.rmtree(isolated_workspace, ignore_errors=True)
        task_log(task_id, "Phase 2 隔离工作目录已清理")

    if status == "done":
        if not raw_output:
            status = "error"
            result.update({"stderr": "模型未输出 Phase 2 评审报告正文", "returncode": 1})
        else:
            try:
                validate_phase2_review_basic_output(raw_output)
            except ValueError as exc:
                status = "error"
                result.update({"stderr": str(exc), "returncode": 1})
                raw_output = ""
        if status == "done":
            factor_name = (payload.get("factorName") or "factor").strip()
            slug = re.sub(r'[^a-zA-Z0-9]', '-', factor_name.lower()).strip('-') or 'factor'
            review_checksum = compute_review_checksum(payload, phase2_context["standardText"])
            review_file_id = f"review-{slug}-{time.strftime('%Y-%m-%d')}-{task_output_token(task_id)}.md"
            saved_report = normalize_review_output(raw_output, payload, slug, standard_file_id, review_checksum)
            OUTPUTS_DIR.mkdir(exist_ok=True)
            (OUTPUTS_DIR / review_file_id).write_text(saved_report, encoding="utf-8")
            result["stdout"] = saved_report
            result["reviewFileId"] = review_file_id
            result["reviewChecksum"] = review_checksum
            task_log(task_id, f"Phase 2 文件已保存：outputs/{review_file_id}")
    with TASK_LOCK:
        if status == "error":
            attach_error_metadata(result, "Phase 2 执行失败")
        TASKS[task_id]["status"] = status
        TASKS[task_id]["result"] = result
        TASKS[task_id]["outputs"] = list_outputs()
        TASKS[task_id]["updated"] = time.time()

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


def validation_run_for_example(
    task_id: str,
    example: dict,
    *,
    run_id: str | None = None,
    status: str = "pending",
) -> dict:
    """Create the initial persisted run record for one validation case."""
    return {
        "groupId": task_id,
        "taskId": task_id,
        "runId": run_id or f"validation-{task_output_token(task_id)}-{uuid.uuid4().hex[:8]}",
        "caseId": str(example.get("id") or ""),
        "caseName": str(example.get("title") or example.get("id") or ""),
        "status": status,
        "expectedConclusion": validation_expected_code(example.get("expected")),
        "expectedIssuePatterns": _string_list(example.get("expectedIssuePatterns")),
        "forbiddenIssuePatterns": _string_list(example.get("forbiddenIssuePatterns")),
        "maxIssueCount": _optional_nonnegative_int(example.get("maxIssueCount")),
        "actualConclusion": None,
        "conclusionMatched": None,
        "issuesMatched": None,
        "matched": None,
        "standardFile": None,
        "standardMetadataFile": None,
        "reviewFile": None,
        "startedAt": _iso_time(),
        "finishedAt": None,
        "error": None,
    }


def prepare_validation_runs(task_id: str, examples: list[dict]) -> dict[str, str]:
    """Pre-create run records and index their IDs by validation case."""
    run_ids = {}
    for example in examples:
        run = validation_run_for_example(task_id, example)
        upsert_validation_run(run)
        run_ids[str(example.get("id") or "")] = run["runId"]
    return run_ids


def run_regression_validation(payload: dict, task_id: str):
    """Execute selected validation cases sequentially inside one global-pool worker."""
    requested_ids = set(str(x) for x in (payload.get("exampleIds") or []) if str(x).strip())
    examples = parse_examples()
    if requested_ids:
        examples = [item for item in examples if item.get("id") in requested_ids]
    if task_cancel_requested(task_id):
        mark_task_cancelled(task_id, "回归验证启动前已取消。")
        cancel_validation_runs_for_task(task_id)
        return
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
        TASKS[task_id]["result"] = {"items": [], "summary": {}, "stdout": "", "stderr": "", "returncode": 0}
    task_log(task_id, f"回归验证开始：{len(examples)} 条")
    items = []
    for index, example in enumerate(examples):
        if task_cancel_requested(task_id):
            task_log(task_id, "回归验证已停止：跳过剩余用例")
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
        run = validation_run_for_example(task_id, example, run_id=run_id or None, status="running")
        run_id = run["runId"]
        item["runId"] = run_id
        upsert_validation_run(run)
        items.append(item)
        with TASK_LOCK:
            TASKS[task_id]["result"] = {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "stdout": "", "stderr": "", "returncode": 0}
            TASKS[task_id]["updated"] = time.time()
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
                item["standardText"] = phase1_result.get("stdout") or ""
                item["cacheHit"] = bool(phase1_result.get("cacheHit"))
                upsert_validation_run({
                    "runId": run_id,
                    "status": "running",
                    "standardFile": phase1_result.get("standardFileId"),
                    "standardMetadataFile": phase1_result.get("standardMetadataFileId"),
                })
                with TASK_LOCK:
                    TASKS[task_id]["result"] = {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "stdout": "", "stderr": "", "returncode": 0}
                    TASKS[task_id]["outputs"] = list_outputs()
                    TASKS[task_id]["updated"] = time.time()

            pipeline = run_review_pipeline(
                task_id,
                index,
                phase1_payload,
                draft,
                mock=payload.get("mock"),
                should_stop=lambda: task_cancel_requested(task_id),
                on_phase1_done=on_phase1_done,
            )
            if pipeline.get("stopped"):
                item.update({"status": "skip", "raw": "已请求停止，Phase 1 完成后未继续执行 Phase 2", "time": int(time.time() * 1000)})
                upsert_validation_run({
                    "runId": run_id,
                    "status": "cancelled",
                    "finishedAt": _iso_time(),
                    "error": "验证任务已终止",
                })
                task_log(task_id, f"停止：{example.get('title') or example.get('id')} Phase 1 后停止")
                break
            report = pipeline.get("raw") or ""
            review_file_id = pipeline.get("reviewFileId")
            verdict = detect_verdict_text(report)
            conclusion_matched = expected_matches_verdict(example, verdict)
            issue_evaluation = evaluate_issue_expectations(example, report)
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
                "summary": collect_review_stats(report),
                "raw": report,
                "reviewFileId": review_file_id,
                "reportPreview": report[:600],
                "time": int(time.time() * 1000),
            })
            upsert_validation_run({
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
                "finishedAt": _iso_time(),
                "error": None,
            })
            task_log(task_id, f"验证完成：{example.get('title') or example.get('id')}（{'通过' if expected_ok else '未通过'}）")
        except Exception as exc:
            if task_cancel_requested(task_id):
                item.update({"status": "skip", "raw": "已停止：当前模型调用已中止", "reportPreview": "已停止：当前模型调用已中止", "time": int(time.time() * 1000)})
                upsert_validation_run({
                    "runId": run_id,
                    "status": "cancelled",
                    "finishedAt": _iso_time(),
                    "error": "验证任务已终止",
                })
                task_log(task_id, f"验证停止：{example.get('title') or example.get('id')}")
            else:
                item.update({"status": "error", "raw": str(exc), "reportPreview": str(exc), "time": int(time.time() * 1000)})
                item.update(classify_task_error(str(exc)))
                upsert_validation_run({
                    "runId": run_id,
                    "status": "failed",
                    "finishedAt": _iso_time(),
                    "error": str(exc),
                })
                task_log(task_id, f"验证失败：{example.get('title') or example.get('id')}：{exc}")
        with TASK_LOCK:
            TASKS[task_id]["result"] = {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "stdout": "", "stderr": "", "returncode": 0}
            TASKS[task_id]["outputs"] = list_outputs()
            TASKS[task_id]["updated"] = time.time()
    final_status = "cancelled" if task_cancel_requested(task_id) else "done"
    processed_case_ids = {str(item.get("id") or "") for item in items}
    for case_id, pending_run_id in (payload.get("_validationRunIds") or {}).items():
        if case_id in processed_case_ids:
            continue
        upsert_validation_run({
            "runId": pending_run_id,
            "status": "cancelled" if final_status == "cancelled" else "failed",
            "finishedAt": _iso_time(),
            "error": "验证任务已终止" if final_status == "cancelled" else "验证案例未执行",
        })
    with TASK_LOCK:
        TASKS[task_id]["status"] = final_status
        TASKS[task_id]["result"] = {"items": items, "summary": summarize_regression_items(items, total=len(examples)), "stdout": "", "stderr": "", "returncode": 0}
        TASKS[task_id]["outputs"] = list_outputs()
        TASKS[task_id]["updated"] = time.time()
    task_log(task_id, "回归验证已停止" if final_status == "cancelled" else "回归验证完成")


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


def experiment_mode_result(example: dict, report: str, *, duration_ms: int, **artifacts) -> dict:
    """Evaluate one experiment-mode report against the case's frozen expectations."""
    verdict = detect_verdict_text(report)
    conclusion_matched = expected_matches_verdict(example, verdict)
    issue_evaluation = evaluate_issue_expectations(example, report)
    issue_configured = bool(issue_evaluation.get("configured"))
    issues_matched = bool(issue_evaluation.get("matched")) if issue_configured else None
    return {
        "status": "success",
        "actualConclusion": verdict,
        "conclusionMatched": conclusion_matched,
        "issuesMatched": issues_matched,
        "matched": conclusion_matched and bool(issue_evaluation.get("matched")),
        "issueEvaluation": issue_evaluation,
        "issueStats": collect_review_stats(report),
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


def mock_direct_review_report(example: dict) -> str:
    """Generate a deterministic direct-review report for Mock experiments."""
    expected = validation_expected_code(example.get("expected"))
    expected_patterns = _string_list(example.get("expectedIssuePatterns"))
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
    mock: bool | None = None,
) -> tuple[str, str]:
    """Run one direct-review experiment item and persist its child-task state."""

    child_id = f"{parent_task_id}-{item_index}-direct"
    create_task_record(
        child_id,
        task_type="comparison-direct",
        parent_task_id=parent_task_id,
    )
    with TASK_LOCK:
        TASKS[child_id]["status"] = "running"
        TASKS[child_id]["updated"] = time.time()
    use_mock = os.environ.get("RISK_REVIEW_MOCK") == "1" or mock is True
    if use_mock:
        time.sleep(0.05)
        status = "cancelled" if task_cancel_requested(child_id) else "done"
        result = {
            "stdout": "" if status == "cancelled" else mock_direct_review_report(example),
            "stderr": "直接评审已取消" if status == "cancelled" else "",
            "returncode": 130 if status == "cancelled" else 0,
        }
    else:
        prompt = build_direct_review_prompt(
            requirement=str(example.get("requirement") or ""),
            draft=str(example.get("draft") or example.get("reviewContent") or ""),
        )
        with tempfile.TemporaryDirectory(prefix="risk-review-direct-") as workspace:
            status, result = run_model_prompt(
                child_id,
                "Direct Review",
                prompt,
                isolated_workspace=Path(workspace),
            )
    with TASK_LOCK:
        TASKS[child_id]["status"] = status
        TASKS[child_id]["result"] = result
        TASKS[child_id]["updated"] = time.time()
    if status != "done":
        raise RuntimeError(result.get("stderr") or "直接评审失败")
    report = model_response_content(result).strip()
    if not report.startswith("# 直接评审报告"):
        raise ValueError("直接评审输出格式错误：必须以“# 直接评审报告”开头")
    safe_case = re.sub(r"[^a-zA-Z0-9_-]", "-", str(example.get("id") or "case")).strip("-") or "case"
    file_id = (
        f"direct-review-{safe_case}-{time.strftime('%Y-%m-%d')}-"
        f"{task_output_token(child_id)}.md"
    )
    saved = "\n".join([
        "---",
        "mode: direct_review_experiment",
        f"case_id: {safe_case}",
        f"generated: {time.strftime('%Y-%m-%d')}",
        "---",
        report,
        "",
    ])
    OUTPUTS_DIR.mkdir(exist_ok=True)
    atomic_write_text(OUTPUTS_DIR / file_id, saved)
    return saved, file_id


def run_review_mode_experiment(payload: dict, task_id: str):
    """Compare direct review with the two-phase pipeline across selected cases."""
    experiment_id = str(payload.get("experimentId") or task_id)
    requested_ids = {
        str(value)
        for value in (payload.get("caseIds") or [])
        if str(value).strip()
    }
    repetitions = max(1, min(3, int(payload.get("repetitions") or 1)))
    with EXAMPLES_LOCK:
        examples = [
            item for item in parse_examples()
            if not requested_ids or str(item.get("id") or "") in requested_ids
        ]
    total = len(examples) * repetitions
    items: list[dict] = []
    started_at = _iso_time()
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
    upsert_review_mode_experiment({
        "experimentId": experiment_id,
        "taskId": task_id,
        "status": "running",
        "startedAt": started_at,
        "items": items,
        "summary": summarize_review_mode_experiment(items, total),
    })
    task_log(task_id, f"评审模式对比实验开始：{len(examples)} 个案例 × {repetitions} 次")
    for round_index in range(repetitions):
        for case_index, example in enumerate(examples):
            if task_cancel_requested(task_id):
                break
            item_index = round_index * len(examples) + case_index
            item = {
                "itemId": f"{example.get('id')}-r{round_index + 1}",
                "caseId": str(example.get("id") or ""),
                "caseName": str(example.get("title") or example.get("id") or ""),
                "round": round_index + 1,
                "expectedConclusion": validation_expected_code(example.get("expected")),
                "issueBaselineConfigured": bool(
                    _string_list(example.get("expectedIssuePatterns"))
                    or _string_list(example.get("forbiddenIssuePatterns"))
                    or _optional_nonnegative_int(example.get("maxIssueCount")) is not None
                ),
                "direct": {"status": "running"},
                "twoStage": {"status": "pending"},
            }
            items.append(item)
            upsert_review_mode_experiment({
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
                    mock=payload.get("mock"),
                )
                item["direct"] = experiment_mode_result(
                    example,
                    report,
                    duration_ms=round((time.monotonic() - direct_started) * 1000),
                    reviewFile=file_id,
                )
            except Exception as exc:
                item["direct"] = {
                    "status": "cancelled" if task_cancel_requested(task_id) else "error",
                    "durationMs": round((time.monotonic() - direct_started) * 1000),
                    "error": str(exc),
                }
            if task_cancel_requested(task_id):
                item["twoStage"] = {"status": "cancelled", "error": "实验已终止"}
                break
            item["twoStage"] = {"status": "running"}
            upsert_review_mode_experiment({
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
                    child_suffix="comparison",
                    mock=payload.get("mock"),
                    should_stop=lambda: task_cancel_requested(task_id),
                )
                if pipeline.get("stopped"):
                    raise RuntimeError(str(pipeline.get("raw") or "两阶段评审已终止"))
                phase1_result = pipeline.get("phase1") or {}
                report = str(pipeline.get("raw") or "")
                item["twoStage"] = experiment_mode_result(
                    example,
                    report,
                    duration_ms=round((time.monotonic() - two_stage_started) * 1000),
                    standardFile=phase1_result.get("standardFileId"),
                    standardMetadataFile=phase1_result.get("standardMetadataFileId"),
                    reviewFile=pipeline.get("reviewFileId"),
                )
            except Exception as exc:
                item["twoStage"] = {
                    "status": "cancelled" if task_cancel_requested(task_id) else "error",
                    "durationMs": round((time.monotonic() - two_stage_started) * 1000),
                    "error": str(exc),
                }
            upsert_review_mode_experiment({
                "experimentId": experiment_id,
                "items": items,
                "summary": summarize_review_mode_experiment(items, total),
            })
            with TASK_LOCK:
                TASKS[task_id]["result"] = {
                    "experimentId": experiment_id,
                    "items": items,
                    "summary": summarize_review_mode_experiment(items, total),
                }
                TASKS[task_id]["outputs"] = list_outputs()
                TASKS[task_id]["updated"] = time.time()
        if task_cancel_requested(task_id):
            break
    final_status = "cancelled" if task_cancel_requested(task_id) else "done"
    summary = summarize_review_mode_experiment(items, total)
    finished_at = _iso_time()
    experiment = upsert_review_mode_experiment({
        "experimentId": experiment_id,
        "status": final_status,
        "finishedAt": finished_at,
        "items": items,
        "summary": summary,
        "error": None,
    })
    with TASK_LOCK:
        TASKS[task_id]["status"] = final_status
        TASKS[task_id]["result"] = experiment
        TASKS[task_id]["outputs"] = list_outputs()
        TASKS[task_id]["updated"] = time.time()
    task_log(task_id, "评审模式对比实验已终止" if final_status == "cancelled" else "评审模式对比实验完成")


def create_task_record(
    task_id: str,
    debug_agent: bool = False,
    *,
    task_type: str = "child",
    parent_task_id: str | None = None,
    client_id: str = "",
    group_concurrency: int | None = None,
    context: dict | None = None,
):
    """Create the canonical in-memory task record shared by every submission path."""
    with TASK_LOCK:
        parent = TASKS.get(parent_task_id) if parent_task_id else None
        trace_id = str((parent or {}).get("traceId") or "")
        if not trace_id and parent_task_id:
            trace_id = f"tr_{hashlib.sha256(parent_task_id.encode('utf-8')).hexdigest()[:16]}"
        if not trace_id:
            trace_id = new_trace_id()
        parent_cancelled = bool(
            parent_task_id
            and (parent or {}).get("cancel_requested")
        )
        TASKS[task_id] = {
            "id": task_id,
            "traceId": trace_id,
            "taskType": task_type,
            "parentTaskId": parent_task_id,
            "clientId": client_id,
            "groupConcurrency": group_concurrency,
            "context": dict(context or {}),
            "status": "cancelled" if parent_cancelled else "queued",
            "cancel_requested": parent_cancelled,
            "result": ({
                "stdout": "",
                "stderr": "父任务已取消，子任务不再启动。",
                "returncode": 130,
            } if parent_cancelled else None),
            "outputs": [],
            "logs": [],
            "debugLogs": [],
            "debug_agent": bool(debug_agent),
            "created": time.time(),
            "updated": time.time(),
        }
    runtime_log(
        task_type,
        "任务已创建并进入队列。" if not parent_cancelled else "父任务已取消，子任务不再启动。",
        level="WARN" if parent_cancelled else "INFO",
        event="task_created",
        trace_id=trace_id,
        task_id=task_id,
    )
    if len(TASKS) % 16 == 0:
        gc_tasks()


def track_task_future(task_id: str, future, label: str):
    """Convert an unexpected worker exception into a terminal task error."""

    def complete(completed) -> None:
        try:
            completed.result()
        except CancelledError:
            message = f"{label}在全局线程池排队期间被取消。"
            status = "cancelled"
            returncode = 130
        except BaseException as exc:
            message = f"{label}异常终止：{type(exc).__name__}: {exc}"
            status = "error"
            returncode = 1
        else:
            with TASK_LOCK:
                current = TASKS.get(task_id) or {}
                current_status = str(current.get("status") or "")
                cancel_requested = bool(current.get("cancel_requested"))
            if current_status in {"done", "error", "cancelled"}:
                return
            status = "cancelled" if cancel_requested else "error"
            returncode = 130 if cancel_requested else 1
            message = (
                f"{label}已取消。"
                if cancel_requested
                else f"{label}后台作业已结束，但未写入终态。"
            )

        result = {
            "stdout": "",
            "stderr": message,
            "returncode": returncode,
        }
        if status == "error":
            attach_error_metadata(result, message)
        with TASK_LOCK:
            task = TASKS.get(task_id)
            if not task or task.get("status") in {"done", "error", "cancelled"}:
                return
            task["status"] = status
            task["result"] = result
            task["outputs"] = list_outputs()
            task["updated"] = time.time()
        task_log(task_id, message)

    future.add_done_callback(complete)
    return future


def submit_requirement_review(payload: dict) -> tuple[str, dict]:
    """Create and enqueue one standalone requirement-review task."""
    task_id = f"requirement-review-{uuid.uuid4().hex[:12]}"
    create_task_record(
        task_id,
        debug_agent=True,
        task_type="requirement-review",
        client_id=str(payload.get("clientId") or ""),
        group_concurrency=1,
        context={"apiNames": list(payload.get("apiNames") or [])},
    )
    track_task_future(
        task_id,
        GLOBAL_TASK_POOL.submit(
            task_id,
            task_id,
            run_agent_requirement_review,
            payload,
            task_id,
            group_limit=1,
        ),
        "需求评审",
    )
    with TASK_LOCK:
        task = decorate_task(TASKS[task_id])
    return task_id, task


def submit_regression_task(payload: dict, examples: list[dict]) -> tuple[str, dict, dict[str, str]]:
    """Create validation-run records and enqueue their parent regression task."""
    task_id = str(uuid.uuid4())
    create_task_record(
        task_id,
        task_type="regression",
        client_id=str(payload.get("clientId") or ""),
        group_concurrency=1,
        context={
            "taskName": str(payload.get("taskName") or "").strip()
            or f"质量验证 · {time.strftime('%Y-%m-%d %H:%M')}",
        },
    )
    run_ids = prepare_validation_runs(task_id, examples)
    task_payload = dict(payload)
    task_payload["exampleIds"] = [str(item.get("id") or "") for item in examples]
    task_payload["_validationRunIds"] = run_ids
    with TASK_LOCK:
        TASKS[task_id]["caseIds"] = list(run_ids.keys())
        TASKS[task_id]["result"] = {
            "items": [],
            "summary": {"total": len(examples), "pass": 0, "fail": 0, "error": 0, "skip": 0, "running": 0},
        }
    track_task_future(
        task_id,
        GLOBAL_TASK_POOL.submit(
            task_id,
            task_id,
            run_regression_validation,
            task_payload,
            task_id,
            group_limit=1,
        ),
        "质量验证",
    )
    with TASK_LOCK:
        task = decorate_task(TASKS[task_id])
    return task_id, task, run_ids


def submit_review_mode_experiment(payload: dict, examples: list[dict]) -> tuple[str, dict]:
    """Persist and enqueue a direct-versus-two-phase comparison experiment."""
    task_id = str(uuid.uuid4())
    experiment_id = f"experiment-{task_output_token(task_id)}"
    repetitions = max(1, min(3, int(payload.get("repetitions") or 1)))
    create_task_record(
        task_id,
        task_type="review-mode-experiment",
        client_id=str(payload.get("clientId") or ""),
        group_concurrency=1,
        context={
            "taskName": str(payload.get("taskName") or "").strip()
            or f"模式对比实验 · {time.strftime('%Y-%m-%d %H:%M')}",
        },
    )
    task_payload = {
        **payload,
        "experimentId": experiment_id,
        "caseIds": [str(item.get("id") or "") for item in examples],
        "repetitions": repetitions,
    }
    initial = {
        "experimentId": experiment_id,
        "taskId": task_id,
        "status": "queued",
        "caseIds": task_payload["caseIds"],
        "caseCount": len(examples),
        "repetitions": repetitions,
        "createdAt": _iso_time(),
        "startedAt": None,
        "finishedAt": None,
        "items": [],
        "summary": summarize_review_mode_experiment([], len(examples) * repetitions),
        "error": None,
    }
    upsert_review_mode_experiment(initial)
    with TASK_LOCK:
        TASKS[task_id]["caseIds"] = task_payload["caseIds"]
        TASKS[task_id]["experimentId"] = experiment_id
        TASKS[task_id]["result"] = initial
    track_task_future(
        task_id,
        GLOBAL_TASK_POOL.submit(
            task_id,
            task_id,
            run_review_mode_experiment,
            task_payload,
            task_id,
            group_limit=1,
        ),
        "评审模式对比实验",
    )
    with TASK_LOCK:
        task = decorate_task(TASKS[task_id])
    return experiment_id, task


def run_review_pipeline(
    parent_task_id: str,
    index: int,
    phase1_payload: dict,
    draft: str,
    *,
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
    create_task_record(
        phase1_task_id,
        debug_agent=bool(phase1_payload.get("debugAgent")),
        parent_task_id=parent_task_id,
    )
    run_agent_phase1(phase1_payload, phase1_task_id)
    phase1_task = TASKS.get(phase1_task_id, {})
    if phase1_task.get("status") != "done":
        raise RuntimeError((phase1_task.get("result") or {}).get("stderr") or "Phase 1 失败")
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
        return {
            "stopped": True,
            "phase1": phase1_result,
            "raw": "已请求停止，未启动 Phase 2",
        }
    create_task_record(
        phase2_task_id,
        debug_agent=bool(phase2_payload.get("debugAgent")),
        parent_task_id=parent_task_id,
    )
    run_agent_phase2(phase2_payload, phase2_task_id)
    phase2_task = TASKS.get(phase2_task_id, {})
    if phase2_task.get("status") != "done":
        raise RuntimeError((phase2_task.get("result") or {}).get("stderr") or "Phase 2 失败")
    phase2_result = phase2_task.get("result") or {}
    return {
        "stopped": False,
        "phase1": phase1_result,
        "phase2": phase2_result,
        "raw": phase2_result.get("stdout") or "",
        "reviewFileId": phase2_result.get("reviewFileId"),
    }


def run_review_pair(row: dict, payload: dict, task_id: str, index: int) -> dict:
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
    if task_cancel_requested(task_id):
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
        if task_cancel_requested(task_id):
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
                child_suffix=f"a{attempt + 1}",
                mock=payload.get("mock"),
            )
            phase1_result = pipeline["phase1"]
            item["standardFileId"] = phase1_result.get("standardFileId")
            item["standardMetadataFileId"] = phase1_result.get("standardMetadataFileId")
            item["standardText"] = phase1_result.get("stdout") or ""
            item["cacheHit"] = bool(phase1_result.get("cacheHit"))
            report = str(pipeline.get("raw") or "")
            summary = collect_review_stats(report)
            item.update({
                "status": "done",
                "raw": report,
                "reviewFileId": pipeline.get("reviewFileId"),
                "conclusion": detect_verdict_text(report),
                "riskCount": int(summary.get("fail") or 0),
                "summary": summary,
            })
            return item
        except Exception as exc:
            last_error = str(exc)
            if task_cancel_requested(task_id):
                item.update({"status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"})
                return item
            task_log(task_id, f"{factor_name} 第 {attempt + 1} 次失败：{last_error}")
    item.update({"status": "error", "raw": last_error, **classify_task_error(last_error)})
    return item


def run_batch_factor_job(row: dict, payload: dict, task_id: str, index: int, items: list[dict]) -> dict:
    """Run one batch item and return its result without mutating sibling items."""
    with TASK_LOCK:
        if task_id in TASKS and index < len(items):
            items[index]["status"] = "running"
            TASKS[task_id]["updated"] = time.time()
    return run_review_pair(row, payload, task_id, index)


def finish_batch_factor(task_id: str, index: int, items: list[dict], future):
    """Merge a completed batch future and finalize the parent when all items end."""
    try:
        item = future.result()
    except CancelledError:
        item = {**items[index], "status": "skip", "raw": "任务已取消", "skipReason": "任务已取消"}
    except Exception as exc:
        item = {**items[index], "status": "error", "raw": str(exc), **classify_task_error(str(exc))}
    items[index] = item
    if item.get("status") == "done":
        message = f"完成：{item.get('factorName')}"
    elif item.get("status") == "skip":
        message = f"跳过：{item.get('factorName')}：{item.get('skipReason') or item.get('raw')}"
    else:
        message = f"失败：{item.get('factorName')}：{item.get('raw')}"
    terminal = {"done", "error", "skip"}
    all_finished = all(value.get("status") in terminal for value in items)
    final_status = None
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task["result"] = {"items": items, "stdout": "", "stderr": "", "returncode": 0}
        task["outputs"] = list_outputs()
        task["updated"] = time.time()
        if all_finished:
            final_status = "cancelled" if task.get("cancel_requested") else "done"
            task["status"] = final_status
    task_log(task_id, message)
    if final_status:
        task_log(task_id, "批量评审已停止" if final_status == "cancelled" else "批量评审完成")


def run_scheduled_phase1(payload: dict, task_id: str) -> dict:
    """Execute Phase 1 for the scheduled-task service and return its result payload."""
    create_task_record(task_id, task_type="scheduled-phase1", parent_task_id=task_id.rsplit("-p1-", 1)[0])
    run_agent_phase1(payload, task_id)
    with TASK_LOCK:
        task = TASKS.get(task_id, {})
        return {"status": task.get("status"), "result": dict(task.get("result") or {})}


def run_scheduled_phase2(payload: dict, task_id: str) -> dict:
    """Execute Phase 2 for the scheduled-task service and return its result payload."""
    create_task_record(task_id, task_type="scheduled-phase2", parent_task_id=task_id.rsplit("-p2-", 1)[0])
    run_agent_phase2(payload, task_id)
    with TASK_LOCK:
        task = TASKS.get(task_id, {})
        return {"status": task.get("status"), "result": dict(task.get("result") or {})}


def get_scheduled_task_service():
    """Lazily construct the process-wide scheduled-task service singleton."""
    global SCHEDULED_TASK_SERVICE
    if SCHEDULED_TASK_SERVICE is None:
        SCHEDULED_TASK_SERVICE = build_scheduled_task_service(
            run_review_pair=run_review_pair,
            run_phase1=run_scheduled_phase1,
            run_phase2=run_scheduled_phase2,
            cancel_execution=request_task_cancel,
            log=lambda message: runtime_log(
                "scheduled_tasks",
                message,
                level="ERROR" if "failed" in str(message).lower() else "INFO",
                event="scheduler",
            ),
        )
    return SCHEDULED_TASK_SERVICE


def start_batch_review(payload: dict, task_id: str):
    """Submit each batch item fairly through the shared global task pool."""
    rows = payload.get("rows") or []
    concurrency = get_runtime_settings()["batchConcurrency"]
    with TASK_LOCK:
        TASKS[task_id]["status"] = "running"
        TASKS[task_id]["updated"] = time.time()
        TASKS[task_id]["result"] = {"items": [], "stdout": "", "stderr": "", "returncode": 0}
    task_log(task_id, f"批量评审进入全局线程池：{len(rows)} 条，本任务并发上限 {concurrency}")
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
    with TASK_LOCK:
        TASKS[task_id]["result"] = {"items": items, "stdout": "", "stderr": "", "returncode": 0}
        TASKS[task_id]["updated"] = time.time()
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
            group_limit=concurrency,
        )
        future.add_done_callback(
            lambda completed, item_index=index: finish_batch_factor(task_id, item_index, items, completed)
        )


class Handler(BaseHTTPRequestHandler):
    """HTTP adapter that delegates domain endpoints to route modules and services."""
    def log_message(self, fmt, *args):
        """Suppress BaseHTTPRequestHandler's duplicate access-log output."""
        return

    def read_json_body(self):
        """Decode the request body as JSON, returning an empty object when absent."""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8")) if body else {}

    def do_GET(self):
        """Dispatch read requests across system, validation, task, and static routes."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if scheduled_routes.handle_get(self, path, get_scheduled_task_service):
            return
        if system_routes.handle_get(
            self,
            path,
            parsed.query,
            list_outputs=list_outputs,
            pool_snapshot=GLOBAL_TASK_POOL.snapshot,
            system_health=system_health,
            model_test=test_model_runtime,
        ):
            return
        if static_routes.handle_get(self, path, STATIC_DIR, OUTPUTS_DIR):
            return
        if validation_routes.handle_get(self, path, parsed.query):
            return
        if path == "/api/review-history":
            success_response(self, review_history_records())
        elif path == "/api/review-records":
            success_response(self, query_review_records(parse_qs(parsed.query)))
        elif path == "/api/examples":
            json_response(self, 200, {"examples": parse_examples()})
        elif path == "/api/business-rules":
            json_response(self, 200, {"rules": parse_business_rules()})
        elif path == "/api/tasks":
            query = parse_qs(parsed.query)
            include_history = (query.get("scope") or ["active"])[0].lower() == "all"
            json_response(self, 200, {
                "tasks": task_history(include_history=include_history),
                "pool": GLOBAL_TASK_POOL.snapshot(),
            })
        elif path.startswith("/api/tasks/"):
            task_id = unquote(path.split("/")[-1])
            with TASK_LOCK:
                task = TASKS.get(task_id)
            if not task:
                persisted_task = find_persisted_task(task_id)
                if not persisted_task:
                    json_response(self, 404, {"error": "not found"})
                    return
                json_response(self, 200, {"task": persisted_task, "pool": GLOBAL_TASK_POOL.snapshot()})
                return
            json_response(self, 200, {
                "task": decorate_task(task),
                "pool": GLOBAL_TASK_POOL.snapshot(),
            })
        else:
            static_routes.serve_static(self, STATIC_DIR, path.lstrip("/"))

    def do_POST(self):
        """Dispatch creation, control, import, and review-submission requests."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_post(self, parsed.path, get_scheduled_task_service):
            return
        if requirement_review_routes.handle_post(
            self,
            parsed.path,
            submit_review=submit_requirement_review,
        ):
            return
        if validation_routes.handle_post(
            self,
            parsed.path,
            submit_regression=submit_regression_task,
            submit_experiment=submit_review_mode_experiment,
            validation_run_factory=validation_run_for_example,
        ):
            return
        if parsed.path.startswith("/api/review-records/") and parsed.path.endswith("/archive"):
            record_id = unquote(parsed.path.split("/")[-2])
            try:
                payload = self.read_json_body()
                success_response(self, archive_review_record(record_id, bool(payload.get("archived", True))))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            return
        if parsed.path == "/api/parse-excel":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            name = payload.get("name", "")
            data_b64 = payload.get("data", "")
            if not data_b64:
                json_response(self, 400, {"error": "no data"})
                return
            try:
                raw = base64.b64decode(data_b64)
            except Exception:
                json_response(self, 400, {"error": "base64 decode failed"})
                return
            is_xlsx = name.lower().endswith(".xlsx") or name.lower().endswith(".xls")
            if is_xlsx:
                rows = parse_xlsx(raw)
            else:
                try:
                    rows = parse_csv_rows(raw.decode("utf-8-sig"))
                except UnicodeDecodeError:
                    rows = parse_csv_rows(raw.decode("gb18030"))
            json_response(self, 200, {"rows": rows, "items": normalize_batch_rows_from_table(rows), "name": name})
            return
        if parsed.path == "/api/examples":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            with EXAMPLES_LOCK:
                examples = parse_examples()
                example = normalize_example(payload)
                errors = validate_example(example)
                if errors:
                    json_response(self, 400, {"error": "；".join(errors), "errors": errors})
                    return
                if any(item.get("id") == example.get("id") for item in examples):
                    json_response(self, 409, {"error": "案例 ID 已存在，请换一个 ID。"})
                    return
                examples.append(example)
                save_examples(examples)
            json_response(self, 201, {"example": example, "examples": examples})
            return
        if parsed.path == "/api/business-rules":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            with BUSINESS_RULES_LOCK:
                rules = parse_business_rules()
                rule = normalize_business_rule(payload)
                errors = validate_business_rule(rule)
                if errors:
                    json_response(self, 400, {"error": "；".join(errors), "errors": errors})
                    return
                rules.append(rule)
                save_business_rules(rules)
            json_response(self, 201, {"rule": rule, "rules": rules})
            return
        if parsed.path == "/api/review/batch":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            rows = payload.get("rows") or []
            if not isinstance(rows, list) or not rows:
                json_response(self, 400, {"error": "批量评审缺少有效数据行。"})
                return
            task_id = str(uuid.uuid4())
            concurrency = get_runtime_settings()["batchConcurrency"]
            create_task_record(
                task_id,
                task_type="batch",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=concurrency,
                context={
                    "taskName": str(payload.get("taskName") or "").strip()
                    or f"批量评审 · {time.strftime('%Y-%m-%d %H:%M')}",
                },
            )
            with TASK_LOCK:
                TASKS[task_id]["result"] = {"items": []}
            start_batch_review(payload, task_id)
            with TASK_LOCK:
                task = decorate_task(TASKS[task_id])
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/cancel"):
            task_id = parsed.path.split("/")[-2]
            if not request_task_cancel(task_id):
                json_response(self, 404, {"error": "task not found"})
                return
            with TASK_LOCK:
                task = TASKS.get(task_id)
            json_response(self, 200, {"task": decorate_task(task), "pool": GLOBAL_TASK_POOL.snapshot()})
            return
        if parsed.path == "/api/review/phase1":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            if not payload.get("requirement") or not payload.get("apiNames"):
                json_response(self, 400, {"error": "Phase 1 请填写需求内容并至少选择一个接口文档。"})
                return
            # Standalone single-factor runs retain Agent messages and tool events
            # in the in-memory task log so the execution-detail view is auditable.
            payload["debugAgent"] = True
            task_id = str(uuid.uuid4())
            create_task_record(
                task_id,
                debug_agent=True,
                task_type="phase1",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=1,
                context={
                    "factorName": str(payload.get("factorName") or "").strip(),
                    "requirement": str(payload.get("requirement") or ""),
                },
            )
            track_task_future(
                task_id,
                GLOBAL_TASK_POOL.submit(
                    task_id,
                    task_id,
                    run_agent_phase1,
                    payload,
                    task_id,
                    group_limit=1,
                ),
                "Phase 1",
            )
            with TASK_LOCK:
                task = decorate_task(TASKS[task_id])
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        
        if parsed.path == "/api/review/phase2":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            if not payload.get("draft") or not payload.get("standardFileId") or not payload.get("requirement"):
                json_response(self, 400, {"error": "Phase 2 请提供待评审加工逻辑、原始需求和标准文件 ID。"})
                return
            payload["debugAgent"] = True
            task_id = str(uuid.uuid4())
            create_task_record(
                task_id,
                debug_agent=True,
                task_type="phase2",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=1,
                context={
                    "factorName": str(payload.get("factorName") or "").strip(),
                    "requirement": str(payload.get("requirement") or ""),
                    "standardFileId": str(payload.get("standardFileId") or ""),
                },
            )
            track_task_future(
                task_id,
                GLOBAL_TASK_POOL.submit(
                    task_id,
                    task_id,
                    run_agent_phase2,
                    payload,
                    task_id,
                    group_limit=1,
                ),
                "Phase 2",
            )
            with TASK_LOCK:
                task = decorate_task(TASKS[task_id])
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        
        if parsed.path != "/api/review":
            json_response(self, 404, {"error": "not found"})
            return
        json_response(self, 410, {"error": "/api/review 已废弃，请使用 /api/review/phase1 和 /api/review/phase2 两阶段接口。"})
        return

    def do_PUT(self):
        """Dispatch updates for settings, validation cases, rules, and examples."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_put(self, parsed.path, get_scheduled_task_service):
            return
        if validation_routes.handle_put(self, parsed.path):
            return
        if parsed.path == "/api/runtime-settings":
            try:
                success_response(self, update_runtime_settings(self.read_json_body()))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except (TypeError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path.startswith("/api/business-rules/"):
            rule_id = unquote(parsed.path.split("/")[-1])
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            with BUSINESS_RULES_LOCK:
                rules = parse_business_rules()
                for index, item in enumerate(rules):
                    if item.get("id") == rule_id:
                        if payload.get("version") and item.get("version") and payload.get("version") != item.get("version"):
                            json_response(self, 409, {"error": "规则已被其他操作更新，请刷新后再保存。", "rule": item})
                            return
                        updated = normalize_business_rule(payload, existing_id=rule_id)
                        errors = validate_business_rule(updated)
                        if errors:
                            json_response(self, 400, {"error": "；".join(errors), "errors": errors})
                            return
                        rules[index] = updated
                        save_business_rules(rules)
                        json_response(self, 200, {"rule": updated, "rules": rules})
                        return
            json_response(self, 404, {"error": "business rule not found"})
            return
        if not parsed.path.startswith("/api/examples/"):
            json_response(self, 404, {"error": "not found"})
            return
        example_id = unquote(parsed.path.split("/")[-1])
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            json_response(self, 400, {"error": "invalid json"})
            return
        with EXAMPLES_LOCK:
            examples = parse_examples()
            for index, item in enumerate(examples):
                if item.get("id") == example_id:
                    if payload.get("version") and item.get("version") and payload.get("version") != item.get("version"):
                        json_response(self, 409, {"error": "案例已被其他操作更新，请刷新后再保存。", "example": item})
                        return
                    updated = normalize_example(payload, existing_id=example_id)
                    errors = validate_example(updated)
                    if errors:
                        json_response(self, 400, {"error": "；".join(errors), "errors": errors})
                        return
                    examples[index] = updated
                    save_examples(examples)
                    json_response(self, 200, {"example": examples[index], "examples": examples})
                    return
        json_response(self, 404, {"error": "example not found"})

    def do_DELETE(self):
        """Dispatch deletion requests while preserving active-task safety checks."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_delete(self, parsed.path, get_scheduled_task_service):
            return
        if validation_routes.handle_delete(self, parsed.path):
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, delete_task_record(task_id))
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except (OSError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path.startswith("/api/review-history/"):
            record_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, delete_review_history_record(record_id))
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except (OSError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path.startswith("/api/business-rules/"):
            rule_id = unquote(parsed.path.split("/")[-1])
            with BUSINESS_RULES_LOCK:
                rules = parse_business_rules()
                filtered = [item for item in rules if item.get("id") != rule_id]
                if len(filtered) == len(rules):
                    json_response(self, 404, {"error": "business rule not found"})
                    return
                save_business_rules(filtered)
            json_response(self, 200, {"rules": filtered})
            return
        if not parsed.path.startswith("/api/examples/"):
            json_response(self, 404, {"error": "not found"})
            return
        example_id = unquote(parsed.path.split("/")[-1])
        with EXAMPLES_LOCK:
            examples = parse_examples()
            filtered = [item for item in examples if item.get("id") != example_id]
            if len(filtered) == len(examples):
                json_response(self, 404, {"error": "example not found"})
                return
            save_examples(filtered)
        json_response(self, 200, {"examples": filtered})

def main():
    """Recover persisted state, start scheduling, and serve the local application."""
    startup_trace_id = new_trace_id()
    runtime_log(
        "server",
        "风控 AI 评审平台正在启动。",
        event="server_starting",
        trace_id=startup_trace_id,
    )
    recover_stale_validation_runs()
    recover_stale_review_mode_experiments()
    scheduled_service = get_scheduled_task_service()
    scheduled_service.start_scheduler()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Risk review web running at http://127.0.0.1:{port}")
    runtime_log(
        "server",
        f"服务已启动：http://127.0.0.1:{port}",
        event="server_started",
        trace_id=startup_trace_id,
    )
    try:
        server.serve_forever()
    finally:
        runtime_log(
            "server",
            "风控 AI 评审平台正在停止。",
            event="server_stopping",
            trace_id=startup_trace_id,
        )
        scheduled_service.stop_scheduler()
        server.server_close()


if __name__ == "__main__":
    main()
