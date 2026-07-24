#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import BytesIO
from io import StringIO
from concurrent.futures import CancelledError
from zipfile import ZipFile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from risk_review_web.api_docs import get_api_doc, list_api_docs
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
)
from risk_review_web.business_rules import (
    BUSINESS_RULES_LOCK,
    normalize_business_rule,
    parse_business_rules,
    save_business_rules,
    validate_business_rule,
)
from risk_review_web.paths import (
    BUSINESS_RULES_PATH,
    FACTOR_PROCESSING_SPEC_PATH,
    EXAMPLES_DIR,
    EXAMPLES_PATH,
    VALIDATION_RUNS_PATH,
    REVIEW_RECORDS_PATH,
    REVIEW_MODE_EXPERIMENTS_PATH,
    OUTPUTS_DIR,
    PHASE1_SKILL_PATH,
    PHASE2_SKILL_PATH,
    RULE_ENGINE_BUILD_PATH,
    RULE_ENGINE_PHASE1_OUTPUT_PATH,
    RULE_ENGINE_PHASE2_OUTPUT_PATH,
    ROOT,
    STATIC_DIR,
    resolve_validation_set_path,
)
from risk_review_web.prompt_builder import (
    build_phase1_prompt,
    build_phase2_prompt,
    build_direct_review_prompt,
)
from risk_review_web.scheduled_tasks import ScheduledTaskError, build_service as build_scheduled_task_service
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text, parse_frontmatter, read_text
from risk_review_web.error_classification import attach_error_metadata, classify_task_error

TASKS = {}
TASK_LOCK = threading.Lock()
RUNNING_PROCS = {}
RUNNING_PROCS_LOCK = threading.Lock()
EXAMPLES_LOCK = threading.Lock()
VALIDATION_RUNS_LOCK = threading.Lock()
REVIEW_MODE_EXPERIMENTS_LOCK = threading.Lock()
REVIEW_RECORDS_LOCK = threading.Lock()
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
    if removed:
        with RUNNING_PROCS_LOCK:
            for tid in list(RUNNING_PROCS.keys()):
                proc = RUNNING_PROCS.get(tid)
                if proc is not None and proc.poll() is not None:
                    RUNNING_PROCS.pop(tid, None)
    return removed


def json_response(handler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def success_response(handler, data, *, status: int = 200):
    json_response(handler, status, {"success": True, "data": data})


def error_response(handler, status: int, message: str, detail=None):
    payload = {"success": False, "error": message}
    if detail is not None:
        payload["detail"] = detail
    json_response(handler, status, payload)


def text_response(handler, status: int, content: bytes, content_type: str):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)



def parse_xlsx(data: bytes) -> list[list[str]]:
    """Parse .xlsx file using only Python stdlib."""
    rows = []
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    try:
        with ZipFile(BytesIO(data)) as z:
            sst = []
            if 'xl/sharedStrings.xml' in z.namelist():
                tree = ET.parse(z.open('xl/sharedStrings.xml'))
                for si in tree.findall(f'{ns}si'):
                    texts = [t.text or '' for t in si.findall(f'.//{ns}t')]
                    sst.append(''.join(texts))
            tree = ET.parse(z.open('xl/worksheets/sheet1.xml'))
            sheet = tree.getroot()
            sheet_data = sheet.find(f'{ns}sheetData')
            if sheet_data is None:
                return rows
            for row_el in sheet_data.findall(f'{ns}row'):
                cells = {}
                for c in row_el.findall(f'{ns}c'):
                    r = c.get('r', '')
                    t = c.get('t', '')
                    v_el = c.find(f'{ns}v')
                    val = v_el.text if v_el is not None else ''
                    if t == 's' and val:
                        try:
                            val = sst[int(float(val))]
                        except (IndexError, ValueError):
                            pass
                    cells[r] = val
                if not cells:
                    continue
                max_col = 0
                for ref in cells:
                    col_str = ''.join(filter(str.isalpha, ref))
                    col_idx = 0
                    for ch in col_str.upper():
                        col_idx = col_idx * 26 + (ord(ch) - 64)
                    max_col = max(max_col, col_idx)
                row_num = ''.join(filter(str.isdigit, next(iter(cells))))
                row_data = []
                for col_i in range(1, max_col + 1):
                    ref = ''
                    c = col_i
                    while c > 0:
                        c, r = divmod(c - 1, 26)
                        ref = chr(r + 65) + ref
                    ref += row_num
                    row_data.append(cells.get(ref, ''))
                rows.append(row_data)
    except Exception:
        return []
    return rows


def parse_csv_rows(text: str) -> list[list[str]]:
    sample = text.lstrip("\ufeff")
    rows = []
    for row in csv.reader(StringIO(sample)):
        if any(str(cell).strip() for cell in row):
            rows.append([str(cell).strip() for cell in row])
    return rows


