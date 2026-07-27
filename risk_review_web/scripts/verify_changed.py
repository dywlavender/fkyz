from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "risk_review_web"
FRONTEND_ROOT = PACKAGE_ROOT / "web"


def git_lines(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def changed_files(base: str | None) -> set[str]:
    paths = git_lines("diff", "--name-only", *(base.split() if base else ()))
    paths.update(git_lines("diff", "--cached", "--name-only"))
    paths.update(git_lines("ls-files", "--others", "--exclude-standard"))
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    for line in status.splitlines():
        candidate = line[3:].strip()
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1]
        if candidate:
            paths.add(candidate)
    return paths


def run(label: str, command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    print(f"[RUN ] {label}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)
    print(f"[PASS] {label}", flush=True)


def existing_python_files(paths: set[str]) -> list[str]:
    files = []
    for relative in sorted(paths):
        if not relative.endswith(".py"):
            continue
        path = PROJECT_ROOT / relative
        if path.is_file():
            files.append(relative)
    return files


def matches_any(path: str, prefixes: tuple[str, ...], names: tuple[str, ...] = ()) -> bool:
    return path.startswith(prefixes) or path in names


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 Git 改动范围执行风控评审平台验证。")
    parser.add_argument("--all", action="store_true", help="执行全部验证。")
    parser.add_argument("--base", help="额外比较的 Git 基准，例如 HEAD~1。")
    parser.add_argument("--list", action="store_true", help="仅输出识别到的改动文件。")
    args = parser.parse_args()

    paths = changed_files(args.base)
    if args.list:
        print("\n".join(sorted(paths)))
        return 0

    if not paths and not args.all:
        print("[SKIP] 工作区没有可验证改动。")
        return 0

    phase1 = args.all or any(
        matches_any(
            path,
            (
                "risk_review_web/prompts/phase1.md",
                "risk_review_web/prompts/risk-review-skill/phase/phase1_",
                "risk_review_web/prompts/risk-review-skill/spec/phase1_",
            ),
        )
        for path in paths
    )
    phase2 = args.all or any(
        matches_any(
            path,
            (
                "risk_review_web/prompts/phase2.md",
                "risk_review_web/prompts/risk-review-skill/phase/phase2_",
                "risk_review_web/prompts/risk-review-skill/spec/phase2_",
            ),
        )
        for path in paths
    )
    spec_renderer = args.all or any(
        path == "risk_review_web/prompts/risk-review-skill/build/build_prompt.py"
        for path in paths
    )
    if spec_renderer:
        phase1 = phase2 = True

    backend_smoke = args.all or any(
        path in {
            "risk_review_web/server_v2.py",
            "risk_review_web/artifacts.py",
            "risk_review_web/prompt_builder.py",
            "risk_review_web/paths.py",
            "risk_review_web/task_pool.py",
        }
        for path in paths
    )
    if backend_smoke:
        phase1 = phase2 = True

    frontend = args.all or any(
        matches_any(
            path,
            ("risk_review_web/web/src/", "risk_review_web/web/public/"),
            (
                "risk_review_web/web/package.json",
                "risk_review_web/web/package-lock.json",
                "risk_review_web/web/vite.config.ts",
                "risk_review_web/web/tsconfig.json",
            ),
        )
        for path in paths
    )
    desktop = args.all or frontend or "risk_review_web/scripts/playwright_desktop_smoke.py" in paths
    legacy = args.all or "risk_review_web/static/app.js" in paths
    golden = args.all or any(
        path in {
            "risk_review_web/data/examples.json",
            "risk_review_web/data/golden_cases.json",
            "risk_review_web/scripts/golden_gate.py",
        }
        for path in paths
    )
    python_files = (
        [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            if not {"node_modules", ".local", ".venv"} & set(path.parts)
        ]
        if args.all
        else existing_python_files(paths)
    )

    ran = False
    if python_files:
        run("Python 编译", [sys.executable, "-m", "py_compile", *python_files])
        ran = True
    if legacy:
        run("原有功能台语法检查", ["node", "--check", "risk_review_web/static/app.js"])
        ran = True
    if phase1 or phase2:
        run(
            "生成开发规范展示文档",
            [sys.executable, "risk_review_web/prompts/risk-review-skill/build/build_prompt.py"],
        )
        smoke_phase = "all" if phase1 and phase2 else "phase1" if phase1 else "phase2"
        run(
            f"{smoke_phase} Mock 冒烟",
            [sys.executable, "risk_review_web/scripts/smoke_review.py", "--phase", smoke_phase],
        )
        ran = True
    if golden:
        run(
            "黄金案例冻结检查",
            [sys.executable, "risk_review_web/scripts/golden_gate.py"],
        )
        ran = True
    if frontend:
        run("React 类型检查", ["npm", "run", "typecheck"], cwd=FRONTEND_ROOT)
        run("React 构建", ["npm", "run", "build"], cwd=FRONTEND_ROOT)
        ran = True
    if desktop:
        run(
            "桌面端 Playwright 主链",
            [sys.executable, "risk_review_web/scripts/playwright_desktop_smoke.py"],
        )
        ran = True
    if not ran:
        print("[SKIP] 当前改动没有匹配到专项验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
