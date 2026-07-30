from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from risk_review_web.services.runtime_logging import DailyRuntimeLogger


class DailyRuntimeLoggerTest(unittest.TestCase):
    def test_can_flush_each_record_to_console_immediately(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-console-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                logger.write(
                    level="INFO",
                    module="agent",
                    event="model_agent_tool_result",
                    message="工具执行完成",
                    mirror_to_console=True,
                )
                self.assertIn("工具执行完成", console.getvalue())

    def test_writes_one_daily_file_with_trace_and_single_line_message(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-log-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            occurred_at = datetime(2026, 7, 29, 16, 30, tzinfo=timezone(timedelta(hours=8)))
            path = logger.write(
                level="INFO",
                module="requirement-review",
                event="claude_output",
                message="第一行\n第二行",
                trace_id="tr_example",
                task_id="requirement-review-example",
                timestamp=occurred_at,
            )
            self.assertEqual(path.name, "application-2026-07-29.log")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("traceId=tr_example", lines[0])
            self.assertIn("taskId=requirement-review-example", lines[0])
            self.assertIn(r"第一行\n第二行", lines[0])

    def test_keeps_only_ten_calendar_days(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-log-retention-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            first_day = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
            for offset in range(11):
                logger.write(
                    level="INFO",
                    module="test",
                    event="retention",
                    message=str(offset),
                    timestamp=first_day + timedelta(days=offset),
                )
            files = sorted(Path(temp_dir).glob("application-*.log"))
            self.assertEqual(len(files), 10)
            self.assertEqual(files[0].name, "application-2026-07-02.log")
            self.assertEqual(files[-1].name, "application-2026-07-11.log")


if __name__ == "__main__":
    unittest.main()
