from __future__ import annotations

import base64
import csv
import json
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from risk_review_web.api_docs import get_api_doc, list_api_docs
from risk_review_web.paths import SCHEDULED_RUNS_PATH, SCHEDULED_TASKS_PATH
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text, read_text

JsonDict = dict[str, Any]
RunItemExecutor = Callable[[JsonDict, JsonDict, str, int], JsonDict]
Phase1Executor = Callable[[JsonDict, str], JsonDict]
Phase2Executor = Callable[[JsonDict, str], JsonDict]
LogFn = Callable[[str], None]
CancelExecutionFn = Callable[[str], bool]

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
VALID_RUNTIME_MODES = {"mock", "claude"}
VALID_FAILURE_POLICIES = {"continue", "stop"}
VALID_RUN_STATUSES = {"pending", "running", "success", "partial_success", "failed", "cancelled"}

SCHEDULER_POLL_INTERVAL_SECONDS = max(1, int(os.environ.get("RISK_REVIEW_SCHEDULER_POLL", "15")))
SCHEDULED_RUN_MAX_COUNT = max(10, int(os.environ.get("RISK_REVIEW_SCHEDULED_RUN_MAX", "1000")))


class ScheduledTaskError(ValueError):
    pass


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


def _atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _read_json_list(path: Path) -> list[JsonDict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, [])
        return []
    try:
        data = json.loads(read_text(path))
    except Exception as exc:
        raise ScheduledTaskError(f"定时任务数据文件无法解析：{path.name}") from exc
    if not isinstance(data, list):
        raise ScheduledTaskError(f"定时任务数据文件格式错误：{path.name} 顶层必须是数组。")
    return data


def _normalize_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n，、]+", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _parse_time_text(value: str | None) -> tuple[int, int]:
    text = (value or "").strip() or "00:00"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ScheduledTaskError("runTime 格式必须为 HH:MM。")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduledTaskError("runTime 超出有效时间范围。")
    return hour, minute


def _weekday_index(value: str | None) -> int:
    text = (value or "").strip().lower()
    mapping = {
        "1": 0,
        "mon": 0,
        "monday": 0,
        "周一": 0,
        "2": 1,
        "tue": 1,
        "tuesday": 1,
        "周二": 1,
        "3": 2,
        "wed": 2,
        "wednesday": 2,
        "周三": 2,
        "4": 3,
        "thu": 3,
        "thursday": 3,
        "周四": 3,
        "5": 4,
        "fri": 4,
        "friday": 4,
        "周五": 4,
        "6": 5,
        "sat": 5,
        "saturday": 5,
        "周六": 5,
        "7": 6,
        "sun": 6,
        "sunday": 6,
        "周日": 6,
        "周天": 6,
    }
    if text not in mapping:
        raise ScheduledTaskError("weekday 无效，应为 1-7 或周几。")
    return mapping[text]


def _month_day(value: str | None) -> int:
    try:
        day = int(str(value or "").strip())
    except ValueError as exc:
        raise ScheduledTaskError("monthDay 必须为 1-31。") from exc
    if day < 1 or day > 31:
        raise ScheduledTaskError("monthDay 必须为 1-31。")
    return day


def _set_time(base: datetime, run_time: str | None) -> datetime:
    hour, minute = _parse_time_text(run_time)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _safe_month_replace(base: datetime, day: int) -> datetime:
    candidate = base.replace(day=1)
    while True:
        try:
            return candidate.replace(day=day)
        except ValueError:
            day -= 1
            if day < 1:
                raise ScheduledTaskError("无法计算 monthly nextRunAt。")


