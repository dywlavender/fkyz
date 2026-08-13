from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAYWRIGHT_CLI = (
    Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh"
)
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, process: subprocess.Popen[str], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(f"Mock 服务启动失败。\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            with LOCAL_OPENER.open(f"{base_url}/api/self-check", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    raise TimeoutError(f"等待 Mock 服务启动超时。\nstdout:\n{stdout}\nstderr:\n{stderr}")


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_cli(cli: Path, session: str, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(cli), f"-s={session}", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        timeout=90,
    )
    if "### Error" in result.stdout or "### Error" in result.stderr:
        raise RuntimeError((result.stdout + result.stderr).strip())
    return result


def main() -> int:
    cli = Path(os.environ.get("PLAYWRIGHT_CLI") or DEFAULT_PLAYWRIGHT_CLI)
    if not cli.is_file():
        raise FileNotFoundError(
            f"未找到 Playwright CLI 包装脚本：{cli}。"
            "可通过 PLAYWRIGHT_CLI 指定其他路径。"
        )

    with tempfile.TemporaryDirectory(prefix="risk-review-playwright-") as temp_dir:
        temp_root = Path(temp_dir)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        session = f"risk-review-{uuid.uuid4().hex[:10]}"
        environment = {
            **os.environ,
            "PORT": str(port),
            "RISK_REVIEW_MOCK": "1",
            "RISK_REVIEW_STATE_DIR": str(temp_root / "state"),
            "RISK_REVIEW_OUTPUTS_DIR": str(temp_root / "outputs"),
            "RISK_REVIEW_LOG_DIR": str(temp_root / "logs"),
            "RISK_REVIEW_STATE_SEED": "0",
        }
        server = subprocess.Popen(
            [sys.executable, "-m", "risk_review_web.server_v2"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_for_server(base_url, server)
            run_cli(cli, session, temp_root, "open", base_url)
            run_cli(cli, session, temp_root, "resize", "1440", "1000")
            code = r"""async page => {
const factorName = '桌面端 Playwright 主链';
await page.getByRole('textbox', { name: '* 因子名称' }).fill(factorName);
await page.getByRole('textbox', { name: '* 需求内容' }).fill('选取开票月份位于进件时点前12个月至进件时点之间的采购金额并求和');
await page.getByRole('textbox', { name: '因子加工输入字段' }).fill('授信申请接口.applyTime\nES_医药企业药品采购信息查询接口.content[].kpyf\nES_医药企业药品采购信息查询接口.content[].inputAmount');
await page.getByRole('textbox', { name: '* 加工逻辑' }).fill('将进件时点转换为月份并前推12个月。遍历采购明细，保留开票月份位于起止月份之间的记录，对采购金额求和。');
const fallback = page.getByRole('textbox', { name: '默认值' });
await fallback.fill('异常时返回配置默认值');
if (await fallback.inputValue() !== '异常时返回配置默认值') throw new Error('默认值输入框不可编辑');
const apiSelect = page.getByRole('combobox', { name: /关联接口文档/ });
await apiSelect.click();
await page.getByTitle('ES_医药企业药品采购信息查询接口 · 外部接口').click();
await apiSelect.click();
await page.getByTitle('授信申请接口 · 本系统接口').click();
await page.getByRole('button', { name: '开始评审' }).click();
await page.getByRole('menuitem', { name: '批量评审' }).click();
await page.getByRole('heading', { name: '批量评审' }).waitFor();
await page.getByRole('menuitem', { name: '单因子评审' }).click();
if (await page.getByRole('textbox', { name: '* 因子名称' }).inputValue() !== factorName) throw new Error('切页后单因子输入丢失');
await page.getByText('已完成', { exact: true }).first().waitFor({ timeout: 30000 });
await page.getByRole('heading', { name: '总体结论' }).waitFor({ timeout: 30000 });
await page.getByRole('button', { name: '执行详情' }).click();
const executionDialog = page.getByRole('dialog', { name: '执行日志' });
await executionDialog.getByText('生成标准', { exact: true }).first().waitFor();
await executionDialog.getByText('执行评审', { exact: true }).first().waitFor();
await executionDialog.getByRole('tab', { name: /模型输入\/输出\/工具调用/ }).click();
await executionDialog.getByRole('button', { name: 'Close' }).click();
await page.getByRole('button', { name: '任务中心' }).click();
const taskDialog = page.getByRole('dialog', { name: '任务中心' });
await taskDialog.getByText(factorName, { exact: true }).first().waitFor();
await taskDialog.getByRole('button', { name: 'Close' }).click();
await page.getByRole('menuitem', { name: '需求评审' }).click();
await page.getByRole('heading', { name: '需求评审', exact: true }).waitFor();
const requirementApiSelect = page.locator('.requirement-review-page .ant-select-selector').first();
await requirementApiSelect.click();
await page.locator('.ant-select-dropdown:visible').getByTitle('授信申请接口 · 本系统接口').click();
await page.locator('.requirement-review-page textarea').fill('读取授信申请接口的申请时间，并校验字段、枚举和异常处理口径。');
const requirementPage = page.locator('.requirement-review-page');
await requirementPage.getByRole('button', { name: '开始评审' }).click();
await requirementPage.getByText('已完成', { exact: true }).waitFor({ timeout: 30000 });
await requirementPage.getByRole('heading', { name: '需求评审报告' }).waitFor({ timeout: 30000 });
await requirementPage.getByRole('heading', { name: '总体结论' }).waitFor();
await requirementPage.getByRole('button', { name: '执行详情' }).click();
const requirementLogDialog = page.getByRole('dialog', { name: '执行日志' });
await requirementLogDialog.getByRole('tab', { name: /运行日志/ }).waitFor();
await requirementLogDialog.getByText('需求评审', { exact: true }).first().waitFor();
await requirementLogDialog.getByRole('tab', { name: /模型输入\/输出\/工具调用/ }).click();
await requirementLogDialog.getByText('暂无执行日志。').waitFor();
await requirementLogDialog.getByRole('button', { name: 'Close' }).click();
await page.getByRole('menuitem', { name: /评审记录/ }).click();
await page.getByRole('row', { name: new RegExp(factorName) }).first().waitFor({ timeout: 30000 });
return { page: page.url(), factorName };
}"""
            result = run_cli(cli, session, temp_root, "run-code", code)
            print(result.stdout.strip())
            print("[PASS] 桌面端 Playwright 主链")
            return 0
        finally:
            try:
                run_cli(cli, session, temp_root, "close")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            terminate_process(server)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stdout or "") + (exc.stderr or "")
            print(f"[FAIL] Playwright 主链失败\n{detail.strip()}")
        else:
            print(f"[FAIL] {exc}")
        raise SystemExit(1)
