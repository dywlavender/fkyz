from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs, unquote

from risk_review_web.services.http_responses import error_response, json_response, success_response
from risk_review_web.services.tabular_import import TabularImportError, decode_import_rows
from risk_review_web.services.validation_store import (
    EXAMPLES_LOCK,
    REVIEW_MODE_EXPERIMENTS_LOCK,
    VALIDATION_RUNS_LOCK,
    batch_delete_validation_run_groups,
    get_review_mode_experiment,
    get_validation_run,
    import_validation_rows,
    latest_validation_runs_by_case,
    normalize_validation_case_payload,
    parse_examples,
    parse_review_mode_experiments,
    parse_validation_runs,
    query_validation_run_groups,
    save_examples,
    validate_validation_case,
    validation_case_from_example,
    validation_cases,
)


def handle_get(handler, path: str, query_string: str) -> bool:
    """Handle validation cases, runs, groups, and comparison-experiment reads."""
    if path == "/api/validation-cases":
        success_response(handler, validation_cases())
        return True
    if path.startswith("/api/validation-cases/"):
        case_id = unquote(path.split("/")[-1])
        latest_runs = latest_validation_runs_by_case()
        with EXAMPLES_LOCK:
            example = next((item for item in parse_examples() if item.get("id") == case_id), None)
        if not example:
            error_response(handler, 404, "验证案例不存在")
        else:
            success_response(handler, validation_case_from_example(example, latest_runs.get(case_id)))
        return True
    if path == "/api/validation-runs":
        case_id = (parse_qs(query_string).get("caseId") or [""])[0]
        with VALIDATION_RUNS_LOCK:
            runs = parse_validation_runs()
        if case_id:
            runs = [item for item in runs if item.get("caseId") == case_id]
        runs.sort(key=lambda item: item.get("finishedAt") or item.get("startedAt") or "", reverse=True)
        success_response(handler, runs)
        return True
    if path == "/api/validation-run-groups":
        success_response(handler, query_validation_run_groups(parse_qs(query_string)))
        return True
    if path.startswith("/api/validation-runs/"):
        run = get_validation_run(unquote(path.split("/")[-1]))
        if not run:
            error_response(handler, 404, "验证运行记录不存在")
        else:
            success_response(handler, run)
        return True
    if path == "/api/review-mode-experiments":
        with REVIEW_MODE_EXPERIMENTS_LOCK:
            experiments = parse_review_mode_experiments()
        experiments.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        success_response(handler, experiments)
        return True
    if path.startswith("/api/review-mode-experiments/"):
        experiment = get_review_mode_experiment(unquote(path.split("/")[-1]))
        if not experiment:
            error_response(handler, 404, "对比实验不存在")
        else:
            success_response(handler, experiment)
        return True
    return False


