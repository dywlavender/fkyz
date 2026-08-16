from __future__ import annotations

# Canonical scheduling service implementation.

import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from risk_review_web.api_docs import get_api_doc, list_api_docs
from risk_review_web.error_classification import classify_task_error
from risk_review_web.paths import OUTPUTS_DIR
from risk_review_web.runtime_settings import get_runtime_settings
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text, read_text
from risk_review_web.services.tabular_import import TabularImportError, decode_import_rows as decode_tabular_import_rows
from risk_review_web.services.review_history import scheduled_run_to_review_record
from risk_review_web.scheduling.repository import ScheduledTaskStore, _atomic_write_json, _read_json_list

JsonDict = dict[str, Any]
RunItemExecutor = Callable[[JsonDict, JsonDict, str, int], JsonDict]
Phase1Executor = Callable[[JsonDict, str], JsonDict]
Phase2Executor = Callable[[JsonDict, str], JsonDict]
LogFn = Callable[[str], None]
CancelExecutionFn = Callable[[str], bool]
ReviewRecordSink = Callable[[JsonDict], Any]

IMPORT_FIELDS = [
    "factorName",
    "requirement",
    "paramFields",
    "procLogic",
    "reviewContent",
    "defaultVal",
    "apiDocIds",
    "businessRuleIds",
    "standardFile",
    "remark",
]

IMPORT_ALIASES: dict[str, list[str]] = {
    "factorName": ["factorName", "factor_name", "因子名", "因子名称"],
    "requirement": ["requirement", "需求", "需求内容", "准出标准"],
    "paramFields": ["paramFields", "因子加工输入字段"],
    "procLogic": ["procLogic", "加工逻辑", "逻辑"],
    "reviewContent": ["reviewContent", "review_content", "待评审内容", "review"],
    "defaultVal": ["defaultVal", "defaultValues", "默认值"],
    "apiDocIds": ["apiDocIds", "api_doc_ids", "接口文档", "接口文档ID", "apiDocs"],
    "businessRuleIds": ["businessRuleIds", "business_rule_ids", "业务规则", "业务规则ID"],
    "standardFile": ["standardFile", "standard_file", "标准文件", "标准文件ID"],
    "remark": ["remark", "备注"],
}

VALID_SCHEDULE_TYPES = {"once", "daily", "weekly", "monthly", "cron"}
VALID_MODES = {"phase1_only", "phase1_phase2", "phase2_only"}
VALID_RUNTIME_MODES = {"mock", "agent"}
VALID_FAILURE_POLICIES = {"continue", "stop"}
VALID_RUN_STATUSES = {"pending", "running", "success", "partial_success", "failed", "cancelled"}

SCHEDULER_POLL_INTERVAL_SECONDS = max(1, int(os.environ.get("RISK_REVIEW_SCHEDULER_POLL", "15")))
SCHEDULED_RUN_MAX_COUNT = max(10, int(os.environ.get("RISK_REVIEW_SCHEDULED_RUN_MAX", "1000")))


from risk_review_web.scheduling.models import ScheduledTaskError
from risk_review_web.scheduling.schedule_parser import compute_next_run_at, compute_next_run_from_cron


def enrich_error_metadata(payload: JsonDict) -> JsonDict:
    """Attach normalized task-error metadata to a scheduled-run item."""
    error = str(payload.get("error") or "").strip()
    if error:
        payload.update(classify_task_error(error))
    return payload


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso_now() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _normalize_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n，、]+", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_import_header(cell: Any) -> str:
    return str(cell or "").strip()


def _normalize_import_rows(rows: list[list[str]]) -> tuple[list[JsonDict], list[JsonDict]]:
    if not rows:
        return [], [{"rowNumber": 1, "error": "文件为空。"}]
    header = [_normalize_import_header(cell) for cell in rows[0]]
    column_map: dict[str, int] = {}
    for field, aliases in IMPORT_ALIASES.items():
        for alias in aliases:
            if alias in header:
                column_map[field] = header.index(alias)
                break
    missing_required = [field for field in ("factorName", "requirement") if field not in column_map]
    if missing_required:
        return [], [{"rowNumber": 1, "error": f"缺少必需列：{', '.join(missing_required)}"}]
    items: list[JsonDict] = []
    errors: list[JsonDict] = []
    for index, raw in enumerate(rows[1:], start=2):
        item: JsonDict = {}
        for field in IMPORT_FIELDS:
            position = column_map.get(field)
            item[field] = str(raw[position]).strip() if position is not None and position < len(raw) else ""
        if not any(str(item.get(field) or "").strip() for field in IMPORT_FIELDS):
            continue
        if not item["factorName"]:
            errors.append({"rowNumber": index, "error": "factorName 不能为空。"})
            continue
        if not item["requirement"]:
            errors.append({"rowNumber": index, "error": "requirement 不能为空。"})
            continue
        item["apiDocIds"] = _normalize_id_list(item.get("apiDocIds"))
        item["businessRuleIds"] = _normalize_id_list(item.get("businessRuleIds"))
        item["_rowNumber"] = index
        items.append(item)
    return items, errors