def normalize_batch_rows_from_table(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    aliases = {
        "factorName": ["因子名", "因子中文名", "factorName", "factor_name"],
        "requirement": ["需求内容", "需求", "requirement"],
        "apiDocs": ["接口文档", "接口", "接口文档ID", "apiDocs", "apiDocIds", "api_names"],
        "paramFields": ["因子加工输入字段", "paramFields"],
        "procLogic": ["加工逻辑", "逻辑", "procLogic"],
        "draft": ["待评审内容", "准出内容", "reviewContent", "review_content", "draft"],
        "defaultValues": ["默认值", "defaultVal", "defaultValues"],
        "businessRuleIds": ["业务规则ID", "业务规则 ID", "businessRuleIds", "business_rule_ids"],
        "standardFile": ["标准文件ID", "标准文件", "standardFile", "standard_file"],
        "remark": ["备注", "remark"],
    }
    header = [str(cell).strip() for cell in rows[0]]
    col_map = {}
    for key, names in aliases.items():
        for name in names:
            if name in header:
                col_map[key] = header.index(name)
                break
    normalized = []
    for raw in rows[1:]:
        item = {}
        for key in aliases:
            index = col_map.get(key)
            item[key] = str(raw[index]).strip() if index is not None and index < len(raw) else ""
        if item.get("factorName") or item.get("requirement"):
            normalized.append(item)
    return normalized

def list_outputs():
    OUTPUTS_DIR.mkdir(exist_ok=True)
    files = []
    candidates = [
        path
        for path in OUTPUTS_DIR.iterdir()
        if path.is_file()
        and path.name.startswith(("standard-", "review-", "risk-review-"))
        and path.name.endswith((".md", ".json", ".txt"))
    ]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        files.append(
            {
                "name": path.name,
                "path": path.relative_to(ROOT).as_posix(),
                "mtime": int(path.stat().st_mtime),
                "size": path.stat().st_size,
            }
        )
    return files


def review_history_record_id(base: str) -> str:
    """Match the browser's unsigned 32-bit JavaScript hash for existing session IDs."""
    hash_value = 0
    encoded = str(base).encode("utf-16-be", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[index:index + 2], "big")
        hash_value = ((hash_value * 31) + code_unit) & 0xFFFFFFFF
    return f"session-{hash_value:08x}"


def factor_name_from_output_name(name: str) -> str:
    current = re.match(
        r"^(?:standard|review)-(.+?)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}-[a-z0-9]{8}(?:\.|$)",
        name,
        re.I,
    )
    if current:
        return current.group(1).replace("-", " ").strip()
    legacy = re.match(
        r"^(?:standard|review)-(.+)-\d{4}-\d{2}-\d{2}-[a-z0-9]{8}\.",
        name,
        re.I,
    )
    return legacy.group(1).replace("-", " ").strip() if legacy else name


def parse_review_history_artifact(name: str) -> dict | None:
    legacy = re.match(
        r"^(standard|review)-(.+)\.(standard|review)\.(md|json|raw\.txt|validation\.json)$",
        name,
        re.I,
    )
    if legacy and legacy.group(1).lower() == legacy.group(3).lower():
        extension = legacy.group(4).lower()
        return {
            "phase": legacy.group(1).lower(),
            "base": legacy.group(2),
            "kind": "raw" if extension == "raw.txt" else "validation" if extension == "validation.json" else extension,
        }
    current = re.match(
        r"^(standard|review)-(.+)\.(md|metadata\.json|raw\.txt|validation\.json)$",
        name,
        re.I,
    )
    if not current:
        return None
    extension = current.group(3).lower()
    return {
        "phase": current.group(1).lower(),
        "base": f"{current.group(1).lower()}-{current.group(2)}",
        "kind": (
            "json"
            if extension == "metadata.json"
            else "raw"
            if extension == "raw.txt"
            else "validation"
            if extension == "validation.json"
            else "md"
        ),
    }


def review_history_records() -> list[dict]:
    grouped: dict[str, dict] = {}
    created_at_numbers: dict[str, int] = {}
    for output in list_outputs():
        parsed = parse_review_history_artifact(output["name"])
        if not parsed:
            continue
        review_text = ""
        base = parsed["base"]
        if parsed["phase"] == "review" and parsed["kind"] == "md":
            try:
                review_text = read_text(OUTPUTS_DIR / output["name"])
                linked_standard = str(parse_frontmatter(review_text).get("standard_file") or "").strip()
                if linked_standard and Path(linked_standard).name == linked_standard:
                    base = Path(linked_standard).stem
            except OSError:
                review_text = ""
        record = grouped.get(base)
        if record is None:
            record = {
                "recordId": review_history_record_id(base),
                "factorName": factor_name_from_output_name(output["name"]),
                "createdAt": datetime.fromtimestamp(output.get("mtime") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": "validation" if "vrun-" in base else "single",
                "mode": "phase1_only",
                "conclusion": "unreviewable",
                "riskCount": 0,
                "standardArtifacts": {},
                "reviewArtifacts": {},
            }
            grouped[base] = record
            created_at_numbers[base] = int(output.get("mtime") or 0)
        if int(output.get("mtime") or 0) > created_at_numbers[base]:
            created_at_numbers[base] = int(output.get("mtime") or 0)
            record["createdAt"] = datetime.fromtimestamp(
                output.get("mtime") or 0,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        artifact_group = record["standardArtifacts"] if parsed["phase"] == "standard" else record["reviewArtifacts"]
        artifact_group[parsed["kind"]] = output["name"]
        if parsed["phase"] == "review" and parsed["kind"] == "md":
            try:
                review_text = review_text or read_text(OUTPUTS_DIR / output["name"])
                record["conclusion"] = detect_verdict_text(review_text)
                record["riskCount"] = int(collect_review_stats(review_text).get("fail") or 0)
            except OSError:
                record["conclusion"] = "unreviewable"
                record["riskCount"] = 0
        record["mode"] = (
            "one_click"
            if record["standardArtifacts"].get("md") and record["reviewArtifacts"].get("md")
            else "phase2_only"
            if record["reviewArtifacts"].get("md")
            else "phase1_only"
        )
    return sorted(grouped.values(), key=lambda item: str(item.get("createdAt") or ""), reverse=True)


def delete_review_history_record(record_id: str) -> dict:
    record = next(
        (item for item in review_history_records() if item.get("recordId") == record_id),
        None,
    )
    if not record:
        raise FileNotFoundError("评审历史记录不存在")
    file_names = {
        str(file_name)
        for group_name in ("standardArtifacts", "reviewArtifacts")
        for file_name in (record.get(group_name) or {}).values()
        if file_name
    }
    deleted_files = []
    for file_name in sorted(file_names):
        safe_name = Path(file_name)
        if safe_name.is_absolute() or len(safe_name.parts) != 1 or safe_name.name != file_name:
            raise ValueError(f"非法产物文件名：{file_name}")
        artifact_path = OUTPUTS_DIR / safe_name
        if artifact_path.exists() and artifact_path.is_file():
            artifact_path.unlink()
            deleted_files.append(file_name)
    return {"recordId": record_id, "deletedFiles": deleted_files}


def parse_examples():
    if EXAMPLES_PATH.exists():
        try:
            data = json.loads(read_text(EXAMPLES_PATH))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    examples = parse_examples_from_validation_set()
    save_examples(examples)
    return examples


def save_examples(examples: list[dict]):
    EXAMPLES_DIR.mkdir(exist_ok=True)
    atomic_write_text(EXAMPLES_PATH, json.dumps(examples, ensure_ascii=False, indent=2))


def parse_examples_from_validation_set():
    if not VALIDATION_SET_PATH.exists():
        return []
    text = read_text(VALIDATION_SET_PATH)
    examples = []
    pattern = re.compile(
        r"## Case (?P<num>\d{2}) (?P<kind>\S+).*?```text\n(?P<input>.*?)\n```.*?### 期望结论\n\n(?P<expected>.*?)(?=\n\n###|\Z)",
        re.S,
    )
    for match in pattern.finditer(text):
        raw_input = match.group("input").strip()
        examples.append(
            {
                "id": f"case-{match.group('num')}",
                "title": f"Case {match.group('num')} {match.group('kind')}",
                "kind": match.group("kind"),
                "expected": re.sub(r"\s+", " ", match.group("expected").strip()),
                **split_example_input(raw_input),
            }
        )
    return examples


def split_example_input(raw_input: str):
    def section(name: str):
        m = re.search(rf"【{re.escape(name)}】\s*(.*?)(?=\n【[^】]+】|\Z)", raw_input, re.S)
        return m.group(1).strip() if m else ""

    factor_name = section("因子名")
    api_line = section("接口文档")
    requirement = section("需求内容")
    draft_match = re.search(r"【待评审准出内容】\s*(.*)\Z", raw_input, re.S)
    draft = draft_match.group(1).strip() if draft_match else ""
    def draft_section(name: str):
        m = re.search(rf"【{re.escape(name)}】\s*(.*?)(?=\n【[^】]+】|\Z)", draft, re.S)
        return m.group(1).strip() if m else ""
    api_names = [x.strip() for x in re.split(r"[、,，\n]+", api_line) if x.strip()]
    return {
        "factorName": factor_name,
        "apiNames": api_names,
        "requirement": requirement,
        "paramFields": draft_section("因子加工输入字段"),
        "procLogic": draft_section("加工逻辑"),
        "defaultValues": draft_section("默认值"),
        "draft": draft,
    }


def example_version() -> str:
    return uuid.uuid4().hex[:12]


def validate_example(example: dict) -> list[str]:
    errors = []
    if not (example.get("title") or "").strip():
        errors.append("案例标题不能为空")
    if not (example.get("factorName") or "").strip():
        errors.append("因子名不能为空")
    if not (example.get("requirement") or "").strip():
        errors.append("需求内容不能为空")
    if not example.get("apiNames"):
        errors.append("接口文档不能为空")
    if not (example.get("draft") or "").strip():
        errors.append("待评审内容不能为空")
    return errors


def normalize_example(payload: dict, existing_id: str | None = None) -> dict:
    example_id = existing_id or (payload.get("id") or f"case-{uuid.uuid4().hex[:8]}")
    api_names = payload.get("apiNames") or []
    if isinstance(api_names, str):
        api_names = [x.strip() for x in re.split(r"[、,，\n]+", api_names) if x.strip()]
    else:
        api_names = [str(x).strip() for x in api_names if str(x).strip()]
    param_fields = (payload.get("paramFields") or "").strip()
    proc_logic = (payload.get("procLogic") or "").strip()
    default_values = "字段缺失、格式错误或数据异常时返回默认值"
    draft = (payload.get("draft") or "").strip()
    if not draft:
        draft_parts = []
        if param_fields:
            draft_parts.append(f"【因子加工输入字段】\n{param_fields}")
        if proc_logic:
            draft_parts.append(f"【加工逻辑】\n{proc_logic}")
        if draft_parts:
            draft_parts.append(f"【默认值】\n{default_values}")
        draft = "\n\n".join(draft_parts)
    return {
        "id": str(example_id),
        "title": (payload.get("title") or "").strip() or str(example_id),
        "kind": (payload.get("kind") or "").strip(),
        "factorName": (payload.get("factorName") or "").strip(),
        "apiNames": api_names,
        "requirement": (payload.get("requirement") or "").strip(),
        "paramFields": param_fields,
        "procLogic": proc_logic,
        "defaultValues": default_values,
        "draft": draft,
        "expected": (payload.get("expected") or "").strip(),
        "expectedIssuePatterns": _string_list(payload.get("expectedIssuePatterns")),
        "forbiddenIssuePatterns": _string_list(payload.get("forbiddenIssuePatterns")),
        "maxIssueCount": _optional_nonnegative_int(payload.get("maxIssueCount")),
        "version": example_version(),
        "updatedAt": int(time.time()),
    }


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,，、]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _optional_nonnegative_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def validation_expected_code(value: str | None) -> str:
    source = str(value or "").strip()
    if re.search(r"不通过|不符合|失败|fail", source, re.I):
        return "fail"
    if re.search(r"无法评审|unreviewable", source, re.I):
        return "unreviewable"
    if re.search(r"通过|符合|pass", source, re.I):
        return "pass"
    return source or "unreviewable"


def _iso_time(epoch_value=None) -> str:
    try:
        epoch = float(epoch_value) if epoch_value is not None else time.time()
    except (TypeError, ValueError):
        epoch = time.time()
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def parse_validation_runs() -> list[dict]:
    if not VALIDATION_RUNS_PATH.exists():
        return []
    try:
        data = json.loads(read_text(VALIDATION_RUNS_PATH))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_validation_runs(runs: list[dict]):
    EXAMPLES_DIR.mkdir(exist_ok=True)
    trimmed = sorted(
        runs,
        key=lambda item: item.get("finishedAt") or item.get("startedAt") or "",
        reverse=True,
    )[:1000]
    atomic_write_text(VALIDATION_RUNS_PATH, json.dumps(trimmed, ensure_ascii=False, indent=2))


def recover_stale_validation_runs():
    with VALIDATION_RUNS_LOCK:
        runs = parse_validation_runs()
        changed = False
        for run in runs:
            if run.get("status") not in ("pending", "running"):
                continue
            run.update({
                "status": "failed",
                "finishedAt": _iso_time(),
                "error": "后端服务已重启，原执行任务无法继续，请重新运行该案例",
            })
            changed = True
        if changed:
            save_validation_runs(runs)


def cancel_validation_runs_for_task(task_id: str):
    with VALIDATION_RUNS_LOCK:
        runs = parse_validation_runs()
        changed = False
        for run in runs:
            if run.get("taskId") != task_id or run.get("status") not in ("pending", "running"):
                continue
            run.update({
                "status": "cancelled",
                "finishedAt": _iso_time(),
                "error": "验证任务已终止",
            })
            changed = True
        if changed:
            save_validation_runs(runs)


def upsert_validation_run(run: dict) -> dict:
    with VALIDATION_RUNS_LOCK:
        runs = parse_validation_runs()
        run_id = str(run.get("runId") or "")
        for index, current in enumerate(runs):
            if current.get("runId") == run_id:
                runs[index] = {**current, **run}
                saved = runs[index]
                break
        else:
            runs.append(run)
            saved = run
        save_validation_runs(runs)
    return saved


def get_validation_run(run_id: str) -> dict | None:
    with VALIDATION_RUNS_LOCK:
        return next((item for item in parse_validation_runs() if item.get("runId") == run_id), None)


def latest_validation_runs_by_case() -> dict[str, dict]:
    latest = {}
    with VALIDATION_RUNS_LOCK:
        runs = parse_validation_runs()
    for run in runs:
        case_id = str(run.get("caseId") or "")
        if not case_id:
            continue
        current = latest.get(case_id)
        current_time = (current or {}).get("finishedAt") or (current or {}).get("startedAt") or ""
        candidate_time = run.get("finishedAt") or run.get("startedAt") or ""
        if current is None or candidate_time > current_time:
            latest[case_id] = run
    return latest


def parse_review_mode_experiments() -> list[dict]:
    if not REVIEW_MODE_EXPERIMENTS_PATH.exists():
        return []
    try:
        data = json.loads(read_text(REVIEW_MODE_EXPERIMENTS_PATH))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_review_mode_experiments(experiments: list[dict]):
    EXAMPLES_DIR.mkdir(exist_ok=True)
    trimmed = sorted(
        experiments,
        key=lambda item: item.get("createdAt") or "",
        reverse=True,
    )[:200]
    atomic_write_text(
        REVIEW_MODE_EXPERIMENTS_PATH,
        json.dumps(trimmed, ensure_ascii=False, indent=2),
    )


def upsert_review_mode_experiment(experiment: dict) -> dict:
    with REVIEW_MODE_EXPERIMENTS_LOCK:
        experiments = parse_review_mode_experiments()
        experiment_id = str(experiment.get("experimentId") or "")
        for index, current in enumerate(experiments):
            if current.get("experimentId") == experiment_id:
                experiments[index] = {**current, **experiment}
                saved = experiments[index]
                break
        else:
            experiments.append(experiment)
            saved = experiment
        save_review_mode_experiments(experiments)
    return saved


def get_review_mode_experiment(experiment_id: str) -> dict | None:
    with REVIEW_MODE_EXPERIMENTS_LOCK:
        return next(
            (
                item
                for item in parse_review_mode_experiments()
                if item.get("experimentId") == experiment_id
            ),
            None,
        )


def recover_stale_review_mode_experiments():
    with REVIEW_MODE_EXPERIMENTS_LOCK:
        experiments = parse_review_mode_experiments()
        changed = False
        for experiment in experiments:
            if experiment.get("status") not in ("queued", "running", "cancelling"):
                continue
            experiment.update({
                "status": "error",
                "finishedAt": _iso_time(),
                "error": "后端服务已重启，原对比实验无法继续，请重新执行",
            })
            changed = True
        if changed:
            save_review_mode_experiments(experiments)


def validation_case_from_example(example: dict, latest_run: dict | None = None) -> dict:
    tags = _string_list(example.get("tags"))
    kind = str(example.get("kind") or "").strip()
    if kind and kind not in tags:
        tags.insert(0, kind)
    created_at = example.get("createdAt") or example.get("updatedAt")
    result = {
        "caseId": str(example.get("id") or ""),
        "caseName": str(example.get("title") or example.get("id") or ""),
        "factorName": str(example.get("factorName") or ""),
        "requirement": str(example.get("requirement") or ""),
        "reviewContent": str(example.get("reviewContent") or example.get("draft") or example.get("procLogic") or ""),
        "expectedConclusion": validation_expected_code(example.get("expected")),
        "expectedIssuePatterns": _string_list(example.get("expectedIssuePatterns")),
        "forbiddenIssuePatterns": _string_list(example.get("forbiddenIssuePatterns")),
        "maxIssueCount": _optional_nonnegative_int(example.get("maxIssueCount")),
        "apiDocIds": _string_list(example.get("apiNames")),
        "businessRuleIds": _string_list(example.get("businessRuleIds")),
        "tags": tags,
        "remark": str(example.get("remark") or ""),
        "createdAt": _iso_time(created_at) if created_at else None,
        "updatedAt": _iso_time(example.get("updatedAt")) if example.get("updatedAt") else None,
    }
    if latest_run:
        result.update({
            "lastRunAt": latest_run.get("finishedAt") or latest_run.get("startedAt"),
            "lastActualConclusion": latest_run.get("actualConclusion"),
            "lastMatched": latest_run.get("matched"),
            "lastRunId": latest_run.get("runId"),
            "lastRunStatus": latest_run.get("status"),
        })
    return result


def validation_cases() -> list[dict]:
    latest_runs = latest_validation_runs_by_case()
    with EXAMPLES_LOCK:
        examples = parse_examples()
    return [validation_case_from_example(item, latest_runs.get(str(item.get("id") or ""))) for item in examples]


def normalize_validation_case_payload(payload: dict, existing: dict | None = None) -> dict:
    current = existing or {}
    review_content = (
        payload.get("reviewContent")
        if "reviewContent" in payload
        else current.get("reviewContent") or current.get("draft") or ""
    )
    api_doc_ids = payload.get("apiDocIds") if "apiDocIds" in payload else current.get("apiNames")
    expected = (
        payload.get("expectedConclusion")
        if "expectedConclusion" in payload
        else current.get("expected")
    )
    tags = _string_list(payload.get("tags") if "tags" in payload else current.get("tags"))
    kind = str(current.get("kind") or "").strip()
    if not kind and tags:
        kind = tags[0]
    normalized = normalize_example(
        {
            "id": current.get("id"),
            "title": payload.get("caseName") if "caseName" in payload else current.get("title"),
            "kind": kind,
            "factorName": payload.get("factorName") if "factorName" in payload else current.get("factorName"),
            "apiNames": api_doc_ids,
            "requirement": payload.get("requirement") if "requirement" in payload else current.get("requirement"),
            "paramFields": current.get("paramFields") or "",
            "procLogic": current.get("procLogic") or "",
            "draft": review_content,
            "expected": validation_expected_code(expected),
            "expectedIssuePatterns": (
                payload.get("expectedIssuePatterns")
                if "expectedIssuePatterns" in payload
                else current.get("expectedIssuePatterns")
            ),
            "forbiddenIssuePatterns": (
                payload.get("forbiddenIssuePatterns")
                if "forbiddenIssuePatterns" in payload
                else current.get("forbiddenIssuePatterns")
            ),
            "maxIssueCount": (
                payload.get("maxIssueCount")
                if "maxIssueCount" in payload
                else current.get("maxIssueCount")
            ),
        },
        existing_id=str(current.get("id")) if current.get("id") else None,
    )
    normalized.update({
        "businessRuleIds": _string_list(
            payload.get("businessRuleIds")
            if "businessRuleIds" in payload
            else current.get("businessRuleIds")
        ),
        "tags": tags,
        "remark": str(payload.get("remark") if "remark" in payload else current.get("remark") or "").strip(),
        "expectedIssuePatterns": _string_list(
            payload.get("expectedIssuePatterns")
            if "expectedIssuePatterns" in payload
            else current.get("expectedIssuePatterns")
        ),
        "forbiddenIssuePatterns": _string_list(
            payload.get("forbiddenIssuePatterns")
            if "forbiddenIssuePatterns" in payload
            else current.get("forbiddenIssuePatterns")
        ),
        "maxIssueCount": _optional_nonnegative_int(
            payload.get("maxIssueCount")
            if "maxIssueCount" in payload
            else current.get("maxIssueCount")
        ),
        "createdAt": current.get("createdAt") or int(time.time()),
    })
    return normalized


def validate_validation_case(example: dict) -> list[str]:
    return validate_example(example)


def import_validation_rows(file_name: str, rows: list[list]) -> dict:
    if not rows:
        return {"fileName": file_name, "totalRows": 0, "success": 0, "failed": 0, "items": [], "errors": [], "columns": []}
    columns = [str(cell or "").strip() for cell in rows[0]]
    aliases = {
        "caseName": ["案例名称", "案例名", "caseName", "title"],
        "factorName": ["因子名称", "因子名", "factorName"],
        "requirement": ["需求内容", "需求", "requirement"],
        "reviewContent": ["待评审内容", "准出内容", "reviewContent", "draft"],
        "expectedConclusion": ["期望结论", "expectedConclusion", "expected"],
        "apiDocIds": ["接口文档", "关联接口文档", "apiDocIds", "apiNames"],
        "businessRuleIds": ["业务规则ID", "业务规则 ID", "businessRuleIds"],
        "tags": ["标签", "tags"],
        "remark": ["备注", "remark"],
        "expectedIssuePatterns": ["期望问题关键词", "expectedIssuePatterns"],
        "forbiddenIssuePatterns": ["禁止问题关键词", "forbiddenIssuePatterns"],
        "maxIssueCount": ["最大问题数", "maxIssueCount"],
        "paramFields": ["因子加工输入字段", "paramFields"],
        "procLogic": ["加工逻辑", "procLogic"],
        "defaultValues": ["默认值", "defaultValues"],
    }
    index_map = {}
    for key, names in aliases.items():
        index = next((columns.index(name) for name in names if name in columns), None)
        if index is not None:
            index_map[key] = index

    items = []
    errors = []
    with EXAMPLES_LOCK:
        examples = parse_examples()
        for row_number, row in enumerate(rows[1:], start=2):
            values = {
                key: str(row[index] or "").strip() if index < len(row) else ""
                for key, index in index_map.items()
            }
            if not any(values.values()):
                continue
            review_content = values.get("reviewContent") or ""
            if not review_content and (values.get("paramFields") or values.get("procLogic")):
                parts = []
                if values.get("paramFields"):
                    parts.append(f"【因子加工输入字段】\n{values['paramFields']}")
                if values.get("procLogic"):
                    parts.append(f"【加工逻辑】\n{values['procLogic']}")
                parts.append("【默认值】\n字段缺失、格式错误或数据异常时返回默认值")
                review_content = "\n\n".join(parts)
            example = normalize_validation_case_payload({
                "caseName": values.get("caseName") or values.get("factorName"),
                "factorName": values.get("factorName"),
                "requirement": values.get("requirement"),
                "reviewContent": review_content,
                "expectedConclusion": values.get("expectedConclusion"),
                "apiDocIds": _string_list(values.get("apiDocIds")),
                "businessRuleIds": _string_list(values.get("businessRuleIds")),
                "tags": _string_list(values.get("tags")),
                "remark": values.get("remark"),
                "expectedIssuePatterns": _string_list(values.get("expectedIssuePatterns")),
                "forbiddenIssuePatterns": _string_list(values.get("forbiddenIssuePatterns")),
                "maxIssueCount": _optional_nonnegative_int(values.get("maxIssueCount")),
            })
            validation_errors = validate_validation_case(example)
            if validation_errors:
                errors.append({
                    "rowNumber": row_number,
                    "caseName": values.get("caseName"),
                    "factorName": values.get("factorName"),
                    "error": "；".join(validation_errors),
                })
                continue
            examples.append(example)
            items.append(validation_case_from_example(example))
        if items:
            save_examples(examples)
    return {
        "fileName": file_name,
        "totalRows": max(0, len(rows) - 1),
        "success": len(items),
        "failed": len(errors),
        "items": items,
        "errors": errors,
        "columns": columns,
    }


def decorate_task(task: dict) -> dict:
    snapshot = dict(task)
    task_id = str(snapshot.get("id") or "")
    snapshot.update(GLOBAL_TASK_POOL.group_info(task_id))
    job_info = GLOBAL_TASK_POOL.job_info(task_id)
    if job_info.get("poolStatus") is not None:
        snapshot.update(job_info)
    return snapshot


def task_history():
    gc_tasks()
    with TASK_LOCK:
        tasks = [decorate_task(task) for task in TASKS.values()]
    return sorted(tasks, key=lambda x: x.get("created", 0), reverse=True)[:50]


def task_log(task_id: str, message: str):
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.setdefault("logs", []).append({"time": time.time(), "message": message})
        task["updated"] = time.time()


def task_debug_log(task_id: str, stream: str, message: str):
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.setdefault("debugLogs", []).append({
            "time": time.time(),
            "stream": stream,
            "message": str(message or ""),
        })
        task["updated"] = time.time()


def task_cancel_requested(task_id: str) -> bool:
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


def terminate_task_process(task_id: str):
    with RUNNING_PROCS_LOCK:
        proc = RUNNING_PROCS.get(task_id)
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def request_task_cancel(task_id: str) -> bool:
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
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task and queued_single_cancelled:
            task["status"] = "cancelled"
            task["result"] = task.get("result") or {
                "stdout": "",
                "stderr": "任务在全局线程池排队期间被取消。",
                "returncode": 130,
            }
    for child_id in child_ids:
        terminate_task_process(child_id)
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
            f"已请求停止任务：取消 {len(cancelled_pool_jobs) + int(queued_single_cancelled)} 个排队作业，并中止正在执行的 Claude 调用",
        )
    return True


def claude_health():
    command = os.environ.get("RISK_REVIEW_CLAUDE_CMD", "claude")
    info = {
        "command": command,
        "claude_path": None,
        "version": None,
        "working_dir": str(ROOT),
        "mock_mode": os.environ.get("RISK_REVIEW_MOCK") == "1",
        "note": "",
    }
    try:
        path = shutil.which(command)
        info["claude_path"] = path
    except Exception as exc:
        info["note"] = f"find {command} failed: {exc}"
        return info
    try:
        info["version"] = subprocess.check_output([command, "--version"], text=True, stderr=subprocess.STDOUT).strip()
    except Exception as exc:
        info["note"] = f"{command} --version failed: {exc}"
    return info


def file_health(path: Path) -> dict:
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
    OUTPUTS_DIR.mkdir(exist_ok=True)
    probe = OUTPUTS_DIR / f".health-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"path": OUTPUTS_DIR.relative_to(ROOT).as_posix(), "exists": True, "writable": True, "note": ""}
    except Exception as exc:
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        return {"path": OUTPUTS_DIR.relative_to(ROOT).as_posix(), "exists": OUTPUTS_DIR.exists(), "writable": False, "note": str(exc)}


def api_docs_health() -> dict:
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
    required = [
        PHASE1_SKILL_PATH,
        PHASE2_SKILL_PATH,
        FACTOR_PROCESSING_SPEC_PATH,
        RULE_ENGINE_BUILD_PATH,
        RULE_ENGINE_PHASE1_OUTPUT_PATH,
        RULE_ENGINE_PHASE2_OUTPUT_PATH,
        STATIC_DIR / "index.html",
        STATIC_DIR / "app.js",
        STATIC_DIR / "style.css",
        STATIC_DIR / "dist" / "index.html",
        EXAMPLES_PATH,
        BUSINESS_RULES_PATH,
    ]
    files = {path.relative_to(ROOT).as_posix(): file_health(path) for path in required}
    claude = claude_health()
    checks = {
        "required_files": all(item["exists"] for item in files.values()),
        "outputs_writable": outputs_health()["writable"],
        "claude_available": bool(claude.get("claude_path")) or os.environ.get("RISK_REVIEW_MOCK") == "1",
        "api_docs_loaded": api_docs_health()["count"] > 0,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "working_dir": str(ROOT),
        "mock_mode": os.environ.get("RISK_REVIEW_MOCK") == "1",
        "claude": claude,
        "files": files,
        "outputs": outputs_health(),
        "api_docs": api_docs_health(),
        "task_pool": GLOBAL_TASK_POOL.snapshot(),
    }


def claude_cmd(default_args: str):
    command = os.environ.get("RISK_REVIEW_CLAUDE_CMD", "claude")
    args = os.environ.get("RISK_REVIEW_CLAUDE_ARGS", default_args)
    return [command, *shlex.split(args)]


def test_claude_print():
    cmd = claude_cmd("-p --output-format text")
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            input="只回复 OK",
            text=True,
            capture_output=True,
            timeout=int(os.environ.get("RISK_REVIEW_CLAUDE_TEST_TIMEOUT", "30")),
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "seconds": round(time.time() - started, 2),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "cmd": " ".join(cmd) + " < stdin",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": 124,
            "seconds": round(time.time() - started, 2),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "claude -p 测试超时",
            "cmd": " ".join(cmd) + " < stdin",
        }


