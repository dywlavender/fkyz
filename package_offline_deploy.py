#!/usr/bin/env python3
"""Build a source-only offline deployment ZIP for the risk review web app."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
APP_DIR = PROJECT_ROOT / "risk_review_web"

RUNTIME_ROOT_FILES = (
    "start_v2.sh",
    ".env.example",
    "requirements.txt",
)
RUNTIME_TREES = (
    "risk_review_web",
    "raw/api",
)
ALWAYS_EXCLUDED_PARTS = {
    ".DS_Store",
    ".git",
    ".idea",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
    "tests",
    "web",
    "complex_factor_cases",
    "scripts",
}
ALWAYS_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".ipynb"}
ALWAYS_EXCLUDED_FILES = {
    "risk_review_web/README.md",
    "risk_review_web/data/golden_cases.json",
}
RUNTIME_DATA_PREFIXES = (
    Path("outputs"),
    Path("risk_review_web/.local"),
    Path("risk_review_web/local"),
    Path("risk_review_web/data/review_records.json"),
    Path("risk_review_web/data/review_mode_experiments.json"),
    Path("risk_review_web/data/runtime_settings.json"),
    Path("risk_review_web/data/scheduled_runs.json"),
    Path("risk_review_web/data/scheduled_tasks.json"),
    Path("risk_review_web/data/validation_runs.json"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打包风控 AI 评审平台的离线运行 ZIP（不包含 node_modules）。"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "deployment_packages",
        help="ZIP 输出目录（默认：deployment_packages）。",
    )
    parser.add_argument(
        "--name",
        help="ZIP 文件名（可省略 .zip）；默认包含当前时间。",
    )
    parser.add_argument(
        "--include-runtime-data",
        action="store_true",
        help="同时打包 outputs、运行状态和日志。可能包含敏感业务数据，默认关闭。",
    )
    return parser.parse_args()


def fail(message: str) -> "NoReturn":
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(1)


def validate_frontend() -> None:
    index_path = APP_DIR / "static" / "dist" / "index.html"
    if not index_path.is_file():
        fail(
            "缺少 risk_review_web/static/dist/index.html。"
            "请先在 risk_review_web/web 下执行 npm ci && npm run build。"
        )
    assets_dir = index_path.parent / "assets"
    if not assets_dir.is_dir() or not any(path.is_file() for path in assets_dir.rglob("*")):
        fail("前端构建产物不完整：risk_review_web/static/dist/assets 为空。")


def is_runtime_data(relative: Path) -> bool:
    return any(relative == prefix or prefix in relative.parents for prefix in RUNTIME_DATA_PREFIXES)


def should_include(relative: Path, *, include_runtime_data: bool) -> bool:
    if relative.as_posix() in ALWAYS_EXCLUDED_FILES:
        return False
    if any(part in ALWAYS_EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative.suffix.lower() in ALWAYS_EXCLUDED_SUFFIXES:
        return False
    if is_runtime_data(relative) and not include_runtime_data:
        return False
    return True


def collect_files(*, include_runtime_data: bool) -> list[Path]:
    candidates: list[Path] = []
    for file_name in RUNTIME_ROOT_FILES:
        path = PROJECT_ROOT / file_name
        if path.is_file():
            candidates.append(path)

    for tree_name in RUNTIME_TREES:
        tree = PROJECT_ROOT / tree_name
        if not tree.exists():
            if tree_name == "raw/api":
                print("警告：raw/api 不存在，部署包将不包含接口文档。", file=sys.stderr)
            continue
        candidates.extend(path for path in tree.rglob("*") if path.is_file())

    if include_runtime_data:
        for prefix in RUNTIME_DATA_PREFIXES:
            tree = PROJECT_ROOT / prefix
            if tree.is_dir():
                candidates.extend(path for path in tree.rglob("*") if path.is_file())

    included: dict[str, Path] = {}
    for path in candidates:
        relative = path.relative_to(PROJECT_ROOT)
        if not should_include(relative, include_runtime_data=include_runtime_data):
            continue
        if path.is_symlink():
            fail(f"拒绝打包符号链接：{relative}")
        included[relative.as_posix()] = path
    return [included[key] for key in sorted(included)]


def git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"


def deployment_readme(*, include_runtime_data: bool, revision: str) -> str:
    data_note = "已包含原环境运行数据。" if include_runtime_data else "未包含历史产物、运行状态和日志。"
    return f"""风控 AI 评审平台——离线部署包