def decode_import_rows(name: str, data_base64: str) -> list[list[str]]:
    """Decode an uploaded CSV/XLSX payload through the shared parser."""
    try:
        return decode_tabular_import_rows(name, data_base64)
    except TabularImportError as exc:
        raise ScheduledTaskError(str(exc)) from exc


def preview_import(name: str, data_base64: str, mode: str | None = None, task_standard_file: str | None = None) -> JsonDict:
    """Normalize an import file and return row-level validation without persisting it."""
    rows = decode_import_rows(name, data_base64)
    items, base_errors = _normalize_import_rows(rows)
    mode_value = (mode or "").strip() or "phase1_phase2"
    validation_errors = list(base_errors)
    valid_items: list[JsonDict] = []
    for offset, item in enumerate(items, start=2):
        row_number = int(item.pop("_rowNumber", offset))
        row_errors = validate_item(item, mode=mode_value, task_standard_file=task_standard_file)
        if row_errors:
            for error in row_errors:
                validation_errors.append({"rowNumber": row_number, "error": error, "factorName": item.get("factorName") or ""})
            continue
        valid_items.append(item)
    error_row_count = len({int(error.get("rowNumber") or 0) for error in validation_errors})
    return {
        "fileName": name,
        "totalRows": max(0, len(rows) - 1),
        "validRows": len(valid_items),
        "errorRows": error_row_count,
        "items": valid_items,
        "errors": validation_errors,
        "columns": rows[0],
    }


def validate_item(item: JsonDict, *, mode: str, task_standard_file: str | None = None) -> list[str]:
    """Validate one scheduled factor against the selected execution mode."""
    errors: list[str] = []
    factor_name = str(item.get("factorName") or "").strip()
    requirement = str(item.get("requirement") or "").strip()
    review_content = build_item_draft(item)
    standard_file = str(item.get("standardFile") or "").strip()
    if not factor_name:
        errors.append("factorName 不能为空。")
    if not requirement:
        errors.append("requirement 不能为空。")
    if mode in {"phase1_only", "phase1_phase2"} and not _normalize_id_list(item.get("apiDocIds")):
        errors.append(f"{mode} 模式下 apiDocIds 不能为空。")
    if mode == "phase1_phase2" and not review_content:
        errors.append("phase1_phase2 模式下 reviewContent 不能为空。")
    if mode == "phase2_only" and not (standard_file or (task_standard_file or "").strip()):
        errors.append("phase2_only 模式下必须提供 standardFile 或任务级标准来源。")
    return errors


def resolve_api_names(item: JsonDict) -> list[str]:
    """Resolve imported API identifiers to names accepted by Phase 1."""
    api_ids = _normalize_id_list(item.get("apiDocIds"))
    if not api_ids:
        return []
    docs = list_api_docs(include_content=False)
    by_name = {str(doc.get("name") or "").strip(): doc for doc in docs}
    by_path = {str(doc.get("path") or "").strip(): doc for doc in docs}
    names: list[str] = []
    for api_id in api_ids:
        doc = by_name.get(api_id) or by_path.get(api_id) or get_api_doc(api_id)
        if doc:
            names.append(str(doc.get("name") or api_id).strip())
        else:
            names.append(api_id)
    return [name for name in names if name]


def build_item_draft(item: JsonDict) -> str:
    """Build a scheduled item's review draft from explicit sections when needed."""
    review_content = str(item.get("reviewContent") or "").strip()
    if review_content:
        return review_content
    parts: list[str] = []
    param_fields = str(item.get("paramFields") or "").strip()
    proc_logic = str(item.get("procLogic") or "").strip()
    if param_fields:
        parts.append(f"【因子加工输入字段】\n{param_fields}")
    if proc_logic:
        parts.append(f"【加工逻辑】\n{proc_logic}")
    parts.append("【默认值】\n字段缺失、格式错误或数据异常时返回默认值")
    return "\n\n".join(parts)


def summarize_run_status(items: list[JsonDict]) -> tuple[str, int, int, int]:
    """Derive parent status and counters from scheduled item results."""
    success = sum(1 for item in items if item.get("status") == "success")
    failed = sum(1 for item in items if item.get("status") == "failed")
    unknown = sum(1 for item in items if item.get("status") not in {"success", "failed"})
    if failed == 0 and unknown == 0:
        status = "success"
    elif success > 0 and failed > 0:
        status = "partial_success"
    elif success > 0 and unknown > 0:
        status = "partial_success"
    elif failed > 0:
        status = "failed"
    else:
        status = "cancelled" if items and all(item.get("status") == "cancelled" for item in items) else "partial_success"
    return status, success, failed, unknown