def build_prompt_phase1(payload: dict) -> str:
    """Build prompt for Phase 1 only (standard spec, no draft)."""
    requirement = (payload.get("requirement") or "").strip()
    api_names = payload.get("apiNames") or []
    api_names = [str(x).strip() for x in api_names if str(x).strip()]
    return build_phase1_prompt(
        requirement=requirement,
        api_names=api_names,
    )


def build_prompt_phase2(payload: dict) -> str:
    """Build prompt for Phase 2 only (review against existing standard)."""
    draft = (payload.get("draft") or "").strip()
    phase2_context = payload.get("_phase2_context") or prepare_phase2_context(payload)
    return build_phase2_prompt(
        standard_text=phase2_context["standardText"],
        requirement=(payload.get("requirement") or "").strip(),
        draft=draft,
    )


def claude_failure_stderr(stdout: str, stderr: str, returncode: int | None) -> str:
    """Recover explicit CLI/gateway errors that Claude sometimes writes to stdout."""
    normalized_stderr = (stderr or "").strip()
    if normalized_stderr:
        return normalized_stderr
    if returncode in (None, 0):
        return ""
    for line in (stdout or "").splitlines():
        candidate = line.strip()
        if re.match(r"^(?:API Error|Error|错误)\s*[:：]", candidate, flags=re.IGNORECASE):
            return candidate
    return f"Claude Code 执行失败（退出码 {returncode}）"


