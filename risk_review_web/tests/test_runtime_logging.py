from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest.mock import patch

from risk_review_web.services.runtime_logging import DailyRuntimeLogger, runtime_log


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

    def test_console_mirroring_does_not_fail_on_gbk_incompatible_characters(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-gbk-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            raw_console = io.BytesIO()
            console = io.TextIOWrapper(raw_console, encoding="gbk", errors="strict")
            with contextlib.redirect_stdout(console):
                logger.write(
                    level="INFO",
                    module="agent",
                    event="model_response",
                    message="总体结论：🚫 不通过",
                    mirror_to_console=True,
                )
                console.flush()
                rendered = raw_console.getvalue().decode("gbk")

            self.assertIn(r"\U0001f6ab", rendered)
            self.assertIn("不通过", rendered)
            log_text = next(Path(temp_dir).glob("application-*.log")).read_text(encoding="utf-8")
            self.assertIn("🚫", log_text)

    def test_runtime_logging_never_propagates_console_encoding_failures(self):
        with patch(
            "risk_review_web.services.runtime_logging._DEFAULT_LOGGER.write",
            side_effect=UnicodeEncodeError("gbk", "🚫", 0, 1, "illegal multibyte sequence"),
        ):
            runtime_log("requirement-review", "不通过")

    def test_writes_one_daily_file_with_trace_and_single_line_message(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-log-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            occurred_at = datetime(2026, 7, 29, 16, 30, tzinfo=timezone(timedelta(hours=8)))
            path = logger.write(
                level="INFO",
                module="requirement-review",
                event="agent_output",
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

    def test_can_write_raw_multiline_message_in_same_daily_file(self):
        with tempfile.TemporaryDirectory(prefix="risk-runtime-raw-log-") as temp_dir:
            logger = DailyRuntimeLogger(Path(temp_dir), retention_days=10)
            occurred_at = datetime(2026, 7, 29, 16, 30, tzinfo=timezone(timedelta(hours=8)))
            path = logger.write(
                level="INFO",
                module="agent",
                event="model_input",
                message='{"messages": [\n  {"role": "user", "content": "原始输入"}\n]}',
                trace_id="tr_example",
                task_id="phase1-example",
                timestamp=occurred_at,
                raw_message=True,
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn("raw_message_begin", content)
            self.assertIn('"content": "原始输入"', content)
            self.assertIn("raw_message_end", content)
            self.assertNotIn(r"\n  {\"role\"", content)

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