def detect_review_conclusion(markdown: str) -> str:
    """Extract the normalized verdict from a generated review report."""
    source = str(markdown or "")
    overall = re.search(r"\|\s*总体结论\s*\|\s*([^|]+?)\s*\|", source)
    target = overall.group(1) if overall else source
    if re.search(r"不通过|未通过|不符合", target):
        return "fail"
    if re.search(r"⛔|无法评审", target):
        return "unreviewable"
    if re.search(r"✅|(?<!不)通过|符合", target):
        return "pass"
    return "unreviewable"


@dataclass
class SchedulerDependencies:
    """Execution callbacks supplied by the application to the scheduler."""
    run_review_pair: RunItemExecutor
    run_phase1: Phase1Executor
    run_phase2: Phase2Executor


class ScheduledTaskService:
    """Own scheduled-task validation, dispatch, cancellation, and timer scanning."""
    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        dependencies: SchedulerDependencies,
        review_record_sink: ReviewRecordSink,
        log: LogFn | None = None,
        cancel_execution: CancelExecutionFn | None = None,
        outputs_dir: Path = OUTPUTS_DIR,
    ) -> None:
        self.store = store
        self.dependencies = dependencies
        self.review_record_sink = review_record_sink
        self.log = log or (lambda _message: None)
        self.cancel_execution = cancel_execution
        self.outputs_dir = outputs_dir
        self.execution_lock = threading.RLock()
        self.running_task_ids: set[str] = set()
        self.run_futures: dict[str, list[Future]] = {}
        self.cancelled_run_ids: set[str] = set()
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._recover_interrupted_runs()

    def start_scheduler(self) -> None:
        """Start the single timer-scanning thread when not already running."""
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self.stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="scheduled-task-loop", daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self) -> None:
        """Request shutdown and briefly join the timer-scanning thread."""
        self.stop_event.set()
        if self.scheduler_thread and self.scheduler_thread is not threading.current_thread():
            self.scheduler_thread.join(timeout=2)

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.scan_and_run_due_tasks()
            except Exception as exc:
                self.log(f"scheduled task scan failed: {exc}")
            self.stop_event.wait(SCHEDULER_POLL_INTERVAL_SECONDS)

    def scan_and_run_due_tasks(self) -> None:
        """Submit every due, enabled, non-running task to the global pool."""
        now = _now()
        for task in self.store.list_tasks():
            if not task.get("enabled"):
                continue
            next_run_at = _parse_iso(task.get("nextRunAt"))
            if next_run_at is None or next_run_at > now:
                continue
            with self.execution_lock:
                if str(task.get("taskId") or "") in self.running_task_ids:
                    continue
            try:
                self.submit_task_run(task.get("taskId"), trigger="scheduler")
            except Exception as exc:
                self.log(f"scheduled task dispatch failed: taskId={task.get('taskId')} error={exc}")
                self._record_scheduler_dispatch_failure(task, str(exc))

    def _recover_interrupted_runs(self) -> None:
        """A process restart cannot resume in-flight Agent calls; close them explicitly."""
        now_text = _iso_now()
        recovered: list[JsonDict] = []
        with self.store.lock:
            runs = _read_json_list(self.store.runs_path)
            changed = False
            for run in runs:
                if run.get("status") not in {"pending", "running"}:
                    continue
                run.update({
                    "status": "failed",
                    "finishedAt": now_text,
                    "error": "服务重启，原执行上下文已丢失。",
                    **classify_task_error("服务重启，原执行上下文已丢失。"),
                })
                recovered.append(dict(run))
                changed = True
            if changed:
                _atomic_write_json(self.store.runs_path, runs)
        for run in recovered:
            task = self.store.get_task(str(run.get("taskId") or "")) or {}
            self._persist_terminal_review_record(run, task)

    def list_tasks(self) -> list[JsonDict]:
        """Return tasks enriched with item counts and latest-run status."""
        tasks = self.store.list_tasks()
        runs = self.store.list_runs()
        latest_by_task: dict[str, JsonDict] = {}
        for run in runs:
            task_id = str(run.get("taskId") or "")
            if task_id and task_id not in latest_by_task:
                latest_by_task[task_id] = run
        enriched: list[JsonDict] = []
        for task in tasks:
            latest_run = latest_by_task.get(str(task.get("taskId") or ""))
            enriched.append({
                **task,
                "itemCount": len(task.get("items") or []),
                "lastRunStatus": latest_run.get("status") if latest_run else None,
                "lastRunAt": task.get("lastRunAt") or (latest_run.get("startedAt") if latest_run else task.get("lastRunAt")),
            })
        return enriched

    def preview_import(self, name: str, data_base64: str, *, mode: str | None = None, task_standard_file: str | None = None) -> JsonDict:
        """Validate an uploaded factor file without creating a task."""
        return preview_import(name, data_base64, mode=mode, task_standard_file=task_standard_file)

    def get_task(self, task_id: str) -> JsonDict:
        """Return one task together with its complete run history."""
        task = self.store.get_task(task_id)
        if not task:
            raise ScheduledTaskError("任务不存在。")
        return {
            **task,
            "runs": self.store.list_runs(task_id),
        }

    def create_task(self, payload: JsonDict) -> JsonDict:
        """Validate and persist a new scheduled task."""
        task = self._normalize_task_payload(payload)
        task["taskId"] = payload.get("taskId") or f"task-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        now_text = _iso_now()
        task["createdAt"] = now_text
        task["updatedAt"] = now_text
        task["lastRunAt"] = ""
        task["nextRunAt"] = compute_next_run_at(task) if task.get("enabled") else ""
        with self.store.lock:
            tasks = _read_json_list(self.store.tasks_path)
            tasks.append(task)
            _atomic_write_json(self.store.tasks_path, tasks)
        return task

    def update_task(self, task_id: str, payload: JsonDict) -> JsonDict:
        """Merge, validate, and persist an existing scheduled task."""
        with self.store.lock:
            tasks = _read_json_list(self.store.tasks_path)
            for index, existing in enumerate(tasks):
                if existing.get("taskId") != task_id:
                    continue
                normalized = self._normalize_task_payload({**existing, **payload, "taskId": task_id})
                normalized["taskId"] = task_id
                normalized["createdAt"] = existing.get("createdAt") or _iso_now()
                normalized["updatedAt"] = _iso_now()
                normalized["lastRunAt"] = existing.get("lastRunAt") or ""
                normalized["nextRunAt"] = compute_next_run_at(normalized) if normalized.get("enabled") else ""
                tasks[index] = normalized
                _atomic_write_json(self.store.tasks_path, tasks)
                return normalized
        raise ScheduledTaskError("任务不存在。")

    def delete_task(self, task_id: str) -> None:
        """Delete a non-running task and its persisted run history."""
        with self.execution_lock:
            if task_id in self.running_task_ids:
                raise ScheduledTaskError("任务正在执行，请先取消对应运行。")
            with self.store.lock:
                tasks = _read_json_list(self.store.tasks_path)
                filtered = [task for task in tasks if task.get("taskId") != task_id]
                if len(filtered) == len(tasks):
                    raise ScheduledTaskError("任务不存在。")
                _atomic_write_json(self.store.tasks_path, filtered)
                runs = _read_json_list(self.store.runs_path)
                _atomic_write_json(self.store.runs_path, [run for run in runs if run.get("taskId") != task_id])

    def set_enabled(self, task_id: str, enabled: bool) -> JsonDict:
        """Enable or disable a task and recompute its next execution time."""
        with self.store.lock:
            tasks = _read_json_list(self.store.tasks_path)
            for task in tasks:
                if task.get("taskId") != task_id:
                    continue
                task["enabled"] = enabled
                task["updatedAt"] = _iso_now()
                next_run_at = compute_next_run_at(task) if enabled else ""
                if enabled and not next_run_at:
                    raise ScheduledTaskError("该单次任务已经执行；请修改执行日期后再启用。")
                task["nextRunAt"] = next_run_at
                _atomic_write_json(self.store.tasks_path, tasks)
                return task
        raise ScheduledTaskError("任务不存在。")

    def list_runs(self, task_id: str) -> list[JsonDict]:
        """Return all runs belonging to one scheduled task."""
        return self.store.list_runs(task_id)

    def get_run(self, run_id: str) -> JsonDict:
        """Return one run enriched with conclusions from report artifacts."""
        run = self.store.get_run(run_id)
        if not run:
            raise ScheduledTaskError("执行记录不存在。")
        return self._enrich_run_conclusions(run)

    def _enrich_run_conclusions(self, run: JsonDict) -> JsonDict:
        enriched_items: list[JsonDict] = []
        output_root = self.outputs_dir.resolve()
        for raw_item in run.get("items") or []:
            item = dict(raw_item)
            if item.get("reviewConclusion") in {"pass", "fail", "unreviewable"}:
                enriched_items.append(item)
                continue
            review_file = str(item.get("reviewFile") or "").strip()
            if not review_file or Path(review_file).name != review_file:
                item["reviewConclusion"] = "unreviewable"
                enriched_items.append(item)
                continue
            candidate = (output_root / review_file).resolve()
            if candidate.parent != output_root or not candidate.is_file():
                item["reviewConclusion"] = "unreviewable"
                enriched_items.append(item)
                continue
            try:
                item["reviewConclusion"] = detect_review_conclusion(read_text(candidate))
            except OSError:
                item["reviewConclusion"] = "unreviewable"
            enriched_items.append(item)
        return {**run, "items": enriched_items}

    def cancel_run(self, run_id: str) -> JsonDict:
        """Request cancellation for a pending or running execution."""
        run = self.get_run(run_id)
        if run.get("status") not in {"pending", "running"}:
            return run
        with self.execution_lock:
            self.cancelled_run_ids.add(run_id)
        self._update_run(run_id, {"status": "running", "error": "正在终止执行"})
        GLOBAL_TASK_POOL.cancel_group(run_id)
        if self.cancel_execution:
            self.cancel_execution(run_id)
        return self.get_run(run_id)

    def run_task_now(self, task_id: str, *, trigger: str = "manual") -> JsonDict:
        """Submit a task and block until its run reaches a terminal state."""
        run = self.submit_task_run(task_id, trigger=trigger)
        deadline = time.time() + 300
        while time.time() < deadline:
            current = self.get_run(run["runId"])
            if current.get("status") in {"success", "partial_success", "failed", "cancelled"}:
                return current
            time.sleep(0.02)
        raise ScheduledTaskError("任务执行超时。")

    def submit_task_run(self, task_id: str, *, trigger: str = "manual") -> JsonDict:
        """Create and asynchronously submit one non-reentrant scheduled run."""
        with self.execution_lock:
            task = self.store.get_task(task_id)
            if not task:
                raise ScheduledTaskError("任务不存在。")
            if task_id in self.running_task_ids:
                raise ScheduledTaskError("同一任务正在执行，不能重复触发。")
            self.running_task_ids.add(task_id)
        try:
            run = self._create_run_record(task, trigger=trigger)
            self._submit_run_to_pool(task, run, trigger=trigger)
            return run
        except Exception:
            with self.execution_lock:
                self.running_task_ids.discard(task_id)
            raise

    def _create_run_record(self, task: JsonDict, *, trigger: str) -> JsonDict:
        run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        run: JsonDict = {
            "runId": run_id,
            "taskId": task["taskId"],
            "taskName": task.get("taskName") or task["taskId"],
            "trigger": trigger,
            "status": "pending",
            "startedAt": "",
            "finishedAt": "",
            "total": len(task.get("items") or []),
            "success": 0,
            "failed": 0,
            "unknown": 0,
            "durationMs": 0,
            "items": [],
            "createdAt": _iso_now(),
        }
        with self.store.lock:
            runs = _read_json_list(self.store.runs_path)
            runs.append(run)
            if len(runs) > SCHEDULED_RUN_MAX_COUNT:
                runs = runs[-SCHEDULED_RUN_MAX_COUNT:]
            _atomic_write_json(self.store.runs_path, runs)
        return run

    def _update_run(self, run_id: str, patch: JsonDict) -> JsonDict:
        with self.store.lock:
            runs = _read_json_list(self.store.runs_path)
            for index, current in enumerate(runs):
                if current.get("runId") != run_id:
                    continue
                runs[index] = {**current, **patch}
                _atomic_write_json(self.store.runs_path, runs)
                return runs[index]
        raise ScheduledTaskError("执行记录不存在。")

    def _update_task_record(self, task_id: str, patch: JsonDict) -> JsonDict:
        with self.store.lock:
            tasks = _read_json_list(self.store.tasks_path)
            for index, task in enumerate(tasks):
                if task.get("taskId") != task_id:
                    continue
                tasks[index] = {**task, **patch}
                _atomic_write_json(self.store.tasks_path, tasks)
                return tasks[index]
        raise ScheduledTaskError("任务不存在。")

    def _persist_terminal_review_record(self, run: JsonDict, task: JsonDict) -> None:
        """Write one terminal scheduled run to the unified history sink."""
        try:
            self.review_record_sink(scheduled_run_to_review_record(run, task))
        except Exception as exc:
            message = f"统一评审历史写入失败：{type(exc).__name__}: {exc}"
            self.log(f"scheduled run history persist failed: runId={run.get('runId')} error={message}")
            try:
                self._update_run(
                    str(run.get("runId") or ""),
                    {"historyPersisted": False, "persistenceError": message},
                )
            except Exception as update_exc:
                self.log(f"scheduled run persistence error marker failed: runId={run.get('runId')} error={update_exc}")
            return
        try:
            self._update_run(
                str(run.get("runId") or ""),
                {"historyPersisted": True, "persistenceError": ""},
            )
        except Exception as exc:
            self.log(f"scheduled run history success marker failed: runId={run.get('runId')} error={exc}")

    def retry_run_history(self, run_id: str) -> JsonDict:
        """Retry a scheduled run whose unified-history write previously failed."""
        run = self.store.get_run(run_id)
        if not run:
            raise ScheduledTaskError("执行记录不存在。")
        if run.get("status") not in {"success", "partial_success", "failed", "cancelled"}:
            raise ScheduledTaskError("只有终态执行记录可以重试历史写入。")
        task = self.store.get_task(str(run.get("taskId") or "")) or {}
        self.review_record_sink(scheduled_run_to_review_record(run, task))
        self._update_run(run_id, {"historyPersisted": True, "persistenceError": ""})
        return self.get_run(run_id)

    def _record_scheduler_dispatch_failure(self, task: JsonDict, error_message: str) -> None:
        run = self._create_run_record(task, trigger="scheduler")
        finished_at = _iso_now()
        completed_run = self._update_run(
            run["runId"],
            {
                "status": "failed",
                "startedAt": finished_at,
                "finishedAt": finished_at,
                "success": 0,
                "failed": len(task.get("items") or []),
                "unknown": 0,
                "durationMs": 0,
                "items": [
                    enrich_error_metadata({
                        "index": index + 1,
                        "factorName": str(item.get("factorName") or f"row-{index + 1}"),
                        "status": "failed",
                        "standardFile": str(item.get("standardFile") or task.get("standardFile") or ""),
                        "reviewFile": "",
                        "error": error_message,
                    })
                    for index, item in enumerate(task.get("items") or [])
                ],
                "error": error_message,
                **classify_task_error(error_message),
            },
        )
        self._persist_terminal_review_record(completed_run, task)
        if task.get("scheduleType") == "once":
            self._update_task_record(str(task.get("taskId") or ""), {"enabled": False, "nextRunAt": "", "updatedAt": finished_at})
        else:
            self._update_task_record(
                str(task.get("taskId") or ""),
                {"nextRunAt": compute_next_run_at(task, from_time=_now() + timedelta(seconds=1)), "updatedAt": finished_at},
            )

    def _submit_run_to_pool(self, task: JsonDict, run: JsonDict, *, trigger: str) -> None:
        started = time.time()
        run_id = str(run["runId"])
        task_id = str(task["taskId"])
        items = list(task.get("items") or [])
        task_standard_file = str(task.get("standardFile") or "").strip()
        failure_policy = str(task.get("failurePolicy") or "continue")
        ordered_results: list[JsonDict | None] = [None] * len(items)
        callback_lock = threading.RLock()
        submitted_all = False
        finalized = False

        def finish_run() -> None:
            nonlocal finalized
            with callback_lock:
                if finalized or not submitted_all or any(value is None for value in ordered_results):
                    return
                finalized = True
                run_items = [value for value in ordered_results if value is not None]
            status, success, failed, unknown = summarize_run_status(run_items)
            with self.execution_lock:
                run_was_cancelled = run_id in self.cancelled_run_ids
            if run_was_cancelled:
                status = "cancelled"
            finished_at = _iso_now()
            task_patch: JsonDict = {
                "lastRunAt": run.get("startedAt") or finished_at,
                "updatedAt": finished_at,
            }
            if trigger == "scheduler" and task.get("enabled"):
                if task.get("scheduleType") == "once":
                    task_patch["enabled"] = False
                    task_patch["nextRunAt"] = ""
                else:
                    task_patch["nextRunAt"] = compute_next_run_at(task, from_time=_now() + timedelta(seconds=1))
            try:
                self._update_task_record(task_id, task_patch)
            except ScheduledTaskError as exc:
                self.log(f"scheduled task record update skipped: taskId={task_id} error={exc}")
            completed_run = self._update_run(
                run_id,
                {
                    "status": status,
                    "items": run_items,
                    "finishedAt": finished_at,
                    "success": success,
                    "failed": failed,
                    "unknown": unknown,
                    "durationMs": int((time.time() - started) * 1000),
                },
            )
            self._persist_terminal_review_record(completed_run, task)
            with self.execution_lock:
                self.running_task_ids.discard(task_id)
                self.run_futures.pop(run_id, None)
                self.cancelled_run_ids.discard(run_id)

        started_at = _iso_now()
        run["startedAt"] = started_at
        run["status"] = "running"
        self._update_run(run_id, {"status": "running", "startedAt": started_at, "items": []})
        futures: list[Future] = []
        with self.execution_lock:
            self.run_futures[run_id] = futures

        for index, item in enumerate(items):
            future = GLOBAL_TASK_POOL.submit(
                run_id,
                f"{run_id}-item-{index + 1}",
                self._execute_single_item_safely,
                task,
                item,
                run_id,
                index,
                task_standard_file=task_standard_file,
                group_limit=get_runtime_settings()["scheduledConcurrency"],
            )
            futures.append(future)

            def item_complete(completed: Future, current_index=index) -> None:
                try:
                    result = completed.result()
                except CancelledError:
                    result = {
                        "index": current_index + 1,
                        "factorName": str(items[current_index].get("factorName") or f"row-{current_index + 1}"),
                        "status": "cancelled",
                        "standardFile": str(items[current_index].get("standardFile") or task_standard_file),
                        "reviewFile": "",
                        "error": "任务已取消",
                    }
                except Exception as exc:
                    result = {
                        "index": current_index + 1,
                        "factorName": str(items[current_index].get("factorName") or f"row-{current_index + 1}"),
                        "status": "failed",
                        "standardFile": str(items[current_index].get("standardFile") or task_standard_file),
                        "reviewFile": "",
                        "error": str(exc),
                    }
                result = enrich_error_metadata(result)
                with callback_lock:
                    ordered_results[current_index] = result
                    self._update_run(run_id, {"items": [value for value in ordered_results if value is not None]})
                if failure_policy == "stop" and result.get("status") == "failed":
                    GLOBAL_TASK_POOL.cancel_group(run_id)
                finish_run()

            future.add_done_callback(item_complete)
        with callback_lock:
            submitted_all = True
        finish_run()

    def _execute_single_item_safely(
        self,
        task: JsonDict,
        item: JsonDict,
        run_id: str,
        index: int,
        *,
        task_standard_file: str,
    ) -> JsonDict:
        try:
            with self.execution_lock:
                cancelled = run_id in self.cancelled_run_ids
            if cancelled:
                raise CancelledError()
            result = self._execute_single_item(task, item, run_id, index, task_standard_file=task_standard_file)
            with self.execution_lock:
                cancelled = run_id in self.cancelled_run_ids
            if cancelled:
                raise CancelledError()
            return result
        except CancelledError:
            return {
                "index": index + 1,
                "factorName": item.get("factorName") or f"row-{index + 1}",
                "status": "cancelled",
                "standardFile": item.get("standardFile") or task_standard_file,
                "reviewFile": "",
                "error": "任务已终止",
            }
        except Exception as exc:
            with self.execution_lock:
                cancelled = run_id in self.cancelled_run_ids
            if cancelled:
                return {
                    "index": index + 1,
                    "factorName": item.get("factorName") or f"row-{index + 1}",
                    "status": "cancelled",
                    "standardFile": item.get("standardFile") or task_standard_file,
                    "reviewFile": "",
                    "error": "任务已终止",
                }
            self.log(f"scheduled task item failed: runId={run_id} index={index + 1} factor={item.get('factorName') or ''} error={exc}")
            return enrich_error_metadata({
                "index": index + 1,
                "factorName": item.get("factorName") or f"row-{index + 1}",
                "status": "failed",
                "standardFile": item.get("standardFile") or task_standard_file,
                "reviewFile": "",
                "error": str(exc),
            })

    def _execute_single_item(
        self,
        task: JsonDict,
        item: JsonDict,
        run_id: str,
        index: int,
        *,
        task_standard_file: str,
    ) -> JsonDict:
        mode = task.get("mode")
        runtime_mode = task.get("runtimeMode")
        factor_name = str(item.get("factorName") or f"row-{index + 1}").strip()
        requirement = str(item.get("requirement") or "").strip()
        api_names = resolve_api_names(item)
        mock = runtime_mode == "mock"
        if mode == "phase1_phase2":
            pipeline_item = self.dependencies.run_review_pair(
                {
                    "factorName": factor_name,
                    "requirement": requirement,
                    "apiNames": api_names,
                    "draft": build_item_draft(item),
                },
                {
                    "mock": mock,
                    "retry": 0,
                },
                run_id,
                index,
            )
            return {
                "index": index + 1,
                "factorName": factor_name,
                "status": "success" if pipeline_item.get("status") == "done" else "failed",
                "standardFile": pipeline_item.get("standardFileId") or "",
                "reviewFile": pipeline_item.get("reviewFileId") or "",
                "reviewConclusion": detect_review_conclusion(str(pipeline_item.get("raw") or "")),
                "error": "" if pipeline_item.get("status") == "done" else str(pipeline_item.get("raw") or "执行失败"),
            }
        if mode == "phase1_only":
            task_id = f"{run_id}-p1-{index + 1}"
            payload = {
                "factorName": factor_name,
                "requirement": requirement,
                "apiNames": api_names,
                "mock": mock,
            }
            outcome = self.dependencies.run_phase1(payload, task_id)
            result = outcome.get("result") or {}
            ok = outcome.get("status") == "done"
            return {
                "index": index + 1,
                "factorName": factor_name,
                "status": "success" if ok else "failed",
                "standardFile": result.get("standardFileId") or "",
                "reviewFile": "",
                "error": "" if ok else str(result.get("error") or "Phase 1 失败"),
            }
        if mode == "phase2_only":
            standard_file = str(item.get("standardFile") or task_standard_file or "").strip()
            payload = {
                "factorName": factor_name,
                "requirement": requirement,
                "draft": build_item_draft(item),
                "standardFileId": standard_file,
                "mock": mock,
            }
            task_id = f"{run_id}-p2-{index + 1}"
            outcome = self.dependencies.run_phase2(payload, task_id)
            result = outcome.get("result") or {}
            ok = outcome.get("status") == "done"
            return {
                "index": index + 1,
                "factorName": factor_name,
                "status": "success" if ok else "failed",
                "standardFile": standard_file,
                "reviewFile": result.get("reviewFileId") or "",
                "reviewConclusion": detect_review_conclusion(str(result.get("content") or "")),
                "error": "" if ok else str(result.get("error") or "Phase 2 失败"),
            }
        raise ScheduledTaskError("不支持的任务模式。")

    def _normalize_task_payload(self, payload: JsonDict) -> JsonDict:
        task_name = str(payload.get("taskName") or "").strip()
        if not task_name:
            raise ScheduledTaskError("taskName 不能为空。")
        schedule_type = str(payload.get("scheduleType") or "").strip()
        if schedule_type not in VALID_SCHEDULE_TYPES:
            raise ScheduledTaskError("scheduleType 无效。")
        mode = str(payload.get("mode") or "").strip()
        if mode not in VALID_MODES:
            raise ScheduledTaskError("mode 无效。")
        runtime_mode = str(payload.get("runtimeMode") or "").strip() or "agent"
        if runtime_mode not in VALID_RUNTIME_MODES:
            raise ScheduledTaskError("runtimeMode 无效。")
        failure_policy = str(payload.get("failurePolicy") or "").strip() or "continue"
        if failure_policy not in VALID_FAILURE_POLICIES:
            raise ScheduledTaskError("failurePolicy 无效。")
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            raise ScheduledTaskError("items 不能为空。")
        standard_file = str(payload.get("standardFile") or "").strip()
        normalized_items: list[JsonDict] = []
        for item in items:
            normalized_item = {
                "factorName": str(item.get("factorName") or "").strip(),
                "requirement": str(item.get("requirement") or "").strip(),
                "paramFields": str(item.get("paramFields") or "").strip(),
                "procLogic": str(item.get("procLogic") or "").strip(),
                "reviewContent": str(item.get("reviewContent") or "").strip(),
                "defaultVal": str(item.get("defaultVal") or item.get("defaultValues") or "").strip(),
                "apiDocIds": _normalize_id_list(item.get("apiDocIds")),
                "businessRuleIds": _normalize_id_list(item.get("businessRuleIds")),
                "standardFile": str(item.get("standardFile") or "").strip(),
                "remark": str(item.get("remark") or "").strip(),
            }
            item_errors = validate_item(normalized_item, mode=mode, task_standard_file=standard_file)
            if item_errors:
                raise ScheduledTaskError(f"{normalized_item.get('factorName') or '未命名因子'}：{'；'.join(item_errors)}")
            normalized_items.append(normalized_item)
        task: JsonDict = {
            "taskName": task_name,
            "enabled": bool(payload.get("enabled", True)),
            "scheduleType": schedule_type,
            "cron": str(payload.get("cron") or "").strip(),
            "runDate": str(payload.get("runDate") or "").strip(),
            "runTime": str(payload.get("runTime") or "").strip(),
            "weekday": str(payload.get("weekday") or "").strip(),
            "monthDay": str(payload.get("monthDay") or "").strip(),
            "mode": mode,
            "runtimeMode": runtime_mode,
            "failurePolicy": failure_policy,
            "standardFile": standard_file,
            "items": normalized_items,
        }
        if task["scheduleType"] == "cron" and not task["cron"]:
            raise ScheduledTaskError("cron 任务必须填写 cron 表达式。")
        if task["scheduleType"] == "weekly" and not task["weekday"]:
            raise ScheduledTaskError("weekly 任务必须填写 weekday。")
        if task["scheduleType"] == "monthly" and not task["monthDay"]:
            raise ScheduledTaskError("monthly 任务必须填写 monthDay。")
        if task["scheduleType"] == "once" and not task["runDate"]:
            raise ScheduledTaskError("once 任务必须填写 runDate。")
        if task["enabled"]:
            task["nextRunAt"] = compute_next_run_at(task)
        return task


def build_service(
    *,
    run_review_pair: RunItemExecutor,
    run_phase1: Phase1Executor,
    run_phase2: Phase2Executor,
    review_record_sink: ReviewRecordSink,
    log: LogFn | None = None,
    cancel_execution: CancelExecutionFn | None = None,
) -> ScheduledTaskService:
    """Construct the scheduler with application execution callbacks."""
    return ScheduledTaskService(
        store=ScheduledTaskStore(),
        dependencies=SchedulerDependencies(
            run_review_pair=run_review_pair,
            run_phase1=run_phase1,
            run_phase2=run_phase2,
        ),
        review_record_sink=review_record_sink,
        log=log,
        cancel_execution=cancel_execution,
    )