def create_phase1_isolated_workspace(task_id: str, payload: dict) -> Path:
    workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase1-{task_id[:8]}-"))
    try:
        allowed_sources = [PHASE1_SKILL_PATH, FACTOR_PROCESSING_SPEC_PATH, BUSINESS_RULES_PATH]
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
    workspace = Path(tempfile.mkdtemp(prefix=f"risk-review-phase2-{task_id[:8]}-"))
    try:
        staged_files = []
        for source_path in (PHASE2_SKILL_PATH, FACTOR_PROCESSING_SPEC_PATH):
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


def isolated_read_tool_rules(workspace: Path) -> str:
    pattern = f"{workspace.resolve().as_posix()}/**"
    return ",".join([
        f"Read({pattern})",
        f"Glob({pattern})",
        f"Grep({pattern})",
    ])


def run_claude_prompt(
    task_id: str,
    phase_label: str,
    prompt: str,
    *,
    isolated_workspace: Path | None = None,
) -> tuple[str, dict]:
    cmd = [*claude_cmd("-p --output-format text")]
    command_cwd = ROOT
    if isolated_workspace is not None:
        command_cwd = isolated_workspace.resolve()
        cmd.extend([
            "--safe-mode",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "Read,Glob,Grep",
            "--allowedTools",
            isolated_read_tool_rules(command_cwd),
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
        ])
        task_log(
            task_id,
            f"{phase_label} 文件隔离已启用：仅允许在隔离目录内使用 Read/Glob/Grep",
        )
    task_log(task_id, f"调用 Claude Code（{phase_label}）：{' '.join(cmd)} < stdin")
    task_log(task_id, f"Prompt 字符数：{len(prompt)}")
    with TASK_LOCK:
        debug_enabled = bool((TASKS.get(task_id) or {}).get("debug_claude"))
    if debug_enabled:
        task_debug_log(task_id, "prompt", f"===== {phase_label} PROMPT =====\n{prompt}")
    timeout_seconds = int(os.environ.get("RISK_REVIEW_CLAUDE_TIMEOUT", "600"))
    popen_kwargs = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        if task_cancel_requested(task_id):
            return "cancelled", {
                "stdout": "",
                "stderr": f"{phase_label} 已取消。",
                "returncode": 130,
            }
        proc = subprocess.Popen(
            cmd,
            cwd=str(command_cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs,
        )
        with RUNNING_PROCS_LOCK:
            RUNNING_PROCS[task_id] = proc
        if task_cancel_requested(task_id):
            terminate_task_process(task_id)
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            terminate_task_process(task_id)
            stdout, stderr = proc.communicate(timeout=3)
            task_log(task_id, "Claude Code 调用超时")
            if debug_enabled:
                task_debug_log(task_id, "stdout", stdout or "")
                task_debug_log(task_id, "stderr", stderr or "Claude Code 调用超时。")
            return "timeout", {"stdout": stdout or "", "stderr": stderr or "Claude Code 调用超时。", "returncode": 124}
        if task_cancel_requested(task_id):
            task_log(task_id, "Claude Code 调用已取消")
            if debug_enabled:
                if stdout:
                    task_debug_log(task_id, "stdout", stdout)
                if stderr:
                    task_debug_log(task_id, "stderr", stderr)
            return "cancelled", {"stdout": stdout or "", "stderr": stderr or "Claude Code 调用已取消。", "returncode": proc.returncode if proc.returncode is not None else 130}
        task_log(task_id, f"Claude Code 返回，退出码：{proc.returncode}")
        if debug_enabled:
            task_debug_log(task_id, "stdout", stdout or "")
            task_debug_log(task_id, "stderr", stderr or "")
        normalized_stderr = claude_failure_stderr(stdout or "", stderr or "", proc.returncode)
        return ("done" if proc.returncode == 0 else "error"), {
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": normalized_stderr,
        }
    except subprocess.TimeoutExpired:
        task_log(task_id, "Claude Code 调用超时")
        return "timeout", {"stdout": "", "stderr": "Claude Code 调用超时。", "returncode": 124}
    except FileNotFoundError:
        task_log(task_id, "未找到 claude 命令")
        return "error", {"stdout": "", "stderr": "未找到 claude 命令。", "returncode": 127}
    except Exception as exc:
        task_log(task_id, f"Claude Code 调用异常：{exc}")
        return "error", {"stdout": "", "stderr": str(exc), "returncode": 1}
    finally:
        with RUNNING_PROCS_LOCK:
            RUNNING_PROCS.pop(task_id, None)


def prepare_phase2_context(payload: dict) -> dict:
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


def run_generation(
    *,
    task_id: str,
    phase_label: str,
    empty_error: str,
    prompt: str,
    isolated_workspace: Path | None = None,
) -> tuple[str, dict, str]:
    raw_output = ""
    if task_cancel_requested(task_id):
        return "cancelled", {"stdout": "", "stderr": f"{phase_label} 已取消。", "returncode": 130}, raw_output

    task_log(task_id, f"{phase_label} 开始生成")
    status, result = run_claude_prompt(
        task_id,
        phase_label,
        prompt,
        isolated_workspace=isolated_workspace,
    )
    if status != "done":
        return status, result, raw_output

    raw_output = (result.get("stdout") or "").strip()
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


def run_claude_phase1(payload: dict, task_id: str):
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

| 步骤 | 逻辑类型 | 标准逻辑 | 形成方式 | 选择理由 |
|---|---|---|---|---|
| 1 | `time_filter` | 将 `授信申请接口.applyTime` 转换为月份，以该月份减12个月为起点、该月份为终点，筛选 `外部接口.content[].mockDate` 位于闭区间内的记录 | `direct` | - |
| 2 | `aggregation` | 对筛选后记录的 `外部接口.content[].mockAmount` 求和 | `direct` | - |

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
            empty_error="Claude 未输出 Phase 1 标准加工规范正文",
            prompt=build_prompt_phase1(payload),
            isolated_workspace=isolated_workspace,
        )
    finally:
        shutil.rmtree(isolated_workspace, ignore_errors=True)
        task_log(task_id, "Phase 1 隔离工作目录已清理")

    if status == "done":
        if not raw_output:
            status = "error"
            result.update({"stderr": "Claude 未输出 Phase 1 标准加工规范正文", "returncode": 1})
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


