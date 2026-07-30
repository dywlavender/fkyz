from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from risk_review_web.paths import LOG_DIR


LOG_RETENTION_DAYS = 10
_LOG_FILE_PATTERN = re.compile(r"^application-(\d{4}-\d{2}-\d{2})\.log$")


def new_trace_id() -> str:
    """Return a compact correlation id for one task or request chain."""
    return f"tr_{uuid.uuid4().hex[:16]}"


def _safe_label(value: object) -> str:
    """Keep bracketed log labels on one physical line."""
    text = str(value or "-").strip() or "-"
    return re.sub(r"[\]\r\n]", "_", text)


class DailyRuntimeLogger:
    """Append thread-safe one-line records to one date-partitioned log file."""

    def __init__(self, log_dir: Path, retention_days: int = LOG_RETENTION_DAYS):
        self.log_dir = Path(log_dir)
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.Lock()
        self._last_cleanup_date: date | None = None

    def write(
        self,
        *,
        level: str,
        module: str,
        event: str,
        message: object,
        trace_id: str = "",
        task_id: str = "",
        timestamp: datetime | None = None,
        mirror_to_console: bool = False,
    ) -> Path:
        """Write exactly one physical line and return the selected daily file."""
        occurred_at = timestamp or datetime.now().astimezone()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.astimezone()
        day = occurred_at.date()
        path = self.log_dir / f"application-{day.isoformat()}.log"
        rendered_message = json.dumps(str(message or ""), ensure_ascii=False)
        line = (
            f"{occurred_at.isoformat(timespec='milliseconds')} "
            f"{_safe_label(level).upper():<5} "
            f"[traceId={_safe_label(trace_id)}] "
            f"[taskId={_safe_label(task_id)}] "
            f"[module={_safe_label(module)}] "
            f"[event={_safe_label(event)}] "
            f"message={rendered_message}\n"
        )
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
            if mirror_to_console:
                sys.stdout.write(line)
                sys.stdout.flush()
            if self._last_cleanup_date != day:
                self._cleanup_expired(day)
                self._last_cleanup_date = day
        return path

    def _cleanup_expired(self, today: date) -> None:
        """Keep today and the previous retention_days - 1 calendar files."""
        cutoff = today - timedelta(days=self.retention_days - 1)
        for path in self.log_dir.glob("application-*.log"):
            match = _LOG_FILE_PATTERN.fullmatch(path.name)
            if not match:
                continue
            try:
                file_day = date.fromisoformat(match.group(1))
                if file_day < cutoff:
                    path.unlink()
            except (OSError, ValueError):
                continue


_DEFAULT_LOGGER = DailyRuntimeLogger(LOG_DIR)


def runtime_log(
    module: str,
    message: object,
    *,
    level: str = "INFO",
    event: str = "runtime",
    trace_id: str = "",
    task_id: str = "",
) -> None:
    """Best-effort application logging that never interrupts business execution."""
    try:
        _DEFAULT_LOGGER.write(
            level=level,
            module=module,
            event=event,
            message=message,
            trace_id=trace_id,
            task_id=task_id,
            mirror_to_console=os.environ.get("RISK_REVIEW_LOG_TO_CONSOLE", "").strip().lower()
            in {"1", "true", "yes", "on"},
        )
    except OSError:
        return
