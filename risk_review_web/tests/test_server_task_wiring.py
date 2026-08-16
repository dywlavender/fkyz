from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from risk_review_web import app_context as server_v2
from risk_review_web.domain.agent_result import AgentResult
from risk_review_web.repositories.review_record_repository import ReviewRecordRepository


class ServerTaskWiringTest(unittest.TestCase):
    def setUp(self):
        self.runtime_log_patch = patch.object(server_v2, "runtime_log")
        self.runtime_log = self.runtime_log_patch.start()
        self.history_temp_dir = tempfile.TemporaryDirectory(prefix="task-wiring-history-")
        self.history_patch = patch.object(
            server_v2,
            "REVIEW_RECORD_REPOSITORY",
            ReviewRecordRepository(path=Path(self.history_temp_dir.name) / "review_records.json"),
        )
        self.history_patch.start()

    def tearDown(self):
        self.history_patch.stop()
        self.history_temp_dir.cleanup()
        self.runtime_log_patch.stop()

    def test_phase1_api_documents_are_available_to_workspace_builder(self):
        documents = server_v2.list_api_docs()
        self.assertTrue(documents)
        api_name = str(documents[0]["name"])
        workspace = server_v2.create_phase1_isolated_workspace(
            "phase1-wiring-test",
            {"apiNames": [api_name]},
        )
        try:
            self.assertTrue(any(path.is_file() for path in workspace.rglob("*.md")))
            self.assertIsNotNone(server_v2.get_api_doc(api_name))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_unexpected_worker_exception_marks_task_as_error(self):
        task_id = f"task-wiring-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        future: Future = Future()
        server_v2.track_task_future(task_id, future, "Phase 1")
        try:
            future.set_exception(NameError("missing dependency"))
            task = server_v2.TASKS[task_id]
            self.assertEqual(task["status"], "error")
            self.assertIn("NameError: missing dependency", task["result"]["error"])
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_cancelling_orphaned_task_reaches_terminal_state(self):
        task_id = f"task-cancel-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        server_v2.TASK_REPOSITORY.start(task_id)
        try:
            self.assertTrue(server_v2.request_task_cancel(task_id))
            self.assertEqual(server_v2.TASKS[task_id]["status"], "cancelled")
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_completed_worker_cannot_leave_task_running(self):
        task_id = f"task-terminal-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        server_v2.TASK_REPOSITORY.start(task_id)
        future: Future = Future()
        server_v2.track_task_future(task_id, future, "Phase 1")
        try:
            future.set_result(None)
            task = server_v2.TASKS[task_id]
            self.assertEqual(task["status"], "error")
            self.assertIn("未写入终态", task["result"]["error"])
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_child_tasks_share_parent_trace_id(self):
        parent_id = f"task-parent-{uuid.uuid4().hex}"
        child_id = f"{parent_id}-child"
        server_v2.create_task_record(parent_id, task_type="batch")
        server_v2.create_task_record(child_id, task_type="phase1", parent_task_id=parent_id)
        try:
            self.assertTrue(server_v2.TASKS[parent_id]["traceId"].startswith("tr_"))
            self.assertEqual(
                server_v2.TASKS[parent_id]["traceId"],
                server_v2.TASKS[child_id]["traceId"],
            )
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(child_id)
                server_v2.TASK_REPOSITORY.remove(parent_id)

    def test_model_dispatch_always_uses_agent(self):
        task_id = f"task-dispatch-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        try:
            with patch.object(server_v2, "run_agent_prompt", return_value=AgentResult.succeeded("ok")) as agent:
                result = server_v2.run_model_prompt(
                    task_id,
                    "Phase 1",
                    "prompt",
                    isolated_workspace=server_v2.ROOT,
                )
            self.assertTrue(result.ok)
            self.assertEqual(result.content, "ok")
            agent.assert_called_once()
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_agent_result_content_is_canonical(self):
        result = AgentResult.succeeded("agent-response")
        self.assertEqual(result.content, "agent-response")
        self.assertNotIn("stdout", result.as_dict())

    def test_stage_executor_consumes_agent_content_without_stdout(self):
        task_id = f"task-agent-content-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        try:
            with patch.object(
                server_v2,
                "run_model_prompt",
                return_value=AgentResult.succeeded("# Agent Markdown"),
            ):
                result = server_v2.STAGE_EXECUTOR.run_agent_result(
                    task_id=task_id,
                    phase_label="Phase 1",
                    prompt="prompt",
                    workspace=server_v2.ROOT,
                    agent_call=lambda: server_v2.run_model_prompt(
                        task_id,
                        "Phase 1",
                        "prompt",
                        isolated_workspace=server_v2.ROOT,
                    ),
                    empty_error="empty",
                )
            self.assertTrue(result.ok)
            self.assertEqual(result.content, "# Agent Markdown")
            self.assertNotIn("stdout", result.as_dict())
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_model_io_is_persisted_without_enabling_page_debug_logs(self):
        task_id = f"task-file-debug-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="batch", debug_agent=False)
        try:
            trace_id = server_v2.TASKS[task_id]["traceId"]
            server_v2.task_debug_log(task_id, "prompt", "test prompt", memory=False)
            self.assertEqual(server_v2.TASKS[task_id]["debugLogs"], [])
            self.runtime_log.assert_called_with(
                "batch",
                "test prompt",
                level="INFO",
                event="model_prompt",
                trace_id=trace_id,
                task_id=task_id,
            )
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASK_REPOSITORY.remove(task_id)

    def test_history_failure_does_not_publish_done_and_can_be_retried(self):
        task_id = f"task-unpersisted-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="child")
        with patch.object(
            server_v2.REVIEW_RECORD_REPOSITORY,
            "upsert_task",
            side_effect=OSError("history unavailable"),
        ):
            server_v2.finish_task(task_id, "done", {"content": "ok"})
        failed = server_v2.TASK_REPOSITORY.get(task_id)
        self.assertEqual(failed.status.value, "error")
        self.assertTrue(failed.unpersisted)
        self.assertEqual(failed.persistence_target_status, "done")
        retried = server_v2.retry_task_persistence(task_id)
        self.assertEqual(retried["status"], "done")
        self.assertFalse(retried["unpersisted"])
        server_v2.TASK_REPOSITORY.remove(task_id)

    def test_cancel_wins_before_done_history_is_persisted(self):
        task_id = f"task-cancel-race-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase2")
        server_v2.TASK_REPOSITORY.start(task_id)
        server_v2.TASK_REPOSITORY.request_cancel(
            task_id,
            server_v2.agent_result_payload(
                AgentResult.cancelled("任务已请求取消。"),
                error="任务已请求取消。",
            ),
        )
        try:
            server_v2.finish_task(task_id, "done", {"content": "late success"})
            task = server_v2.TASK_REPOSITORY.require(task_id)
            record = server_v2.REVIEW_RECORD_REPOSITORY.get(f"task-{task_id}")
            self.assertEqual(task.status.value, "cancelled")
            self.assertEqual(record["status"], "cancelled")
            self.assertEqual(record["taskStatus"], "cancelled")
        finally:
            server_v2.TASK_REPOSITORY.remove(task_id)


if __name__ == "__main__":
    unittest.main()