def run_claude_phase2(payload: dict, task_id: str):
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
            empty_error="Claude 未输出 Phase 2 评审报告正文",
            prompt=build_prompt_phase2(payload),
            isolated_workspace=isolated_workspace,
        )
    finally:
        shutil.rmtree(isolated_workspace, ignore_errors=True)
        task_log(task_id, "Phase 2 隔离工作目录已清理")

    if status == "done":
        if not raw_output:
            status = "error"
            result.update({"stderr": "Claude 未输出 Phase 2 评审报告正文", "returncode": 1})
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
    return str(row.get("paramFields") or "").strip()


def detect_verdict_text(text: str) -> str:
    source = str(text or "")
    overall = re.search(r"\|\s*总体结论\s*\|\s*([^|]+?)\s*\|", source)
    target = overall.group(1) if overall else source
    if re.search(r"❌\s*不通过", target):
        return "fail"
    if re.search(r"⛔\s*无法评审|无法评审", target):
        return "unreviewable"
    if re.search(r"✅\s*(通过|符合)", target):
        return "pass"
    return "unreviewable"


def collect_review_stats(text: str) -> dict:
    source = str(text or "")
    stats_match = re.search(r"\|\s*问题统计\s*\|\s*([^|]+?)\s*\|", source)
    if stats_match:
        stats_text = stats_match.group(1)
        fail_match = re.search(r"不符合\s*(\d+)\s*项", stats_text)
        if fail_match:
            return {"fail": int(fail_match.group(1))}
    issue_rows = re.findall(r"^\|\s*I-\d+\s*\|", source, re.M)
    return {"fail": len(issue_rows)}


