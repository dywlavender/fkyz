from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import unquote

from risk_review_web.scheduling.models import ScheduledTaskError
from risk_review_web.services.http_responses import error_response, success_response


def handle_get(handler, path: str, get_service: Callable):
    """Handle scheduled-task and scheduled-run read endpoints."""
    if path == "/api/scheduled-tasks":
        success_response(handler, get_service().list_tasks())
        return True
    if path.startswith("/api/scheduled-tasks/") and path.endswith("/runs"):
        task_id = unquote(path.split("/")[-2])
        success_response(handler, get_service().list_runs(task_id))
        return True
    if path.startswith("/api/scheduled-tasks/"):
        task_id = unquote(path.split("/")[-1])
        try:
            success_response(handler, get_service().get_task(task_id))
        except ScheduledTaskError as exc:
            error_response(handler, 404, str(exc))
        return True
    if path.startswith("/api/scheduled-runs/"):
        run_id = unquote(path.split("/")[-1])
        try:
            success_response(handler, get_service().get_run(run_id))
        except ScheduledTaskError as exc:
            error_response(handler, 404, str(exc))
        return True
    return False


def handle_post(handler, path: str, get_service: Callable):
    """Handle scheduled-task creation, control actions, import preview, and cancellation."""
    if path == "/api/scheduled-tasks/import-preview":
        try:
            payload = handler.read_json_body()
            result = get_service().preview_import(
                payload.get("name", ""),
                payload.get("data", ""),
                mode=payload.get("mode"),
                task_standard_file=payload.get("standardFile"),
            )
            success_response(handler, result)
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
        except ScheduledTaskError as exc:
            error_response(handler, 400, str(exc))
        return True
    if path == "/api/scheduled-tasks":
        try:
            success_response(handler, get_service().create_task(handler.read_json_body()), status=201)
        except json.JSONDecodeError:
            error_response(handler, 400, "invalid json")
        except (ScheduledTaskError, TypeError, ValueError) as exc:
            error_response(handler, 400, str(exc))
        return True
    actions = {"enable": True, "disable": False}
    for action, enabled in actions.items():
        suffix = f"/{action}"
        if path.startswith("/api/scheduled-tasks/") and path.endswith(suffix):
            task_id = unquote(path.split("/")[-2])
            try:
                success_response(handler, get_service().set_enabled(task_id, enabled))
            except ScheduledTaskError as exc:
                error_response(handler, 404 if "不存在" in str(exc) else 400, str(exc))
            return True
    if path.startswith("/api/scheduled-tasks/") and path.endswith("/run-now"):
        task_id = unquote(path.split("/")[-2])
        try:
            success_response(handler, get_service().submit_task_run(task_id, trigger="manual"), status=202)
        except ScheduledTaskError as exc:
            error_response(handler, 404 if "不存在" in str(exc) else 409, str(exc))
        return True
    if path.startswith("/api/scheduled-runs/") and path.endswith("/cancel"):
        run_id = unquote(path.split("/")[-2])
        try:
            success_response(handler, get_service().cancel_run(run_id))
        except ScheduledTaskError as exc:
            error_response(handler, 404, str(exc))
        return True
    if path.startswith("/api/scheduled-runs/") and path.endswith("/retry-history"):
        run_id = unquote(path.split("/")[-2])
        try:
            success_response(handler, get_service().retry_run_history(run_id))
        except ScheduledTaskError as exc:
            error_response(handler, 404 if "不存在" in str(exc) else 409, str(exc))
        return True
    return False


def handle_put(handler, path: str, get_service: Callable):
    """Handle updates to an existing scheduled task."""
    if not path.startswith("/api/scheduled-tasks/"):
        return False
    task_id = unquote(path.split("/")[-1])
    try:
        success_response(handler, get_service().update_task(task_id, handler.read_json_body()))
    except json.JSONDecodeError:
        error_response(handler, 400, "invalid json")
    except (ScheduledTaskError, TypeError, ValueError) as exc:
        error_response(handler, 404 if "不存在" in str(exc) else 400, str(exc))
    return True


def handle_delete(handler, path: str, get_service: Callable):
    """Handle deletion of a non-running scheduled task and its runs."""
    if not path.startswith("/api/scheduled-tasks/"):
        return False
    task_id = unquote(path.split("/")[-1])
    try:
        get_service().delete_task(task_id)
        success_response(handler, None)
    except ScheduledTaskError as exc:
        error_response(handler, 404, str(exc))
    return True