打包时间：{datetime.now().astimezone().isoformat(timespec='seconds')}
Git 版本：{revision}
数据范围：{data_note}

部署步骤：
1. 将 ZIP 解压到目标目录。
2. 复制 .env.example 为 .env，并按现场环境填写Agent Provider、模型名和凭据。
   不要把真实密钥写回部署 ZIP。
3. 确认目标机存在 /opt/miniconda3/envs/langchain1.2/bin/python，且已安装 requirements.txt 中的依赖。
   如果目标机不能联网，需提前准备与目标 Python 版本、操作系统和 CPU 匹配的 wheelhouse，
   然后执行：python -m pip install --no-index --find-links wheelhouse -r requirements.txt
4. 执行：bash start_v2.sh 8765。若 Python 路径不同，修改 start_v2.sh 最后一行。
5. 浏览器访问：http://127.0.0.1:8765/

说明：
- 前端已预编译在 risk_review_web/static/dist/，运行不需要 Node.js 或 node_modules。
- 本 ZIP 是应用运行包，不内置跨平台 Python wheel；Python 依赖必须在目标环境预装或另配 wheelhouse。
- raw/api/ 是 Phase 1 使用的接口文档，请按业务需要核对内容。
- 服务会自动创建 outputs/ 和 risk_review_web/.local/state/。
- 当前任务池只支持单个 Python 后端进程，不要多进程同时共享运行目录。
- MANIFEST.sha256 可用于核验解压后文件完整性。
"""


def zip_info(archive_name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def write_zip(output_path: Path, files: list[Path], *, include_runtime_data: bool) -> None:
    bundle_root = output_path.stem
    revision = git_revision()
    manifest_lines: list[str] = []

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            manifest_lines.append(f"{digest}  {relative}")
            mode = 0o755 if relative == "start_v2.sh" or os.access(path, os.X_OK) else 0o644
            archive.writestr(zip_info(f"{bundle_root}/{relative}", mode), data)

        readme = deployment_readme(
            include_runtime_data=include_runtime_data,
            revision=revision,
        ).encode("utf-8")
        readme_name = "OFFLINE_DEPLOY_README.txt"
        manifest_lines.append(f"{hashlib.sha256(readme).hexdigest()}  {readme_name}")
        archive.writestr(zip_info(f"{bundle_root}/{readme_name}"), readme)

        manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
        archive.writestr(zip_info(f"{bundle_root}/MANIFEST.sha256"), manifest)


def main() -> int:
    args = parse_args()
    validate_frontend()
    files = collect_files(include_runtime_data=args.include_runtime_data)
    if not files:
        fail("没有找到可打包的运行文件。")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    file_name = args.name or f"risk-review-offline-{timestamp}"
    if not file_name.lower().endswith(".zip"):
        file_name += ".zip"
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / Path(file_name).name
    if output_path.exists():
        fail(f"目标文件已存在，拒绝覆盖：{output_path}")

    write_zip(output_path, files, include_runtime_data=args.include_runtime_data)
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"打包完成：{output_path}")
    print(f"文件数量：{len(files) + 2}")
    print(f"ZIP 大小：{size_mb:.2f} MiB")
    print("已排除：node_modules、web 前端源码、测试、缓存、.env 和 Git 文件。")
    if not args.include_runtime_data:
        print("已排除：outputs、运行状态和日志（可用 --include-runtime-data 显式包含）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