def evaluate_issue_expectations(example: dict, report: str) -> dict:
    expected_patterns = _string_list(example.get("expectedIssuePatterns"))
    forbidden_patterns = _string_list(example.get("forbiddenIssuePatterns"))
    max_issue_count = _optional_nonnegative_int(example.get("maxIssueCount"))
    source = str(report or "").casefold()
    missing_patterns = [pattern for pattern in expected_patterns if pattern.casefold() not in source]
    forbidden_hits = [pattern for pattern in forbidden_patterns if pattern.casefold() in source]
    stats = collect_review_stats(report)
    issue_count = int(stats.get("fail") or 0)
    count_matched = max_issue_count is None or issue_count <= max_issue_count
    configured = bool(expected_patterns or forbidden_patterns or max_issue_count is not None)
    return {
        "configured": configured,
        "matched": not missing_patterns and not forbidden_hits and count_matched,
        "expectedIssuePatterns": expected_patterns,
        "forbiddenIssuePatterns": forbidden_patterns,
        "missingIssuePatterns": missing_patterns,
        "forbiddenIssueHits": forbidden_hits,
        "maxIssueCount": max_issue_count,
        "actualIssueCount": issue_count,
    }


def expected_matches_verdict(example: dict, verdict: str) -> bool:
    expected = str(example.get("expected") or "")
    if not expected.strip():
        return True
    expected_code = validation_expected_code(expected)
    if expected_code == "fail":
        return verdict == "fail"
    if expected_code == "unreviewable":
        return verdict == "unreviewable"
    if expected_code == "pass":
        return verdict == "pass"
    return True


def validation_run_for_example(
    task_id: str,
    example: dict,
    *,
    run_id: str | None = None,
    status: str = "pending",
) -> dict:
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
    run_ids = {}
    for example in examples:
        run = validation_run_for_example(task_id, example)
        upsert_validation_run(run)
        run_ids[str(example.get("id") or "")] = run["runId"]
    return run_ids


def run_regression_validation(payload: dict, task_id: str):
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
                item.update({"status": "skip", "raw": "已停止：当前 Claude 调用已中止", "reportPreview": "已停止：当前 Claude 调用已中止", "time": int(time.time() * 1000)})
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
    expected = validation_expected_code(example.get("expected"))
    expected_patterns = _string_list(example.get("expectedIssuePatterns"))
    if expected == "fail":
        issue = expected_patterns[0] if expected_patterns else "加工逻辑与需求存在明确不一致"
        conclusion = "❌ 不通过，需修改"
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
            status, result = run_claude_prompt(
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
    report = str(result.get("stdout") or "").strip()
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
    debug_claude: bool = False,
    *,
    task_type: str = "child",
    parent_task_id: str | None = None,
    client_id: str = "",
    group_concurrency: int | None = None,
):
    with TASK_LOCK:
        parent_cancelled = bool(
            parent_task_id
            and (TASKS.get(parent_task_id) or {}).get("cancel_requested")
        )
        TASKS[task_id] = {
            "id": task_id,
            "taskType": task_type,
            "parentTaskId": parent_task_id,
            "clientId": client_id,
            "groupConcurrency": group_concurrency,
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
            "debug_claude": bool(debug_claude),
            "created": time.time(),
            "updated": time.time(),
        }
    if len(TASKS) % 16 == 0:
        gc_tasks()


