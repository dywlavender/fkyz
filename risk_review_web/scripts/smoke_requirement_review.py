from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
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

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="risk-requirement-review-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        outputs_dir = temp_root / "outputs"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "PORT": str(port),
            "RISK_REVIEW_MOCK": "1",
            "RISK_REVIEW_STATE_DIR": str(temp_root / "state"),
            "RISK_REVIEW_OUTPUTS_DIR": str(outputs_dir),
            "RISK_REVIEW_LOG_DIR": str(temp_root / "logs"),
            "RISK_REVIEW_STATE_SEED": "0",
        }
        process: subprocess.Popen[str] = start_server(environment)
        try:
            wait_for_server(base_url, process, 30)
            response = request_json(
                base_url,
                "/api/requirement-review",
                {
                    "requirement": "读取授信申请接口的申请时间，并校验字段和枚举口径。",
                    "apiNames": ["授信申请接口"],
                },
            )
            task_id = str(response.get("taskId") or "")
            if not task_id:
                raise AssertionError("需求评审提交响应缺少 taskId")
            trace_id = str((response.get("task") or {}).get("traceId") or "")
            if not trace_id:
                raise AssertionError("需求评审任务缺少 traceId")
            task = wait_for_task(base_url, task_id, 30)
            if task.get("status") != "done":
                raise AssertionError(f"需求评审未完成：{task}")
            result = task.get("result") or {}
            report_file_id = str(result.get("reviewFileId") or "")
            metadata_file_id = str(result.get("reviewMetadataFileId") or "")
            report_path = outputs_dir / report_file_id
            metadata_path = outputs_dir / metadata_file_id
            if not report_path.is_file() or not metadata_path.is_file():
                raise AssertionError("需求评审未在独立目录生成 Markdown 和 Metadata")
            report = report_path.read_text(encoding="utf-8")
            for heading in ("# 需求评审报告", "## 总体结论", "## 问题清单"):
                if heading not in report:
                    raise AssertionError(f"需求评审报告缺少章节：{heading}")
            if "| 总体结论 | ✅ 通过 |" not in report:
                raise AssertionError("需求评审 Mock 报告缺少新版结论表。")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("schema") != "risk_requirement_review_metadata_v1":
                raise AssertionError("需求评审 Metadata schema 无效")
            if metadata.get("conclusion") != "pass":
                raise AssertionError("需求评审新版「✅ 通过」结论未正确归一化为 pass")
            if metadata.get("reportPath") != report_file_id:
                raise AssertionError("需求评审 Metadata 报告路径不一致")
            log_files = sorted((temp_root / "logs").glob("application-*.log"))
            if len(log_files) != 1:
                raise AssertionError("需求评审未生成当日统一日志文件")
            persisted_log = log_files[0].read_text(encoding="utf-8")
            if f"traceId={trace_id}" not in persisted_log or f"taskId={task_id}" not in persisted_log:
                raise AssertionError("需求评审文件日志未按 traceId 串联任务")
            for message in ("需求评审开始", "需求评审完成"):
                if message not in persisted_log:
                    raise AssertionError(f"需求评审文件日志缺少关键步骤：{message}")
            review_records = request_json(base_url, "/api/review-records").get("data") or {}
            if any(str(item.get("taskId") or "") == task_id for item in review_records.get("items") or []):
                raise AssertionError("需求评审任务不应进入评审历史记录")

            try:
                request_json(base_url, "/api/requirement-review", {"requirement": "缺少接口", "apiNames": []})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
            else:
                raise AssertionError("未选择接口文档时需求评审不应提交成功")
        finally:
            terminate_process(process)
        print("[PASS] 需求评审 Mock 冒烟（独立产物、完整 Markdown、无历史记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
