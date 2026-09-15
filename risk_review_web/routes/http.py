from __future__ import annotations

"""HTTP adapter for the application context."""

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

from risk_review_web import app_context as app
from risk_review_web.routes import requirement_review as requirement_review_routes
from risk_review_web.routes import scheduled as scheduled_routes
from risk_review_web.routes import static_files as static_routes
from risk_review_web.routes import system as system_routes
from risk_review_web.routes import validation as validation_routes
from risk_review_web.services.http_responses import error_response, json_response, success_response
from risk_review_web.repositories.review_record_repository import ReviewRecordRepositoryError
from risk_review_web.services.tabular_import import (
    TabularImportError,
    decode_import_rows,
    normalize_batch_rows_from_table,
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
        if scheduled_routes.handle_get(self, path, app.get_scheduled_task_service, parsed.query):
            return
        if system_routes.handle_get(
            self,
            path,
            parsed.query,
            list_outputs=app.list_outputs,
            pool_snapshot=app.GLOBAL_TASK_POOL.snapshot,
            system_health=app.system_health,
            model_test=app.test_model_runtime,
        ):
            return
        if static_routes.handle_get(self, path, app.STATIC_DIR, app.OUTPUTS_DIR):
            return
        if validation_routes.handle_get(self, path, parsed.query):
            return
        if path == "/api/review-history":
            try:
                success_response(self, app.review_history_records(summaries=True))
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
        elif path.startswith("/api/review-records/"):
            record_id = unquote(path.split("/")[-1])
            try:
                record = app.review_record_detail(record_id)
                if record is None:
                    error_response(self, 404, "评审记录不存在")
                else:
                    success_response(self, record)
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
        elif path == "/api/review-records":
            try:
                success_response(self, app.query_review_records(parse_qs(parsed.query)))
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
        elif path == "/api/examples":
            json_response(self, 200, {"examples": app.parse_examples()})
        elif path == "/api/tasks":
            query = parse_qs(parsed.query)
            include_history = (query.get("scope") or ["active"])[0].lower() == "all"
            summary = (query.get("summary") or ["false"])[0].lower() == "true"
            try:
                json_response(self, 200, {
                    "tasks": app.task_history(include_history=include_history, summary=summary),
                    "pool": app.GLOBAL_TASK_POOL.snapshot(),
                })
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
        elif path.startswith("/api/tasks/"):
            task_id = unquote(path.split("/")[-1])
            try:
                task = app.task_view(task_id)
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
                return
            if not task:
                json_response(self, 404, {"error": "not found"})
                return
            json_response(self, 200, {
                "task": task,
                "pool": app.GLOBAL_TASK_POOL.snapshot(),
            })
        else:
            static_routes.serve_static(self, app.STATIC_DIR, path.lstrip("/"))

    def do_POST(self):
        """Dispatch creation, control, import, and review-submission requests."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_post(self, parsed.path, app.get_scheduled_task_service):
            return
        if requirement_review_routes.handle_post(
            self,
            parsed.path,
            submit_review=app.submit_requirement_review,
        ):
            return
        if validation_routes.handle_post(
            self,
            parsed.path,
            submit_regression=app.submit_regression_task,
            submit_experiment=app.submit_review_mode_experiment,
            validation_run_factory=app.validation_run_for_example,
        ):
            return
        if parsed.path.startswith("/api/review-records/") and parsed.path.endswith("/archive"):
            record_id = unquote(parsed.path.split("/")[-2])
            try:
                payload = self.read_json_body()
                success_response(self, app.archive_review_record(record_id, bool(payload.get("archived", True))))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
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
                rows = decode_import_rows(name, data_b64)
            except TabularImportError as exc:
                json_response(self, 400, {"error": str(exc)})
                return
            json_response(self, 200, {"rows": rows, "items": normalize_batch_rows_from_table(rows), "name": name})
            return
        if parsed.path == "/api/review/batch":
            try:
                payload = self.read_json_body()
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "invalid json"})
                return
            try:
                task_id, task = app.submit_batch_review(payload)
            except ValueError as exc:
                json_response(self, 400, {"error": str(exc)})
                return
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/cancel"):
            task_id = parsed.path.split("/")[-2]
            if not app.request_task_cancel(task_id):
                json_response(self, 404, {"error": "task not found"})
                return
            task = app.task_view(task_id)
            json_response(self, 200, {"task": task, "pool": app.GLOBAL_TASK_POOL.snapshot()})
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/retry-persistence"):
            task_id = unquote(parsed.path.split("/")[-2])
            try:
                success_response(self, app.retry_task_persistence(task_id))
            except KeyError:
                error_response(self, 404, "task not found")
            except (OSError, RuntimeError, ValueError) as exc:
                error_response(self, 409, str(exc))
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
            task_id, task = app.submit_phase1(payload)
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
            task_id, task = app.submit_phase2(payload)
            json_response(self, 202, {"taskId": task_id, "task": task})
            return
        
        if parsed.path != "/api/review":
            json_response(self, 404, {"error": "not found"})
            return
        json_response(self, 410, {"error": "/api/review 已废弃，请使用 /api/review/phase1 和 /api/review/phase2 两阶段接口。"})
        return

    def do_PUT(self):
        """Dispatch updates for settings and validation cases."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_put(self, parsed.path, app.get_scheduled_task_service):
            return
        if validation_routes.handle_put(self, parsed.path):
            return
        if parsed.path == "/api/runtime-settings":
            try:
                success_response(self, app.update_runtime_settings(self.read_json_body()))
            except json.JSONDecodeError:
                error_response(self, 400, "invalid json")
            except (TypeError, ValueError) as exc:
                error_response(self, 400, str(exc))
            return
        json_response(self, 404, {"error": "not found"})

    def do_DELETE(self):
        """Dispatch deletion requests while preserving active-task safety checks."""
        parsed = urlparse(self.path)
        if scheduled_routes.handle_delete(self, parsed.path, app.get_scheduled_task_service):
            return
        if validation_routes.handle_delete(self, parsed.path):
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, app.delete_task_record(task_id))
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except (OSError, ValueError) as exc:
                error_response(self, 400, str(exc))
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
            return
        if parsed.path.startswith("/api/review-history/"):
            record_id = unquote(parsed.path.split("/")[-1])
            try:
                success_response(self, app.delete_review_history_record(record_id))
            except FileNotFoundError as exc:
                error_response(self, 404, str(exc))
            except (OSError, ValueError) as exc:
                error_response(self, 400, str(exc))
            except ReviewRecordRepositoryError as exc:
                error_response(self, 503, str(exc))
            return
        json_response(self, 404, {"error": "not found"})