def submit_regression_task(payload: dict, examples: list[dict]) -> tuple[str, dict, dict[str, str]]:
    task_id = str(uuid.uuid4())
    create_task_record(
        task_id,
        task_type="regression",
        client_id=str(payload.get("clientId") or ""),
        group_concurrency=1,
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
    GLOBAL_TASK_POOL.submit(
        task_id,
        task_id,
        run_regression_validation,
        task_payload,
        task_id,
        group_limit=1,
    )
    with TASK_LOCK:
        task = decorate_task(TASKS[task_id])
    return task_id, task, run_ids


def submit_review_mode_experiment(payload: dict, examples: list[dict]) -> tuple[str, dict]:
    task_id = str(uuid.uuid4())
    experiment_id = f"experiment-{task_output_token(task_id)}"
    repetitions = max(1, min(3, int(payload.get("repetitions") or 1)))
    create_task_record(
        task_id,
        task_type="review-mode-experiment",
        client_id=str(payload.get("clientId") or ""),
        group_concurrency=1,
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
    GLOBAL_TASK_POOL.submit(
        task_id,
        task_id,
        run_review_mode_experiment,
        task_payload,
        task_id,
        group_limit=1,
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
        debug_claude=bool(phase1_payload.get("debugClaude")),
        parent_task_id=parent_task_id,
    )
    run_claude_phase1(phase1_payload, phase1_task_id)
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
        "debugClaude": phase1_payload.get("debugClaude"),
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
        debug_claude=bool(phase2_payload.get("debugClaude")),
        parent_task_id=parent_task_id,
    )
    run_claude_phase2(phase2_payload, phase2_task_id)
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
            item.update({"status": "done", "raw": pipeline["raw"], "reviewFileId": pipeline.get("reviewFileId")})
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
    with TASK_LOCK:
        if task_id in TASKS and index < len(items):
            items[index]["status"] = "running"
            TASKS[task_id]["updated"] = time.time()
    return run_review_pair(row, payload, task_id, index)


def finish_batch_factor(task_id: str, index: int, items: list[dict], future):
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
    create_task_record(task_id, task_type="scheduled-phase1", parent_task_id=task_id.rsplit("-p1-", 1)[0])
    run_claude_phase1(payload, task_id)
    with TASK_LOCK:
        task = TASKS.get(task_id, {})
        return {"status": task.get("status"), "result": dict(task.get("result") or {})}


def run_scheduled_phase2(payload: dict, task_id: str) -> dict:
    create_task_record(task_id, task_type="scheduled-phase2", parent_task_id=task_id.rsplit("-p2-", 1)[0])
    run_claude_phase2(payload, task_id)
    with TASK_LOCK:
        task = TASKS.get(task_id, {})
        return {"status": task.get("status"), "result": dict(task.get("result") or {})}


def get_scheduled_task_service():
    global SCHEDULED_TASK_SERVICE
    if SCHEDULED_TASK_SERVICE is None:
        SCHEDULED_TASK_SERVICE = build_scheduled_task_service(
            run_review_pair=run_review_pair,
            run_phase1=run_scheduled_phase1,
            run_phase2=run_scheduled_phase2,
            cancel_execution=request_task_cancel,
            log=lambda message: print(f"[scheduled-tasks] {message}", flush=True),
        )
    return SCHEDULED_TASK_SERVICE


def start_batch_review(payload: dict, task_id: str):
    rows = payload.get("rows") or []
    concurrency = max(1, min(GLOBAL_TASK_POOL.max_workers, int(payload.get("concurrency") or 1)))
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
    def log_message(self, fmt, *args):
        return

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8")) if body else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            react_index = STATIC_DIR / "dist" / "index.html"
            self.serve_static("dist/index.html" if react_index.exists() else "index.html")
        elif path in ("/legacy", "/legacy/"):
            self.serve_static("index.html")
        elif path == "/favicon.ico":
            text_response(self, 204, b"", "image/x-icon")
        elif path == "/api/apis":
            json_response(self, 200, {"apis": list_api_docs(include_content=False)})
        elif path == "/api/api-doc":
            query = parse_qs(parsed.query)
            doc = get_api_doc((query.get("name") or query.get("path") or [""])[0])
            if not doc:
                json_response(self, 404, {"error": "api doc not found"})
                return
            json_response(self, 200, {"api": doc})
        elif path == "/api/outputs":
            json_response(self, 200, {"outputs": list_outputs()})
        elif path == "/api/review-history":
            success_response(self, review_history_records())
        elif path == "/api/validation-cases":
            success_response(self, validation_cases())
        elif path.startswith("/api/validation-cases/"):
            case_id = unquote(path.split("/")[-1])
            latest_runs = latest_validation_runs_by_case()
            with EXAMPLES_LOCK:
                example = next((item for item in parse_examples() if item.get("id") == case_id), None)
            if not example:
                error_response(self, 404, "验证案例不存在")
                return
            success_response(self, validation_case_from_example(example, latest_runs.get(case_id)))
        elif path == "/api/validation-runs":
            case_id = (parse_qs(parsed.query).get("caseId") or [""])[0]
            with VALIDATION_RUNS_LOCK:
                runs = parse_validation_runs()
            if case_id:
                runs = [item for item in runs if item.get("caseId") == case_id]
            runs.sort(key=lambda item: item.get("finishedAt") or item.get("startedAt") or "", reverse=True)
            success_response(self, runs)
        elif path.startswith("/api/validation-runs/"):
            run_id = unquote(path.split("/")[-1])
            run = get_validation_run(run_id)
            if not run:
                error_response(self, 404, "验证运行记录不存在")
                return
            success_response(self, run)
        elif path == "/api/review-mode-experiments":
            with REVIEW_MODE_EXPERIMENTS_LOCK:
                experiments = parse_review_mode_experiments()
            experiments.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
            success_response(self, experiments)
        elif path.startswith("/api/review-mode-experiments/"):
            experiment_id = unquote(path.split("/")[-1])
            experiment = get_review_mode_experiment(experiment_id)
            if not experiment:
                error_response(self, 404, "对比实验不存在")
                return
            success_response(self, experiment)
        elif path == "/api/examples":
            json_response(self, 200, {"examples": parse_examples()})
        elif path == "/api/business-rules":
            json_response(self, 200, {"rules": parse_business_rules()})
        elif path == "/api/tasks":
            json_response(self, 200, {"tasks": task_history(), "pool": GLOBAL_TASK_POOL.snapshot()})
        elif path == "/api/pool/status":
            json_response(self, 200, {"pool": GLOBAL_TASK_POOL.snapshot()})
        elif path in ("/api/health", "/api/self-check"):
            json_response(self, 200, system_health())
        elif path == "/api/claude-test":
            json_response(self, 200, test_claude_print())
        elif path == "/api/scheduled-tasks":
            success_response(self, get_scheduled_task_service().list_tasks())
        elif path.startswith("/api/scheduled-tasks/") and path.endswith("/runs"):
            task_id = unquote(path.split("/")[-2])
            success_response(self, get_scheduled_task_service().list_runs(task_id))
        elif path.startswith("/api/scheduled-tasks/"):
            task_id = unquote(path.split("/")[-1])
            try:
                success_response(self, get_scheduled_task_service().get_task(task_id))
            except ScheduledTaskError as exc:
                error_response(self, 404, str(exc))
        elif path.startswith("/api/scheduled-runs/"):
            run_id = unquote(path.split("/")[-1])
            try:
                success_response(self, get_scheduled_task_service().get_run(run_id))
            except ScheduledTaskError as exc:
                error_response(self, 404, str(exc))
        elif path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            with TASK_LOCK:
                task = TASKS.get(task_id)
            if not task:
                json_response(self, 404, {"error": "not found"})
                return
            json_response(self, 200, {"task": decorate_task(task), "pool": GLOBAL_TASK_POOL.snapshot()})
        elif path.startswith("/outputs/"):
            self.serve_output(path[len("/outputs/") :])
        else:
            self.serve_static(path.lstrip("/"))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/scheduled-tasks/import-preview":
            try:
                payload = self.read_json_body()
                result = get_scheduled_task_service().preview_import(
                    payload.get("name", ""),
                    payload.get("data", ""),
                    mode=payload.get("mode"),
                    task_standard_file=payload.get("standardFile"),
                )
                success_response(self, result)
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except ScheduledTaskError as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path == "/api/scheduled-tasks":
            try:
                success_response(self, get_scheduled_task_service().create_task(self.read_json_body()), status=201)
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except (ScheduledTaskError, TypeError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path.startswith("/api/scheduled-tasks/") and parsed.path.endswith("/enable"):
            task_id = unquote(parsed.path.split("/")[-2])
            try:
                success_response(self, get_scheduled_task_service().set_enabled(task_id, True))
            except ScheduledTaskError as exc:
                error_response(self, 404 if "不存在" in str(exc) else 400, str(exc))
            return
        if parsed.path.startswith("/api/scheduled-tasks/") and parsed.path.endswith("/disable"):
            task_id = unquote(parsed.path.split("/")[-2])
            try:
                success_response(self, get_scheduled_task_service().set_enabled(task_id, False))
            except ScheduledTaskError as exc:
                error_response(self, 404 if "不存在" in str(exc) else 400, str(exc))
            return
        if parsed.path.startswith("/api/scheduled-tasks/") and parsed.path.endswith("/run-now"):
            task_id = unquote(parsed.path.split("/")[-2])
            try:
                success_response(self, get_scheduled_task_service().submit_task_run(task_id, trigger="manual"), status=202)
            except ScheduledTaskError as exc:
                error_response(self, 404 if "不存在" in str(exc) else 409, str(exc))
            return
        if parsed.path.startswith("/api/scheduled-runs/") and parsed.path.endswith("/cancel"):
            run_id = unquote(parsed.path.split("/")[-2])
            try:
                success_response(self, get_scheduled_task_service().cancel_run(run_id))
            except ScheduledTaskError as exc:
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
        if parsed.path == "/api/validation-cases":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
                return
            with EXAMPLES_LOCK:
                examples = parse_examples()
                example = normalize_validation_case_payload(payload)
                errors = validate_validation_case(example)
                if errors:
                    error_response(self, 400, "；".join(errors), errors)
                    return
                examples.append(example)
                save_examples(examples)
            success_response(self, validation_case_from_example(example), status=201)
            return
        if parsed.path == "/api/validation-cases/import":
            try:
                payload = self.read_json_body()
                file_name = str(payload.get("name") or "validation-cases.csv")
                encoded = str(payload.get("data") or "")
                if not encoded:
                    raise ValueError("导入文件内容为空")
                raw = base64.b64decode(encoded)
                if file_name.lower().endswith((".xlsx", ".xls")):
                    rows = parse_xlsx(raw)
                else:
                    try:
                        rows = parse_csv_rows(raw.decode("utf-8-sig"))
                    except UnicodeDecodeError:
                        rows = parse_csv_rows(raw.decode("gb18030"))
                success_response(self, import_validation_rows(file_name, rows))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except (ValueError, TypeError, binascii.Error) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path == "/api/validation-cases/batch-delete":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
                return
            case_ids = [str(item) for item in payload.get("caseIds") or [] if str(item).strip()]
            selected = set(case_ids)
            with EXAMPLES_LOCK:
                examples = parse_examples()
                existing_ids = {str(item.get("id") or "") for item in examples}
                save_examples([item for item in examples if str(item.get("id") or "") not in selected])
            items = [
                {"id": case_id, "success": case_id in existing_ids, **({} if case_id in existing_ids else {"error": "验证案例不存在"})}
                for case_id in case_ids
            ]
            success_response(self, {
                "total": len(items),
                "success": len([item for item in items if item["success"]]),
                "failed": len([item for item in items if not item["success"]]),
                "items": items,
            })
            return
        if parsed.path == "/api/validation-cases/batch-run":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
                return
            selected = {str(item) for item in payload.get("caseIds") or [] if str(item).strip()}
            with EXAMPLES_LOCK:
                examples = [item for item in parse_examples() if str(item.get("id") or "") in selected]
            if not examples:
                error_response(self, 400, "没有可验证的案例")
                return
            task_id, _task, run_ids = submit_regression_task(payload, examples)
            items = []
            for example in examples:
                case_id = str(example.get("id") or "")
                items.append({
                    "id": case_id,
                    "success": True,
                    "run": get_validation_run(run_ids[case_id]),
                })
            success_response(self, {
                "groupId": task_id,
                "total": len(items),
                "success": len(items),
                "failed": 0,
                "unreviewable": 0,
                "mismatched": 0,
                "items": items,
            }, status=202)
            return
        if parsed.path == "/api/review-mode-experiments":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
                return
            selected = {
                str(item)
                for item in payload.get("caseIds") or []
                if str(item).strip()
            }
            with EXAMPLES_LOCK:
                examples = [
                    item for item in parse_examples()
                    if str(item.get("id") or "") in selected
                ]
            if not examples:
                error_response(self, 400, "请至少选择一个验证案例")
                return
            try:
                experiment_id, task = submit_review_mode_experiment(payload, examples)
            except (TypeError, ValueError) as exc:
                error_response(self, 400, str(exc))
                return
            success_response(self, {
                "experimentId": experiment_id,
                "taskId": task.get("id"),
                "task": task,
            }, status=202)
            return
        if parsed.path.startswith("/api/validation-cases/") and parsed.path.endswith("/run"):
            case_id = unquote(parsed.path.split("/")[-2])
            with EXAMPLES_LOCK:
                example = next((item for item in parse_examples() if item.get("id") == case_id), None)
            if not example:
                error_response(self, 404, "验证案例不存在")
                return
            payload = {"exampleIds": [case_id]}
            task_id, _task, run_ids = submit_regression_task(payload, [example])
            run = get_validation_run(run_ids[case_id]) or validation_run_for_example(task_id, example)
            success_response(self, run, status=202)
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
            concurrency = max(1, min(GLOBAL_TASK_POOL.max_workers, int(payload.get("concurrency") or 1)))
            create_task_record(
                task_id,
                task_type="batch",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=concurrency,
            )
            with TASK_LOCK:
                TASKS[task_id]["result"] = {"items": []}
            start_batch_review(payload, task_id)
            with TASK_LOCK:
                task = decorate_task(TASKS[task_id])
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        if parsed.path == "/api/validation/regression":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            examples = parse_examples()
            selected_ids = payload.get("exampleIds") or []
            if selected_ids:
                selected = {str(x) for x in selected_ids}
                examples = [item for item in examples if item.get("id") in selected]
            if not examples:
                json_response(self, 400, {"error": "没有可验证的用例。"})
                return
            task_id, task, _run_ids = submit_regression_task(payload, examples)
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
            task_id = str(uuid.uuid4())
            create_task_record(
                task_id,
                debug_claude=bool(payload.get("debugClaude")),
                task_type="phase1",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=1,
            )
            GLOBAL_TASK_POOL.submit(task_id, task_id, run_claude_phase1, payload, task_id, group_limit=1)
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
            task_id = str(uuid.uuid4())
            create_task_record(
                task_id,
                debug_claude=bool(payload.get("debugClaude")),
                task_type="phase2",
                client_id=str(payload.get("clientId") or ""),
                group_concurrency=1,
            )
            GLOBAL_TASK_POOL.submit(task_id, task_id, run_claude_phase2, payload, task_id, group_limit=1)
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
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/validation-cases/"):
            case_id = unquote(parsed.path.split("/")[-1])
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
                return
            with EXAMPLES_LOCK:
                examples = parse_examples()
                for index, item in enumerate(examples):
                    if item.get("id") != case_id:
                        continue
                    updated = normalize_validation_case_payload(payload, item)
                    errors = validate_validation_case(updated)
                    if errors:
                        error_response(self, 400, "；".join(errors), errors)
                        return
                    examples[index] = updated
                    save_examples(examples)
                    success_response(self, validation_case_from_example(updated, latest_validation_runs_by_case().get(case_id)))
                    return
            error_response(self, 404, "验证案例不存在")
            return
        if parsed.path.startswith("/api/scheduled-tasks/"):
            task_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, get_scheduled_task_service().update_task(task_id, self.read_json_body()))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except (ScheduledTaskError, TypeError, ValueError) as exc:
                error_response(self, 404 if "不存在" in str(exc) else 400, str(exc))
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
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/review-history/"):
            record_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, delete_review_history_record(record_id))
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except (OSError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        if parsed.path.startswith("/api/validation-cases/"):
            case_id = unquote(parsed.path.split("/")[-1])
            with EXAMPLES_LOCK:
                examples = parse_examples()
                filtered = [item for item in examples if item.get("id") != case_id]
                if len(filtered) == len(examples):
                    error_response(self, 404, "验证案例不存在")
                    return
                save_examples(filtered)
            success_response(self, None)
            return
        if parsed.path.startswith("/api/scheduled-tasks/"):
            task_id = unquote(parsed.path.split("/")[-1])
            try:
                get_scheduled_task_service().delete_task(task_id)
                success_response(self, None)
            except ScheduledTaskError as exc:
                error_response(self, 404, str(exc))
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

    def serve_static(self, name: str):
        name = name or "index.html"
        safe = Path(name)
        if safe.is_absolute() or ".." in safe.parts:
            json_response(self, 403, {"error": "forbidden"})
            return
        path = STATIC_DIR / safe
        if not path.exists() or not path.is_file():
            json_response(self, 404, {"error": "not found"})
            return
        ctype = "text/html; charset=utf-8"
        if path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".svg":
            ctype = "image/svg+xml"
        text_response(self, 200, path.read_bytes(), ctype)

    def serve_output(self, name: str):
        safe = Path(name)
        if safe.is_absolute() or ".." in safe.parts:
            json_response(self, 403, {"error": "forbidden"})
            return
        path = OUTPUTS_DIR / safe
        if not path.exists() or not path.is_file():
            json_response(self, 404, {"error": "not found"})
            return
        text_response(self, 200, path.read_bytes(), "text/markdown; charset=utf-8")


def main():
    recover_stale_validation_runs()
    recover_stale_review_mode_experiments()
    scheduled_service = get_scheduled_task_service()
    scheduled_service.start_scheduler()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Risk review web running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    finally:
        scheduled_service.stop_scheduler()
        server.server_close()


if __name__ == "__main__":
    main()
