from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from langchain_core.messages import AIMessage
    from risk_review_web import agent_worker
except ImportError:  # The agent tests run in the dedicated langchain1.2 environment.
    AIMessage = None
    agent_worker = None

from risk_review_web.services.model_runtime.agent_runner import (
    AGENT_EVENT_PREFIX,
    agent_health,
    parse_agent_events,
    run_agent_process,
)


class _FakeModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_file",
                    "args": {"path": "allowed.md"},
                    "id": "call-1",
                    "type": "tool_call",
                }],
            )
        return AIMessage(content="# 标准加工规范\n\n## 因子加工输入字段\n\n内容")


class AgentRunnerHealthTest(unittest.TestCase):
    def test_health_checks_key_for_selected_provider(self):
        environment = {
            "RISK_REVIEW_AGENT_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "configured-for-another-provider",
        }
        with patch.dict(os.environ, environment, clear=False):
            self.assertFalse(agent_health()["api_key_configured"])


@unittest.skipIf(AIMessage is None, "LangChain is only installed in the agent environment")
class AgentWorkerTest(unittest.TestCase):
    def test_worker_does_not_copy_final_response_to_stdout(self):
        with tempfile.TemporaryDirectory(prefix="agent-main-test-") as temp_dir:
            stdout = io.StringIO()
            arguments = [
                "agent_worker",
                "--phase",
                "Phase 1",
                "--workspace",
                temp_dir,
            ]
            with patch.object(agent_worker.sys, "argv", arguments):
                with patch.object(agent_worker.sys, "stdin", io.StringIO("测试 Prompt")):
                    with patch.object(agent_worker, "load_project_env"):
                        with patch.object(agent_worker, "run_agent", return_value="# 最终响应") as run:
                            with contextlib.redirect_stdout(stdout):
                                exit_code = agent_worker.main()
            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(run.call_args.args[0], "测试 Prompt")

    def test_agent_logs_every_round_tool_call_result_and_final_output(self):
        with tempfile.TemporaryDirectory(prefix="agent-worker-test-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            (workspace / "allowed.md").write_text("规则正文", encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(agent_worker, "create_model", return_value=_FakeModel()):
                with contextlib.redirect_stderr(stderr):
                    result = agent_worker.run_agent("测试 Prompt", workspace)
            self.assertTrue(result.startswith("# 标准加工规范"))
            events = [
                json.loads(line[len(agent_worker.EVENT_PREFIX):])
                for line in stderr.getvalue().splitlines()
                if line.startswith(agent_worker.EVENT_PREFIX)
            ]
            names = [item["event"] for item in events]
            self.assertEqual(names.count("model_input"), 2)
            self.assertEqual(names.count("model_output"), 2)
            self.assertIn("tool_call", names)
            self.assertIn("tool_result", names)
            self.assertIn("agent_final", names)
            tool_result = next(item for item in events if item["event"] == "tool_result")
            self.assertEqual(tool_result["payload"]["result"], "规则正文")

    def test_read_tool_rejects_workspace_escape(self):
        with tempfile.TemporaryDirectory(prefix="agent-worker-safe-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            with self.assertRaisesRegex(ValueError, "相对路径"):
                agent_worker.safe_relative_path(workspace, "../outside.md")
            with self.assertRaisesRegex(ValueError, "相对路径"):
                agent_worker.safe_relative_path(workspace, "/etc/passwd")

    def test_read_tool_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="agent-worker-link-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            link = workspace / "outside"
            try:
                link.symlink_to("/etc")
            except OSError:
                self.skipTest("当前文件系统不支持符号链接")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                agent_worker.safe_relative_path(workspace, "outside/passwd")

    def test_structured_stderr_events_are_separated_from_diagnostics(self):
        received: list[tuple[str, object]] = []
        stderr = "\n".join([
            AGENT_EVENT_PREFIX + json.dumps({"event": "model_input", "payload": {"round": 1}}),
            "provider warning",
        ])
        diagnostics = parse_agent_events(stderr, lambda event, payload: received.append((event, payload)))
        self.assertEqual(received, [("model_input", {"round": 1})])
        self.assertEqual(diagnostics, "provider warning")

    def test_agent_process_streams_events_before_final_output(self):
        with tempfile.TemporaryDirectory(prefix="agent-stream-test-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            events: list[tuple[str, float]] = []
            tool_result_seen = threading.Event()
            outcome: dict[str, object] = {}

            def record_event(event: str, _payload: object) -> None:
                events.append((event, time.monotonic()))
                if event == "tool_result":
                    tool_result_seen.set()

            def run_worker() -> None:
                outcome["value"] = run_agent_process(
                    prompt="测试实时日志",
                    phase_label="Phase 1",
                    workspace=workspace,
                    timeout_seconds=10,
                    process_started=lambda _proc: None,
                    terminate=lambda: None,
                    cancelled=lambda: False,
                    event_callback=record_event,
                    worker_module="risk_review_web.tests.fixtures.streaming_agent_worker",
                )

            runner_thread = threading.Thread(target=run_worker, daemon=True)
            runner_thread.start()
            self.assertTrue(tool_result_seen.wait(timeout=5))
            self.assertTrue(runner_thread.is_alive(), "工具日志应在 Worker 返回最终结果前到达")
            runner_thread.join(timeout=10)
            self.assertFalse(runner_thread.is_alive())
            status, result = outcome["value"]
            self.assertEqual(status, "done")
            self.assertEqual(result["content"], "# 实时日志测试")
            self.assertNotIn("stdout", result)
            names = [event for event, _seen_at in events]
            self.assertIn("model_input", names)
            self.assertIn("tool_call", names)
            self.assertIn("tool_result", names)
            self.assertIn("agent_final", names)


if __name__ == "__main__":
    unittest.main()
