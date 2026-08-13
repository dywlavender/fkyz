from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from langchain_core.messages import AIMessageChunk
    from risk_review_web.services.model_runtime import agent_runtime
except ImportError:
    AIMessageChunk = None
    agent_runtime = None

from risk_review_web.services.model_runtime.agent_runner import run_agent_call


class _FakeModel:
    def __init__(self):
        self.calls = 0
        self.message_calls = []

    def bind_tools(self, _tools):
        return self

    def stream(self, messages):
        self.calls += 1
        self.message_calls.append(list(messages))
        if self.calls == 1:
            yield AIMessageChunk(content="", tool_calls=[{
                "name": "read_file",
                "args": {"path": "allowed.md"},
                "id": "call-1",
                "type": "tool_call",
            }])
            return
        yield AIMessageChunk(content="# 标准加工")
        yield AIMessageChunk(content="规范\n\n## 因子加工输入字段\n\n内容")


@unittest.skipIf(AIMessageChunk is None, "LangChain is not installed in this environment")
class AgentRuntimeTest(unittest.TestCase):
    def test_agent_runs_in_current_process_and_reports_events(self):
        with tempfile.TemporaryDirectory(prefix="agent-runtime-test-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            (workspace / "allowed.md").write_text("规则正文", encoding="utf-8")
            events = []
            fake_model = _FakeModel()
            with patch.object(agent_runtime, "create_model", return_value=fake_model):
                status, result = run_agent_call(
                    prompt="测试 Prompt",
                    system_prompt="系统约束",
                    phase_label="Phase 1",
                    workspace=workspace,
                    cancelled=lambda: False,
                    event_callback=lambda event, payload: events.append((event, payload)),
                )
            self.assertEqual(status, "done")
            self.assertTrue(result["content"].startswith("# 标准加工规范"))
            self.assertEqual(result["returncode"], 0)
            self.assertNotIn("stdout", result)
            names = [event for event, _payload in events]
            self.assertEqual(names.count("model_input"), 2)
            self.assertIn("tool_call", names)
            self.assertIn("tool_result", names)
            self.assertIn("agent_final", names)

    def test_agent_cancellation_is_checked_before_call(self):
        with tempfile.TemporaryDirectory(prefix="agent-cancel-test-") as temp_dir:
            with patch.object(agent_runtime, "run_agent") as run:
                status, result = run_agent_call(
                    prompt="不会执行",
                    phase_label="Phase 1",
                    workspace=Path(temp_dir),
                    cancelled=lambda: True,
                )
            self.assertEqual(status, "cancelled")
            self.assertEqual(result["returncode"], 130)
            run.assert_not_called()

    def test_read_tool_rejects_workspace_escape(self):
        with tempfile.TemporaryDirectory(prefix="agent-safe-test-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            with self.assertRaisesRegex(ValueError, "相对路径"):
                agent_runtime.safe_relative_path(workspace, "../outside.md")
            with self.assertRaisesRegex(ValueError, "相对路径"):
                agent_runtime.safe_relative_path(workspace, "/etc/passwd")

    def test_read_tool_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="agent-link-test-") as temp_dir:
            workspace = Path(temp_dir).resolve()
            link = workspace / "outside"
            try:
                link.symlink_to("/etc")
            except OSError:
                self.skipTest("当前文件系统不支持符号链接")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                agent_runtime.safe_relative_path(workspace, "outside/passwd")


if __name__ == "__main__":
    unittest.main()
