from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from risk_review_web.scheduling.models import ScheduledTaskError

JsonDict = dict[str, Any]


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_time_text(value: str | None) -> tuple[int, int]:
    text = (value or "").strip() or "00:00"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ScheduledTaskError("runTime 格式必须为 HH:MM。")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduledTaskError("runTime 超出有效时间范围。")
    return hour, minute


def _weekday_index(value: str | None) -> int:
    text = (value or "").strip().lower()
    mapping = {
        "1": 0,
        "mon": 0,
        "monday": 0,
        "周一": 0,
        "2": 1,
        "tue": 1,
        "tuesday": 1,
        "周二": 1,
        "3": 2,
        "wed": 2,
        "wednesday": 2,
        "周三": 2,
        "4": 3,
        "thu": 3,
        "thursday": 3,
        "周四": 3,
        "5": 4,
        "fri": 4,
        "friday": 4,
        "周五": 4,
        "6": 5,
        "sat": 5,
        "saturday": 5,
        "周六": 5,
        "7": 6,
        "sun": 6,
        "sunday": 6,
        "周日": 6,
        "周天": 6,
    }
    if text not in mapping:
        raise ScheduledTaskError("weekday 无效，应为 1-7 或周几。")
    return mapping[text]


def _month_day(value: str | None) -> int:
    try:
        day = int(str(value or "").strip())
    except ValueError as exc:
        raise ScheduledTaskError("monthDay 必须为 1-31。") from exc
    if day < 1 or day > 31:
        raise ScheduledTaskError("monthDay 必须为 1-31。")
    return day


def _set_time(base: datetime, run_time: str | None) -> datetime:
    hour, minute = _parse_time_text(run_time)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _safe_month_replace(base: datetime, day: int) -> datetime:
    candidate = base.replace(day=1)
    while True:
        try:
            return candidate.replace(day=day)
        except ValueError:
            day -= 1
            if day < 1:
                raise ScheduledTaskError("无法计算 monthly nextRunAt。")


def compute_next_run_at(task: JsonDict, *, now: datetime | None = None, from_time: datetime | None = None) -> str | None:
    """Compute the next local execution time for a normalized schedule."""
    current = now or _now()
    base = from_time or current
    schedule_type = task.get("scheduleType")
    run_time = task.get("runTime")
    if schedule_type == "once":
        run_date_text = (task.get("runDate") or "").strip()
        if not run_date_text:
            raise ScheduledTaskError("once 任务必须提供 runDate。")
        try:
            run_date = datetime.strptime(run_date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ScheduledTaskError("runDate 格式必须为 YYYY-MM-DD。") from exc
        candidate = _set_time(datetime.combine(run_date, datetime.min.time(), tzinfo=current.tzinfo), run_time)
        if candidate <= current and task.get("lastRunAt"):
            return None
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "daily":
        candidate = _set_time(base, run_time)
        if candidate <= current:
            candidate = _set_time(base + timedelta(days=1), run_time)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "weekly":
        weekday = _weekday_index(task.get("weekday"))
        candidate = _set_time(base, run_time)
        delta = (weekday - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=delta)
        if candidate <= current:
            candidate = candidate + timedelta(days=7)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "monthly":
        day = _month_day(task.get("monthDay"))
        candidate = _safe_month_replace(_set_time(base, run_time), day)
        if candidate <= current:
            if candidate.month == 12:
                next_month = candidate.replace(year=candidate.year + 1, month=1, day=1)
            else:
                next_month = candidate.replace(month=candidate.month + 1, day=1)
            candidate = _safe_month_replace(_set_time(next_month, run_time), day)
        return candidate.isoformat(timespec="seconds")
    if schedule_type == "cron":
        cron_text = (task.get("cron") or "").strip()
        return compute_next_run_from_cron(cron_text, current).isoformat(timespec="seconds")
    raise ScheduledTaskError("scheduleType 无效。")


def compute_next_run_from_cron(cron_text: str, now: datetime) -> datetime:
    """Resolve the next timestamp for the supported five-field cron subset."""
    parts = cron_text.split()
    if len(parts) != 5:
        raise ScheduledTaskError("cron 仅支持标准 5 段表达式。")
    minute_values = _parse_cron_field(parts[0], 0, 59)
    hour_values = _parse_cron_field(parts[1], 0, 23)
    day_values = _parse_cron_field(parts[2], 1, 31)
    month_values = _parse_cron_field(parts[3], 1, 12)
    raw_week_values = _parse_cron_field(parts[4], 0, 6, sunday_alias=7)
    # cron: 0/7=Sunday, 1=Monday; datetime.weekday(): Monday=0, Sunday=6.
    week_values = {6 if value == 0 else value - 1 for value in raw_week_values}
    day_wildcard = parts[2] == "*"
    week_wildcard = parts[4] == "*"
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        day_matches = candidate.day in day_values
        week_matches = candidate.weekday() in week_values
        calendar_matches = (
            day_matches and week_matches
            if day_wildcard or week_wildcard
            else day_matches or week_matches
        )
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.month in month_values
            and calendar_matches
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ScheduledTaskError("cron 下一次执行时间超出一年搜索范围。")


def _parse_cron_field(field: str, minimum: int, maximum: int, sunday_alias: int | None = None) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            values.update(range(minimum, maximum + 1))
            continue
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ScheduledTaskError(f"cron 字段步长非法：{part}") from exc
            if step <= 0:
                raise ScheduledTaskError(f"cron 字段步长非法：{part}")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        if sunday_alias is not None:
            if start == sunday_alias:
                start = 0
            if end == sunday_alias:
                end = 0
        if start < minimum or end > maximum:
            raise ScheduledTaskError(f"cron 字段超出范围：{part}")
        if start <= end:
            values.update(range(start, end + 1, step))
        else:
            wrapped = list(range(start, maximum + 1, step)) + list(range(minimum, end + 1, step))
            values.update(wrapped)
    if not values:
        raise ScheduledTaskError("cron 字段不能为空。")
    return values
