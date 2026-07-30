from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import Future
from unittest.mock import patch

from risk_review_web import server_v2


class ServerTaskWiringTest(unittest.TestCase):
    def setUp(self):
        self.runtime_log_patch = patch.object(server_v2, "runtime_log")
        self.runtime_log = self.runtime_log_patch.start()

    def tearDown(self):
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
            self.assertIn("NameError: missing dependency", task["result"]["stderr"])
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

    def test_cancelling_orphaned_task_reaches_terminal_state(self):
        task_id = f"task-cancel-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        with server_v2.TASK_LOCK:
            server_v2.TASKS[task_id]["status"] = "running"
        try:
            self.assertTrue(server_v2.request_task_cancel(task_id))
            self.assertEqual(server_v2.TASKS[task_id]["status"], "cancelled")
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

    def test_running_model_process_group_can_be_terminated(self):
        task_id = f"task-process-cancel-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        with server_v2.RUNNING_PROCS_LOCK:
            server_v2.RUNNING_PROCS[task_id] = process
        try:
            self.assertTrue(server_v2.request_task_cancel(task_id))
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
            with server_v2.RUNNING_PROCS_LOCK:
                server_v2.RUNNING_PROCS.pop(task_id, None)
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

    def test_completed_worker_cannot_leave_task_running(self):
        task_id = f"task-terminal-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        with server_v2.TASK_LOCK:
            server_v2.TASKS[task_id]["status"] = "running"
        future: Future = Future()
        server_v2.track_task_future(task_id, future, "Phase 1")
        try:
            future.set_result(None)
            task = server_v2.TASKS[task_id]
            self.assertEqual(task["status"], "error")
            self.assertIn("未写入终态", task["result"]["stderr"])
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

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
                server_v2.TASKS.pop(child_id, None)
                server_v2.TASKS.pop(parent_id, None)

    def test_child_tasks_share_parent_model_backend_snapshot(self):
        parent_id = f"task-parent-backend-{uuid.uuid4().hex}"
        child_id = f"{parent_id}-child"
        with patch.object(server_v2, "get_runtime_settings", return_value={"modelBackend": "agent"}):
            server_v2.create_task_record(parent_id, task_type="batch")
        with patch.object(server_v2, "get_runtime_settings", return_value={"modelBackend": "claude"}):
            server_v2.create_task_record(child_id, task_type="phase1", parent_task_id=parent_id)
        try:
            self.assertEqual(server_v2.TASKS[parent_id]["modelBackend"], "agent")
            self.assertEqual(server_v2.TASKS[child_id]["modelBackend"], "agent")
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(child_id, None)
                server_v2.TASKS.pop(parent_id, None)

    def test_model_dispatch_uses_task_backend_snapshot(self):
        task_id = f"task-dispatch-{uuid.uuid4().hex}"
        with patch.object(server_v2, "get_runtime_settings", return_value={"modelBackend": "agent"}):
            server_v2.create_task_record(task_id, task_type="phase1")
        try:
            with patch.object(server_v2, "run_agent_prompt", return_value=("done", {"stdout": "ok"})) as agent:
                with patch.object(server_v2, "run_claude_prompt") as claude:
                    status, result = server_v2.run_model_prompt(
                        task_id,
                        "Phase 1",
                        "prompt",
                        isolated_workspace=server_v2.ROOT,
                    )
            self.assertEqual(status, "done")
            self.assertEqual(result["stdout"], "ok")
            agent.assert_called_once()
            claude.assert_not_called()
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

    def test_normalized_model_content_uses_agent_response_before_stdout(self):
        self.assertEqual(
            server_v2.model_response_content({"content": "agent-response", "stdout": "wrong"}),
            "agent-response",
        )
        self.assertEqual(
            server_v2.model_response_content({"stdout": "claude-stdout"}),
            "claude-stdout",
        )

    def test_generation_consumes_agent_content_without_stdout(self):
        task_id = f"task-agent-content-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="phase1")
        try:
            with patch.object(
                server_v2,
                "run_model_prompt",
                return_value=("done", {"content": "# Agent Markdown", "returncode": 0}),
            ):
                status, result, raw_output = server_v2.run_generation(
                    task_id=task_id,
                    phase_label="Phase 1",
                    empty_error="empty",
                    prompt="prompt",
                    isolated_workspace=server_v2.ROOT,
                )
            self.assertEqual(status, "done")
            self.assertEqual(raw_output, "# Agent Markdown")
            self.assertNotIn("stdout", result)
        finally:
            with server_v2.TASK_LOCK:
                server_v2.TASKS.pop(task_id, None)

    def test_model_io_is_persisted_without_enabling_page_debug_logs(self):
        task_id = f"task-file-debug-{uuid.uuid4().hex}"
        server_v2.create_task_record(task_id, task_type="batch", debug_claude=False)
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
                server_v2.TASKS.pop(task_id, None)


if __name__ == "__main__":
    unittest.main()