def handle_post(
    handler,
    path: str,
    *,
    submit_regression: Callable,
    submit_experiment: Callable,
    validation_run_factory: Callable,
) -> bool:
    """Handle validation CRUD actions and submit asynchronous validation work."""
    if path == "/api/validation-run-groups/batch-delete":
        try:
            payload = handler.read_json_body()
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
            return True
        group_ids = [str(item) for item in payload.get("groupIds") or [] if str(item).strip()]
        if not group_ids:
            error_response(handler, 400, "请至少选择一个验证批次")
        else:
            success_response(handler, batch_delete_validation_run_groups(group_ids))
        return True
    if path == "/api/validation-cases":
        try:
            payload = handler.read_json_body()
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
            return True
        with EXAMPLES_LOCK:
            examples = parse_examples()
            example = normalize_validation_case_payload(payload)
            errors = validate_validation_case(example)
            if errors:
                error_response(handler, 400, "；".join(errors), errors)
                return True
            examples.append(example)
            save_examples(examples)
        success_response(handler, validation_case_from_example(example), status=201)
        return True
    if path == "/api/validation-cases/import":
        try:
            payload = handler.read_json_body()
            file_name = str(payload.get("name") or "validation-cases.csv")
            encoded = str(payload.get("data") or "")
            if not encoded:
                raise ValueError("导入文件内容为空")
            rows = decode_import_rows(file_name, encoded)
            success_response(handler, import_validation_rows(file_name, rows))
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
        except (TabularImportError, ValueError, TypeError) as exc:
            error_response(handler, 400, str(exc))
        return True
    if path == "/api/validation-cases/batch-delete":
        try:
            payload = handler.read_json_body()
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
            return True
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
        success_response(handler, {
            "total": len(items),
            "success": sum(1 for item in items if item["success"]),
            "failed": sum(1 for item in items if not item["success"]),
            "items": items,
        })
        return True
    if path in {"/api/validation-cases/batch-run", "/api/validation/regression"}:
        try:
            payload = handler.read_json_body()
        except json.JSONDecodeError:
            if path.endswith("batch-run"):
                error_response(handler, 400, "invalid json")
            else:
                json_response(handler, 400, {"error": "invalid json"})
            return True
        selected_ids = (
            payload.get("caseIds") or []
            if path.endswith("batch-run")
            else payload.get("exampleIds") or []
        )
        examples = parse_examples()
        if path.endswith("batch-run") or selected_ids:
            selected = {str(item) for item in selected_ids}
            examples = [item for item in examples if str(item.get("id") or "") in selected]
        if not examples:
            if path.endswith("batch-run"):
                error_response(handler, 400, "没有可验证的案例")
            else:
                json_response(handler, 400, {"error": "没有可验证的用例。"})
            return True
        task_id, task, run_ids = submit_regression(payload, examples)
        if path.endswith("batch-run"):
            items = [
                {"id": str(example.get("id") or ""), "success": True, "run": get_validation_run(run_ids[str(example.get("id") or "")])}
                for example in examples
            ]
            success_response(handler, {
                "groupId": task_id,
                "total": len(items),
                "success": len(items),
                "failed": 0,
                "unreviewable": 0,
                "mismatched": 0,
                "items": items,
            }, status=202)
        else:
            json_response(handler, 202, {"taskId": task_id, "task": task})
        return True
    if path == "/api/review-mode-experiments":
        try:
            payload = handler.read_json_body()
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
            return True
        selected = {str(item) for item in payload.get("caseIds") or [] if str(item).strip()}
        with EXAMPLES_LOCK:
            examples = [item for item in parse_examples() if str(item.get("id") or "") in selected]
        if not examples:
            error_response(handler, 400, "请至少选择一个验证案例")
            return True
        try:
            experiment_id, task = submit_experiment(payload, examples)
        except (TypeError, ValueError) as exc:
            error_response(handler, 400, str(exc))
            return True
        success_response(handler, {"experimentId": experiment_id, "taskId": task.get("id"), "task": task}, status=202)
        return True
    if path.startswith("/api/validation-cases/") and path.endswith("/run"):
        case_id = unquote(path.split("/")[-2])
        with EXAMPLES_LOCK:
            example = next((item for item in parse_examples() if item.get("id") == case_id), None)
        if not example:
            error_response(handler, 404, "验证案例不存在")
            return True
        payload = {"exampleIds": [case_id]}
        task_id, _task, run_ids = submit_regression(payload, [example])
        run = get_validation_run(run_ids[case_id]) or validation_run_factory(task_id, example)
        success_response(handler, run, status=202)
        return True
    return False


def handle_put(handler, path: str) -> bool:
    """Update one validation case while preserving fields omitted by the client."""
    if not path.startswith("/api/validation-cases/"):
        return False
    case_id = unquote(path.split("/")[-1])
    try:
        payload = handler.read_json_body()
    except json.JSONDecodeError:
        error_response(handler, 400, "invalid json")
        return True
    with EXAMPLES_LOCK:
        examples = parse_examples()
        for index, item in enumerate(examples):
            if item.get("id") != case_id:
                continue
            updated = normalize_validation_case_payload(payload, item)
            errors = validate_validation_case(updated)
            if errors:
                error_response(handler, 400, "；".join(errors), errors)
                return True
            examples[index] = updated
            save_examples(examples)
            success_response(handler, validation_case_from_example(updated, latest_validation_runs_by_case().get(case_id)))
            return True
    error_response(handler, 404, "验证案例不存在")
    return True


def handle_delete(handler, path: str) -> bool:
    """Delete one validation case without deleting its historical run records."""
    if not path.startswith("/api/validation-cases/"):
        return False
    case_id = unquote(path.split("/")[-1])
    with EXAMPLES_LOCK:
        examples = parse_examples()
        filtered = [item for item in examples if item.get("id") != case_id]
        if len(filtered) == len(examples):
            error_response(handler, 404, "验证案例不存在")
            return True
        save_examples(filtered)
    success_response(handler, None)
    return True
