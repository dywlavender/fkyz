from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_STATUSES = {"done", "error", "cancelled"}
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    base_url: str,
    path: str,
    payload: dict | None = None,
    *,
    method: str | None = None,
) -> dict:
    data = None
    headers = {}
    request_method = method or "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        request_method = method or "POST"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=request_method,
    )
    with LOCAL_OPENER.open(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base_url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(f"Mock 服务启动失败。\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            health = request_json(base_url, "/api/health")
            if not (health.get("checks") or {}).get("api_docs_loaded"):
                raise RuntimeError(f"API 文档服务未正确装配：{health.get('api_docs') or health}")
            return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.1)
    raise TimeoutError("等待 Mock 服务启动超时。")


def wait_for_task(base_url: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = request_json(base_url, f"/api/tasks/{task_id}").get("task") or {}
        if task.get("status") in TERMINAL_STATUSES:
            return task
        time.sleep(0.1)
    raise TimeoutError(f"等待任务完成超时：{task_id}")


def require_done(task: dict, phase: str) -> dict:
    if task.get("status") != "done":
        result = task.get("result") or {}
        raise AssertionError(
            f"{phase} 未完成：status={task.get('status')}，error={result.get('error') or result}"
        )
    return task.get("result") or {}


def run_phase1(base_url: str, outputs_dir: Path, timeout: float) -> dict:
    response = request_json(
        base_url,
        "/api/review/phase1",
        {
            "requirement": "选取开票月份大于等于进件时点减12个月且小于等于进件时点的采购金额并求和",
            "apiNames": ["授信申请接口", "ES_医药企业药品采购信息查询接口"],
        },
    )
    task = wait_for_task(base_url, str(response["taskId"]), timeout)
    result = dict(require_done(task, "Phase 1"))
    result["_taskId"] = str(response["taskId"])
    standard_file = outputs_dir / str(result.get("standardFileId") or "")
    metadata_file = outputs_dir / str(result.get("standardMetadataFileId") or "")
    if not standard_file.is_file() or not metadata_file.is_file():
        raise AssertionError("Phase 1 未生成标准文件和元数据文件。")
    standard_text = standard_file.read_text(encoding="utf-8")
    for heading in ("## 因子加工输入字段", "## 标准加工逻辑", "## 默认值兜底"):
        if heading not in standard_text:
            raise AssertionError(f"Phase 1 标准缺少章节：{heading}")
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    if not metadata.get("phase1_spec", {}).get("spec_version"):
        raise AssertionError("Phase 1 元数据缺少规范版本。")
    return result


def run_phase2(
    base_url: str,
    outputs_dir: Path,
    standard_file_id: str,
    timeout: float,
) -> dict:
    response = request_json(
        base_url,
        "/api/review/phase2",
        {
            "requirement": "筛选进件时点前12个月内的采购记录并汇总采购金额",
            "draft": "按进件时点计算12个月时间范围，筛选采购明细并汇总采购金额，异常时返回默认值。",
            "standardFileId": standard_file_id,
        },
    )
    task = wait_for_task(base_url, str(response["taskId"]), timeout)
    result = dict(require_done(task, "Phase 2"))
    result["_taskId"] = str(response["taskId"])
    review_file = outputs_dir / str(result.get("reviewFileId") or "")
    if not review_file.is_file():
        raise AssertionError("Phase 2 未生成评审文件。")
    review_text = review_file.read_text(encoding="utf-8")
    for heading in ("## 总体结论", "## 汇总问题清单"):
        if heading not in review_text:
            raise AssertionError(f"Phase 2 报告缺少章节：{heading}")
    return result


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start_server(environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "risk_review_web.server_v2"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def verify_persisted_tasks(
    base_url: str,
    task_ids: list[str],
    *,
    outputs_dir: Path,
    standard_file_id: str,
) -> None:
    listed = request_json(base_url, "/api/tasks?scope=all").get("tasks") or []
    by_id = {str(item.get("id") or ""): item for item in listed}
    for task_id in task_ids:
        task = by_id.get(task_id)
        if not task:
            raise AssertionError(f"服务重启后任务中心缺少历史任务：{task_id}")
        if not task.get("persisted"):
            raise AssertionError(f"历史任务未标记为持久化记录：{task_id}")
        detail = request_json(base_url, f"/api/tasks/{task_id}").get("task") or {}
        if str(detail.get("id") or "") != task_id or not detail.get("persisted"):
            raise AssertionError(f"历史任务详情无法按 taskId 读取：{task_id}")
    if len(task_ids) > 1:
        request_json(base_url, f"/api/tasks/{task_ids[-1]}", method="DELETE")
        try:
            request_json(base_url, f"/api/tasks/{task_ids[-1]}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        else:
            raise AssertionError("历史任务删除后仍可读取")
        if not (outputs_dir / standard_file_id).is_file():
            raise AssertionError("删除 Phase 2 历史任务时误删了 Phase 1 共享标准文件")


def main() -> int:
    parser = argparse.ArgumentParser(description="在隔离临时目录中执行 Phase 1/Phase 2 Mock 冒烟。")
    parser.add_argument("--phase", choices=("phase1", "phase2", "all"), default="all")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="risk-review-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        state_dir = temp_root / "state"
        outputs_dir = temp_root / "outputs"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "PORT": str(port),
            "RISK_REVIEW_MOCK": "1",
            "RISK_REVIEW_STATE_DIR": str(state_dir),
            "RISK_REVIEW_OUTPUTS_DIR": str(outputs_dir),
            "RISK_REVIEW_LOG_DIR": str(temp_root / "logs"),
            "RISK_REVIEW_STATE_SEED": "0",
        }
        process = start_server(environment)
        completed = []
        task_ids = []
        try:
            wait_for_server(base_url, process, args.timeout)
            phase1_result = run_phase1(base_url, outputs_dir, args.timeout)
            completed = ["Phase 1"]
            task_ids.append(str(phase1_result["_taskId"]))
            if args.phase in {"phase2", "all"}:
                phase2_result = run_phase2(
                    base_url,
                    outputs_dir,
                    str(phase1_result["standardFileId"]),
                    args.timeout,
                )
                task_ids.append(str(phase2_result["_taskId"]))
                completed.append("Phase 2")
            if args.phase == "phase2":
                completed = ["Phase 2（含隔离的 Phase 1 前置标准）"]
            request_json(base_url, "/api/tasks?scope=all")
        finally:
            terminate_process(process)
        restarted = start_server(environment)
        try:
            wait_for_server(base_url, restarted, args.timeout)
            verify_persisted_tasks(
                base_url,
                task_ids,
                outputs_dir=outputs_dir,
                standard_file_id=str(phase1_result["standardFileId"]),
            )
        finally:
            terminate_process(restarted)
        print("[PASS] " + "、".join(completed) + " Mock 冒烟（含任务持久化）")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
