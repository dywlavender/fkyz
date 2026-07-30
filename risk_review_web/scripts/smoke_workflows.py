from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from risk_review_web.scripts.smoke_review import (
    free_port,
    request_json,
    start_server,
    terminate_process,
    wait_for_server,
    wait_for_task,
)


TERMINAL_RUN_STATUSES = {"success", "partial_success", "failed", "cancelled"}


def api_data(payload: dict):
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def wait_for_scheduled_run(base_url: str, run_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api_data(request_json(base_url, f"/api/scheduled-runs/{run_id}")) or {}
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.1)
    raise TimeoutError(f"等待定时任务运行超时：{run_id}")


def scheduled_payload(name: str, *, schedule_type: str, run_date: str = "", run_time: str = "02:00") -> dict:
    return {
        "taskName": name,
        "enabled": True,
        "scheduleType": schedule_type,
        "runDate": run_date,
        "runTime": run_time,
        "weekday": "1",
        "monthDay": "1",
        "cron": "0 2 * * *",
        "mode": "phase1_phase2",
        "runtimeMode": "mock",
        "failurePolicy": "continue",
        "standardFile": "",
        "items": [{
            "factorName": "工作流冒烟因子",
            "requirement": "统计申请时点前近12个月的企业采购金额总和。",
            "reviewContent": "按申请时点计算近12个月窗口并汇总采购金额，异常返回0。",
            "apiDocIds": ["授信申请接口", "ES_医药企业药品采购信息查询接口"],
        }],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="risk-workflow-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "PORT": str(port),
            "RISK_REVIEW_MOCK": "1",
            "RISK_REVIEW_STATE_DIR": str(temp_root / "state"),
            "RISK_REVIEW_OUTPUTS_DIR": str(temp_root / "outputs"),
            "RISK_REVIEW_LOG_DIR": str(temp_root / "logs"),
            "RISK_REVIEW_STATE_SEED": "0",
            "RISK_REVIEW_SCHEDULER_POLL": "1",
        }
        process: subprocess.Popen[str] = start_server(environment)
        try:
            wait_for_server(base_url, process, 30)

            settings = api_data(request_json(base_url, "/api/runtime-settings")) or {}
            if settings.get("modelBackend") != "claude":
                raise AssertionError(f"模型后端首次默认值错误：{settings}")
            agent_settings = api_data(request_json(
                base_url,
                "/api/runtime-settings",
                {**settings, "modelBackend": "agent"},
                method="PUT",
            )) or {}
            health = request_json(base_url, "/api/health")
            if agent_settings.get("modelBackend") != "agent" or (health.get("modelRuntime") or {}).get("selectedBackend") != "agent":
                raise AssertionError(f"模型后端开关未生效：settings={agent_settings}, health={health}")
            request_json(
                base_url,
                "/api/runtime-settings",
                {**agent_settings, "modelBackend": "claude"},
                method="PUT",
            )

            rows = [
                {
                    "factorName": f"批量冒烟因子{index}",
                    "requirement": "统计申请时点前近12个月的企业采购金额总和。",
                    "apiNames": ["授信申请接口", "ES_医药企业药品采购信息查询接口"],
                    "procLogic": "按申请时点计算近12个月窗口并汇总采购金额。",
                }
                for index in (1, 2)
            ]
            batch = request_json(base_url, "/api/review/batch", {"rows": rows, "mock": True})
            batch_task = wait_for_task(base_url, str(batch["taskId"]), 30)
            batch_items = (batch_task.get("result") or {}).get("items") or []
            if batch_task.get("status") != "done" or len(batch_items) != 2:
                raise AssertionError(f"多行批量评审失败：{batch_task}")

            examples = json.loads((PROJECT_ROOT / "risk_review_web/data/examples.json").read_text(encoding="utf-8"))
            example_id = str(examples[0]["id"])
            regression = request_json(
                base_url,
                "/api/validation/regression",
                {"exampleIds": [example_id], "mock": True},
            )
            regression_task = wait_for_task(base_url, str(regression["taskId"]), 30)
            if regression_task.get("status") != "done":
                raise AssertionError(f"回归案例执行失败：{regression_task}")

            manual_task = api_data(request_json(
                base_url,
                "/api/scheduled-tasks",
                scheduled_payload("手动触发冒烟", schedule_type="daily"),
            ))
            manual_run = api_data(request_json(
                base_url,
                f"/api/scheduled-tasks/{manual_task['taskId']}/run-now",
                {},
            ))
            manual_finished = wait_for_scheduled_run(base_url, str(manual_run["runId"]))
            if manual_finished.get("status") != "success" or manual_finished.get("trigger") != "manual":
                raise AssertionError(f"定时任务手动触发失败：{manual_finished}")

            now = datetime.now().astimezone()
            automatic_task = api_data(request_json(
                base_url,
                "/api/scheduled-tasks",
                scheduled_payload(
                    "自动触发冒烟",
                    schedule_type="once",
                    run_date=now.strftime("%Y-%m-%d"),
                    run_time=now.strftime("%H:%M"),
                ),
            ))
            deadline = time.monotonic() + 30
            automatic_finished = None
            while time.monotonic() < deadline:
                runs = api_data(request_json(
                    base_url,
                    f"/api/scheduled-tasks/{automatic_task['taskId']}/runs",
                )) or []
                if runs:
                    automatic_finished = wait_for_scheduled_run(base_url, str(runs[0]["runId"]))
                    break
                time.sleep(0.2)
            if not automatic_finished:
                raise AssertionError("到期自动触发未产生运行记录。")
            if automatic_finished.get("status") != "success" or automatic_finished.get("trigger") != "scheduler":
                raise AssertionError(f"到期自动触发失败：{automatic_finished}")
        finally:
            terminate_process(process)
    print("[PASS] 模型后端开关、多行批量、单案例回归、定时任务手动与到期自动触发")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