def compute_next_run_at(task: JsonDict, *, now: datetime | None = None, from_time: datetime | None = None) -> str | None:
    current = now or _now()
    base = from_time or current
    schedule_type = task.get("scheduleType")
    run_time = task.get("runTime")
    if schedule_type == "once":
        run_date_text = (task.get("runDate") or "").strip()
        if not run_date_text:
            raise ScheduledTaskError("once 任务必须提供 runDate。")
        try:
            run_date = datetime.strptime(run_date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ScheduledTaskError("runDate 格式必须为 YYYY-MM-DD。") from exc
        candidate = _set_time(datetime.combine(run_date, datetime.min.time(), tzinfo=current.tzinfo), run_time)
        if candidate <= current and task.get("lastRunAt"):
            return None
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "daily":
        candidate = _set_time(base, run_time)
        if candidate <= current:
            candidate = _set_time(base + timedelta(days=1), run_time)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "weekly":
        weekday = _weekday_index(task.get("weekday"))
        candidate = _set_time(base, run_time)
        delta = (weekday - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=delta)
        if candidate <= current:
            candidate = candidate + timedelta(days=7)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "monthly":
        day = _month_day(task.get("monthDay"))
        candidate = _safe_month_replace(_set_time(base, run_time), day)
        if candidate <= current:
            if candidate.month == 12:
                next_month = candidate.replace(year=candidate.year + 1, month=1, day=1)
            else:
                next_month = candidate.replace(month=candidate.month + 1, day=1)
            candidate = _safe_month_replace(_set_time(next_month, run_time), day)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "cron":
        cron_text = (task.get("cron") or "").strip()
        return compute_next_run_from_cron(cron_text, current).isoformat(timespec="seconds")
    raise ScheduledTaskError("scheduleType 无效。")


def compute_next_run_from_cron(cron_text: str, now: datetime) -> datetime:
    parts = cron_text.split()
    if len(parts) != 5:
        raise ScheduledTaskError("cron 仅支持标准 5 段表达式。")
    minute_values = _parse_cron_field(parts[0], 0, 59)
    hour_values = _parse_cron_field(parts[1], 0, 23)
    day_values = _parse_cron_field(parts[2], 1, 31)
    month_values = _parse_cron_field(parts[3], 1, 12)
    raw_week_values = _parse_cron_field(parts[4], 0, 6, sunday_alias=7)
    # cron: 0/7=Sunday, 1=Monday; datetime.weekday(): Monday=0, Sunday=6.
    week_values = {6 if value == 0 else value - 1 for value in raw_week_values}
    day_wildcard = parts[2] == "*"
    week_wildcard = parts[4] == "*"
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        day_matches = candidate.day in day_values
        week_matches = candidate.weekday() in week_values
        calendar_matches = (
            day_matches and week_matches
            if day_wildcard or week_wildcard
            else day_matches or week_matches
        )
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.month in month_values
            and calendar_matches
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ScheduledTaskError("cron 下一次执行时间超出一年搜索范围。")


def _parse_cron_field(field: str, minimum: int, maximum: int, sunday_alias: int | None = None) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            values.update(range(minimum, maximum + 1))
            continue
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ScheduledTaskError(f"cron 字段步长非法：{part}") from exc
            if step <= 0:
                raise ScheduledTaskError(f"cron 字段步长非法：{part}")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        if sunday_alias is not None:
            if start == sunday_alias:
                start = 0
            if end == sunday_alias:
                end = 0
        if start < minimum or end > maximum:
            raise ScheduledTaskError(f"cron 字段超出范围：{part}")
        if start <= end:
            values.update(range(start, end + 1, step))
        else:
            wrapped = list(range(start, maximum + 1, step)) + list(range(minimum, end + 1, step))
            values.update(wrapped)
    if not values:
        raise ScheduledTaskError("cron 字段不能为空。")
    return values


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


def parse_csv_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in csv.reader(StringIO(text.lstrip("\ufeff"))):
        if any(str(cell).strip() for cell in row):
            rows.append([str(cell).strip() for cell in row])
    return rows


def parse_xlsx_rows(data: bytes) -> list[list[str]]:
    rows: list[list[str]] = []
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with ZipFile(BytesIO(data)) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                tree = ET.parse(archive.open("xl/sharedStrings.xml"))
                for si in tree.findall(f"{ns}si"):
                    texts = [node.text or "" for node in si.findall(f".//{ns}t")]
                    shared_strings.append("".join(texts))
            tree = ET.parse(archive.open("xl/worksheets/sheet1.xml"))
            sheet_data = tree.getroot().find(f"{ns}sheetData")
            if sheet_data is None:
                return rows
            for row_node in sheet_data.findall(f"{ns}row"):
                cells: dict[str, str] = {}
                for cell in row_node.findall(f"{ns}c"):
                    ref = cell.get("r", "")
                    cell_type = cell.get("t", "")
                    value_node = cell.find(f"{ns}v")
                    value = value_node.text if value_node is not None else ""
                    if cell_type == "s" and value:
                        try:
                            value = shared_strings[int(float(value))]
                        except (ValueError, IndexError):
                            pass
                    cells[ref] = value
                if not cells:
                    continue
                max_col = 0
                row_number = "".join(ch for ch in next(iter(cells)) if ch.isdigit()) or "1"
                for ref in cells:
                    letters = "".join(ch for ch in ref if ch.isalpha())
                    col_index = 0
                    for letter in letters.upper():
                        col_index = col_index * 26 + (ord(letter) - 64)
                    max_col = max(max_col, col_index)
                row_data: list[str] = []
                for col in range(1, max_col + 1):
                    letters = ""
                    cursor = col
                    while cursor > 0:
                        cursor, remainder = divmod(cursor - 1, 26)
                        letters = chr(remainder + 65) + letters
                    row_data.append(str(cells.get(f"{letters}{row_number}", "")).strip())
                rows.append(row_data)
    except Exception:
        return []
    return rows


def decode_import_rows(name: str, data_base64: str) -> list[list[str]]:
    if not data_base64:
        raise ScheduledTaskError("上传文件内容为空。")
    try:
        raw = base64.b64decode(data_base64)
    except Exception as exc:
        raise ScheduledTaskError("上传文件 base64 解码失败。") from exc
    if str(name or "").lower().endswith((".xlsx", ".xls")):
        rows = parse_xlsx_rows(raw)
    else:
        try:
            rows = parse_csv_rows(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            rows = parse_csv_rows(raw.decode("gb18030"))
    if not rows:
        raise ScheduledTaskError("未解析出有效数据行。")
    return rows


def preview_import(name: str, data_base64: str, mode: str | None = None, task_standard_file: str | None = None) -> JsonDict:
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


@dataclass
class SchedulerDependencies:
    run_review_pair: RunItemExecutor
    run_phase1: Phase1Executor
    run_phase2: Phase2Executor


class ScheduledTaskStore:
    def __init__(self, tasks_path: Path = SCHEDULED_TASKS_PATH, runs_path: Path = SCHEDULED_RUNS_PATH) -> None:
        self.tasks_path = tasks_path
        self.runs_path = runs_path
        self.lock = threading.RLock()
        self._ensure_files()

    def _ensure_files(self) -> None:
        for path in (self.tasks_path, self.runs_path):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(path, [])

    def list_tasks(self) -> list[JsonDict]:
        with self.lock:
            tasks = _read_json_list(self.tasks_path)
        tasks.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return tasks

    def get_task(self, task_id: str) -> JsonDict | None:
        for task in self.list_tasks():
            if task.get("taskId") == task_id:
                return task
        return None

    def save_tasks(self, tasks: list[JsonDict]) -> None:
        with self.lock:
            _atomic_write_json(self.tasks_path, tasks)

    def list_runs(self, task_id: str | None = None) -> list[JsonDict]:
        with self.lock:
            runs = _read_json_list(self.runs_path)
        if task_id:
            runs = [item for item in runs if item.get("taskId") == task_id]
        runs.sort(key=lambda item: item.get("startedAt") or item.get("createdAt") or "", reverse=True)
        return runs

    def get_run(self, run_id: str) -> JsonDict | None:
        for run in self.list_runs():
            if run.get("runId") == run_id:
                return run
        return None

    def save_runs(self, runs: list[JsonDict]) -> None:
        with self.lock:
            _atomic_write_json(self.runs_path, runs)


class ScheduledTaskService:
    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        dependencies: SchedulerDependencies,
        log: LogFn | None = None,
        cancel_execution: CancelExecutionFn | None = None,
    ) -> None:
        self.store = store
        self.dependencies = dependencies
        self.log = log or (lambda _message: None)
        self.cancel_execution = cancel_execution
        self.execution_lock = threading.RLock()
        self.running_task_ids: set[str] = set()
        self.run_futures: dict[str, list[Future]] = {}
        self.cancelled_run_ids: set[str] = set()
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._recover_interrupted_runs()

    def start_scheduler(self) -> None:
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self.stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="scheduled-task-loop", daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self) -> None:
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
        """A process restart cannot resume in-flight Claude calls; close them explicitly."""
        now_text = _iso_now()
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
                })
                changed = True
            if changed:
                _atomic_write_json(self.store.runs_path, runs)

    def list_tasks(self) -> list[JsonDict]:
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
        return preview_import(name, data_base64, mode=mode, task_standard_file=task_standard_file)

    def get_task(self, task_id: str) -> JsonDict:
        task = self.store.get_task(task_id)
        if not task:
            raise ScheduledTaskError("任务不存在。")
        return {
            **task,
            "runs": self.store.list_runs(task_id),
        }

    def create_task(self, payload: JsonDict) -> JsonDict:
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
        return self.store.list_runs(task_id)

    def get_run(self, run_id: str) -> JsonDict:
        run = self.store.get_run(run_id)
        if not run:
            raise ScheduledTaskError("执行记录不存在。")
        return run

    def cancel_run(self, run_id: str) -> JsonDict:
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
        run = self.submit_task_run(task_id, trigger=trigger)
        deadline = time.time() + 300
        while time.time() < deadline:
            current = self.get_run(run["runId"])
            if current.get("status") in {"success", "partial_success", "failed", "cancelled"}:
                return current
            time.sleep(0.02)
        raise ScheduledTaskError("任务执行超时。")

    def submit_task_run(self, task_id: str, *, trigger: str = "manual") -> JsonDict:
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

    def _record_scheduler_dispatch_failure(self, task: JsonDict, error_message: str) -> None:
        run = self._create_run_record(task, trigger="scheduler")
        finished_at = _iso_now()
        self._update_run(
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
                    {
                        "index": index + 1,
                        "factorName": str(item.get("factorName") or f"row-{index + 1}"),
                        "status": "failed",
                        "standardFile": str(item.get("standardFile") or task.get("standardFile") or ""),
                        "reviewFile": "",
                        "error": error_message,
                    }
                    for index, item in enumerate(task.get("items") or [])
                ],
                "error": error_message,
            },
        )
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
            self._update_run(
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
                group_limit=max(1, min(GLOBAL_TASK_POOL.max_workers, int(task.get("concurrency") or 1))),
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
            return {
                "index": index + 1,
                "factorName": item.get("factorName") or f"row-{index + 1}",
                "status": "failed",
                "standardFile": item.get("standardFile") or task_standard_file,
                "reviewFile": "",
                "error": str(exc),
            }

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
                    "skipReviewer": task.get("skipReviewer") is True,
                    "skipStageValidation": task.get("skipStageValidation") is True,
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
                "error": "" if pipeline_item.get("status") == "done" else str(pipeline_item.get("raw") or "执行失败"),
            }
        if mode == "phase1_only":
            task_id = f"{run_id}-p1-{index + 1}"
            payload = {
                "factorName": factor_name,
                "requirement": requirement,
                "apiNames": api_names,
                "mock": mock,
                "skipReviewer": task.get("skipReviewer") is True,
                "skipStageValidation": task.get("skipStageValidation") is True,
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
                "error": "" if ok else str(result.get("stderr") or "Phase 1 失败"),
            }
        if mode == "phase2_only":
            standard_file = str(item.get("standardFile") or task_standard_file or "").strip()
            payload = {
                "factorName": factor_name,
                "requirement": requirement,
                "apiNames": api_names,
                "draft": build_item_draft(item),
                "standardFileId": standard_file,
                "mock": mock,
                "skipReviewer": task.get("skipReviewer") is True,
                "skipStageValidation": task.get("skipStageValidation") is True,
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
                "error": "" if ok else str(result.get("stderr") or "Phase 2 失败"),
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
        runtime_mode = str(payload.get("runtimeMode") or "").strip() or "claude"
        if runtime_mode not in VALID_RUNTIME_MODES:
            raise ScheduledTaskError("runtimeMode 无效。")
        failure_policy = str(payload.get("failurePolicy") or "").strip() or "continue"
        if failure_policy not in VALID_FAILURE_POLICIES:
            raise ScheduledTaskError("failurePolicy 无效。")
        concurrency = int(payload.get("concurrency") or 1)
        if concurrency < 1:
            raise ScheduledTaskError("concurrency 不能小于 1。")
        concurrency = min(concurrency, GLOBAL_TASK_POOL.max_workers)
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
            "concurrency": concurrency,
            "failurePolicy": failure_policy,
            "skipReviewer": bool(payload.get("skipReviewer", True)),
            "skipStageValidation": bool(payload.get("skipStageValidation", True)),
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
    log: LogFn | None = None,
    cancel_execution: CancelExecutionFn | None = None,
) -> ScheduledTaskService:
    return ScheduledTaskService(
        store=ScheduledTaskStore(),
        dependencies=SchedulerDependencies(
            run_review_pair=run_review_pair,
            run_phase1=run_phase1,
            run_phase2=run_phase2,
        ),
        log=log,
        cancel_execution=cancel_execution,
    )
