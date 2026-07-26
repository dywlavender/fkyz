from __future__ import annotations

import json
import os
import threading
from typing import Any

from risk_review_web.paths import RUNTIME_SETTINGS_PATH
from risk_review_web.task_pool import GLOBAL_TASK_POOL
from risk_review_web.utils import atomic_write_text, read_text


RUNTIME_SETTINGS_LOCK = threading.RLock()
MAX_CONCURRENCY = 32


def _default_settings() -> dict[str, int]:
    global_concurrency = max(
        1,
        min(MAX_CONCURRENCY, int(os.environ.get("RISK_REVIEW_GLOBAL_CONCURRENCY", "3"))),
    )
    return {
        "globalConcurrency": global_concurrency,
        "batchConcurrency": global_concurrency,
        "scheduledConcurrency": global_concurrency,
    }


def _read_settings() -> dict[str, Any]:
    if not RUNTIME_SETTINGS_PATH.is_file():
        return _default_settings()
    try:
        value = json.loads(read_text(RUNTIME_SETTINGS_PATH))
    except (OSError, json.JSONDecodeError):
        return _default_settings()
    return value if isinstance(value, dict) else _default_settings()


def _normalize_settings(value: dict[str, Any]) -> dict[str, int]:
    defaults = _default_settings()
    try:
        global_concurrency = int(value.get("globalConcurrency", defaults["globalConcurrency"]))
        batch_concurrency = int(value.get("batchConcurrency", defaults["batchConcurrency"]))
        scheduled_concurrency = int(value.get("scheduledConcurrency", defaults["scheduledConcurrency"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("并发数必须是整数。") from exc
    for label, configured in (
        ("全局并发上限", global_concurrency),
        ("批量评审并发上限", batch_concurrency),
        ("定时任务并发上限", scheduled_concurrency),
    ):
        if configured < 1 or configured > MAX_CONCURRENCY:
            raise ValueError(f"{label}必须介于 1 到 {MAX_CONCURRENCY} 之间。")
    if batch_concurrency > global_concurrency:
        raise ValueError("批量评审并发上限不能超过全局并发上限。")
    if scheduled_concurrency > global_concurrency:
        raise ValueError("定时任务并发上限不能超过全局并发上限。")
    return {
        "globalConcurrency": global_concurrency,
        "batchConcurrency": batch_concurrency,
        "scheduledConcurrency": scheduled_concurrency,
    }


def get_runtime_settings() -> dict[str, int]:
    with RUNTIME_SETTINGS_LOCK:
        try:
            settings = _normalize_settings(_read_settings())
        except ValueError:
            settings = _default_settings()
        GLOBAL_TASK_POOL.resize(settings["globalConcurrency"])
        return settings


def update_runtime_settings(value: dict[str, Any]) -> dict[str, int]:
    with RUNTIME_SETTINGS_LOCK:
        current = get_runtime_settings()
        settings = _normalize_settings({**current, **value})
        RUNTIME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            RUNTIME_SETTINGS_PATH,
            json.dumps(settings, ensure_ascii=False, indent=2),
        )
        GLOBAL_TASK_POOL.resize(settings["globalConcurrency"])
        return settings


# Apply persisted settings as soon as the runtime-settings module is loaded.
get_runtime_settings()
