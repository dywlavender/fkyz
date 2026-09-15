from __future__ import annotations

import json
import re
import threading
import time
import uuid

from risk_review_web.paths import (
    DATA_DIR,
    EXAMPLES_PATH,
    REVIEW_MODE_EXPERIMENTS_PATH,
    resolve_validation_set_path,
)
from risk_review_web.repositories.runtime_database import RuntimeDatabase, get_runtime_database
from risk_review_web.services.review_evaluation import (
    optional_nonnegative_int as _optional_nonnegative_int,
    string_list as _string_list,
    validation_expected_code,
)
from risk_review_web.services.time_utils import iso_time as _iso_time
from risk_review_web.utils import atomic_write_text, read_text


EXAMPLES_LOCK = threading.Lock()
VALIDATION_RUNS_LOCK = threading.Lock()
REVIEW_MODE_EXPERIMENTS_LOCK = threading.Lock()
VALIDATION_SET_PATH = resolve_validation_set_path()
RUNTIME_DATABASE: RuntimeDatabase = get_runtime_database()


def parse_examples():
    """Load validation cases, seeding them from the legacy Markdown set when absent."""
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
    """Persist the complete validation-case collection atomically."""
    DATA_DIR.mkdir(exist_ok=True)
    atomic_write_text(EXAMPLES_PATH, json.dumps(examples, ensure_ascii=False, indent=2))


def parse_examples_from_validation_set():
    """Parse legacy Markdown validation cases into the current case schema."""
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
    """Split a legacy case text block into requirement, API, and draft sections."""
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
    """Generate an optimistic-lock version token for a validation case."""
    return uuid.uuid4().hex[:12]


def validate_example(example: dict) -> list[str]:
    """Return all user-correctable validation errors for a case payload."""
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
    expected = str(example.get("expected") or "").strip()
    if expected and validation_expected_code(expected) not in {"pass", "fail", "unreviewable"}:
        errors.append("期望结论无法识别，请填写通过、不通过或无法评审")
    return errors


def normalize_example(payload: dict, existing_id: str | None = None) -> dict:
    """Normalize legacy and current case payloads into the persisted schema."""
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



def parse_validation_runs() -> list[dict]:
    """Load persisted validation runs, treating malformed state as empty."""
    return RUNTIME_DATABASE.list_validation_runs()


def query_validation_run_groups(query: dict[str, list[str]]) -> dict:
    """Group validation runs by batch and apply server-side pagination."""
    try:
        page = max(1, int((query.get("page") or ["1"])[0]))
        page_size = max(1, min(100, int((query.get("pageSize") or ["20"])[0])))
    except ValueError:
        page, page_size = 1, 20
    return RUNTIME_DATABASE.query_validation_groups(page=page, page_size=page_size)


def save_validation_runs(runs: list[dict]):
    """Persist at most the newest 1,000 validation-run records."""
    RUNTIME_DATABASE.replace_validation_runs(runs, max_count=1000)


def batch_delete_validation_run_groups(group_ids: list[str]) -> dict:
    """Delete selected terminal run groups while protecting active executions."""
    ordered_group_ids = list(dict.fromkeys(str(item).strip() for item in group_ids if str(item).strip()))
    if not ordered_group_ids:
        return {"total": 0, "success": 0, "failed": 0, "items": []}
    with VALIDATION_RUNS_LOCK:
        grouped = {
            group_id: RUNTIME_DATABASE.list_validation_runs(group_id=group_id)
            for group_id in ordered_group_ids
        }
        removable: set[str] = set()
        items: list[dict] = []
        for group_id in ordered_group_ids:
            group_runs = grouped.get(group_id) or []
            if not group_runs:
                items.append({"id": group_id, "success": False, "error": "验证批次不存在"})
                continue
            if any(
                str(run.get("status") or "") in {"pending", "queued", "running", "cancelling"}
                for run in group_runs
            ):
                items.append({"id": group_id, "success": False, "error": "执行中的验证批次不能删除"})
                continue
            removable.add(group_id)
            items.append({"id": group_id, "success": True})
        RUNTIME_DATABASE.delete_validation_groups(removable)

    success_count = sum(1 for item in items if item["success"])
    return {
        "total": len(items),
        "success": success_count,
        "failed": len(items) - success_count,
        "items": items,
    }


def recover_stale_validation_runs():
    """Close runs left active by a previous backend process."""
    with VALIDATION_RUNS_LOCK:
        RUNTIME_DATABASE.update_validation_runs(
            "status IN ('pending', 'running')",
            (),
            lambda run: {
                **run,
                "status": "failed",
                "finishedAt": _iso_time(),
                "error": "后端服务已重启，原执行任务无法继续，请重新运行该案例",
            },
        )


def cancel_validation_runs_for_task(task_id: str):
    """Mark all unfinished validation runs owned by a cancelled task."""
    with VALIDATION_RUNS_LOCK:
        RUNTIME_DATABASE.update_validation_runs(
            "task_id = ? AND status IN ('pending', 'running')",
            (str(task_id),),
            lambda run: {
                **run,
                "status": "cancelled",
                "finishedAt": _iso_time(),
                "error": "验证任务已终止",
            },
        )


def upsert_validation_run(run: dict) -> dict:
    """Insert or merge a validation run under the shared persistence lock."""
    with VALIDATION_RUNS_LOCK:
        current = RUNTIME_DATABASE.get_validation_run(str(run.get("runId") or ""))
        saved = {**current, **run} if current else dict(run)
        RUNTIME_DATABASE.upsert_validation_run(saved)
    return saved


def get_validation_run(run_id: str) -> dict | None:
    """Return one persisted validation run by identifier."""
    with VALIDATION_RUNS_LOCK:
        return RUNTIME_DATABASE.get_validation_run(run_id)


def latest_validation_runs_by_case() -> dict[str, dict]:
    """Index the latest known run for each validation case."""
    latest = {}
    with VALIDATION_RUNS_LOCK:
        runs = RUNTIME_DATABASE.list_validation_runs(latest_by_case=True)
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
    """Load persisted review-mode comparison experiments."""
    if not REVIEW_MODE_EXPERIMENTS_PATH.exists():
        return []
    try:
        data = json.loads(read_text(REVIEW_MODE_EXPERIMENTS_PATH))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_review_mode_experiments(experiments: list[dict]):
    """Persist at most the newest 200 review-mode experiments."""
    REVIEW_MODE_EXPERIMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    """Insert or merge a review-mode experiment atomically."""
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
    """Return one persisted review-mode experiment by identifier."""
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
    """Fail experiments that cannot resume after a backend restart."""
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
    """Project a persisted example into the validation-page API schema."""
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
    """Return all cases enriched with their most recent execution state."""
    latest_runs = latest_validation_runs_by_case()
    with EXAMPLES_LOCK:
        examples = parse_examples()
    return [validation_case_from_example(item, latest_runs.get(str(item.get("id") or ""))) for item in examples]


def normalize_validation_case_payload(payload: dict, existing: dict | None = None) -> dict:
    """Merge a validation-page payload with existing persisted case data."""
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
    """Validate a normalized validation-page case payload."""
    return validate_example(example)


def import_validation_rows(file_name: str, rows: list[list]) -> dict:
    """Import tabular validation cases and report row-level successes and errors."""
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
