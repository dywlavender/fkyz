from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path


AGENT_EVENT_PREFIX = "RISK_REVIEW_AGENT_EVENT\t"
AgentEventCallback = Callable[[str, object], None]


def agent_python() -> Path:
    return Path(
        os.environ.get(
            "RISK_REVIEW_AGENT_PYTHON",
            "/opt/miniconda3/envs/langchain1.2/bin/python",
        )
    ).expanduser()


def agent_health() -> dict:
    python_path = agent_python()
    provider = os.environ.get("RISK_REVIEW_AGENT_PROVIDER", "anthropic").strip().lower()
    if provider == "anthropic":
        api_key_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    elif provider in {"openai", "openai-compatible", "openai_compatible"}:
        api_key_configured = bool(os.environ.get("OPENAI_API_KEY"))
    else:
        api_key_configured = False
    info = {
        "python": str(python_path),
        "environment_available": python_path.is_file(),
        "provider": provider,
        "model": os.environ.get("RISK_REVIEW_AGENT_MODEL", "").strip(),
        "api_key_configured": api_key_configured,
        "langchain_version": None,
        "note": "",
    }
    if not python_path.is_file():
        info["note"] = "Agent Python 不存在。"
        return info
    try:
        result = subprocess.run(
            [
                str(python_path),
                "-c",
                "import importlib.metadata as m; print(m.version('langchain'))",
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            info["langchain_version"] = result.stdout.strip()
        else:
            info["note"] = result.stderr.strip() or "无法读取 LangChain 版本。"
    except Exception as exc:
        info["note"] = str(exc)
    return info


def parse_agent_events(stderr: str, callback: AgentEventCallback | None) -> str:
    diagnostics: list[str] = []
    for line in str(stderr or "").splitlines():
        if not line.startswith(AGENT_EVENT_PREFIX):
            diagnostics.append(line)
            continue
        raw = line[len(AGENT_EVENT_PREFIX):]
        try:
            event = json.loads(raw)
            event_name = str(event.get("event") or "agent_event")
            payload = event.get("payload")
        except (TypeError, json.JSONDecodeError):
            diagnostics.append(line)
            continue
        if callback:
            callback(event_name, payload)
    return "\n".join(diagnostics).strip()


def run_agent_process(
    *,
    prompt: str,
    phase_label: str,
    workspace: Path,
    timeout_seconds: int,
    process_started: Callable[[subprocess.Popen[str]], None],
    terminate: Callable[[], None],
    cancelled: Callable[[], bool],
    event_callback: AgentEventCallback | None = None,
    worker_module: str = "risk_review_web.agent_worker",
) -> tuple[str, dict]:
    python_path = agent_python()
    cmd = [
        str(python_path),
        "-m",
        worker_module,
        "--phase",
        phase_label,
        "--workspace",
        str(workspace.resolve()),
    ]
    child_env = dict(os.environ)
    project_root = str(Path(__file__).resolve().parents[3])
    existing_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        project_root if not existing_pythonpath else f"{project_root}{os.pathsep}{existing_pythonpath}"
    )
    popen_kwargs: dict = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    if cancelled():
        return "cancelled", {
            "content": "",
            "stderr": f"{phase_label} 已取消。",
            "returncode": 130,
        }
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace.resolve()),
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **popen_kwargs,
        )
        process_started(proc)
        diagnostic_lines: list[str] = []
        final_content: list[str] = []

        def dispatch_event(event: str, payload: object) -> None:
            if event == "agent_final" and isinstance(payload, dict):
                markdown = str(payload.get("markdown") or "").strip()
                if markdown:
                    final_content.append(markdown)
            if event_callback:
                event_callback(event, payload)

        def read_stderr() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                try:
                    diagnostic = parse_agent_events(line, dispatch_event)
                except Exception as exc:
                    diagnostic = f"Agent 日志回调异常：{type(exc).__name__}: {exc}"
                if diagnostic:
                    diagnostic_lines.append(diagnostic)

        stderr_thread = threading.Thread(target=read_stderr, name="agent-stderr-reader", daemon=True)
        stderr_thread.start()
        if proc.stdin is not None:
            try:
                proc.stdin.write(prompt)
                proc.stdin.flush()
            finally:
                proc.stdin.close()

        deadline = time.monotonic() + max(1, timeout_seconds)
        timed_out = False
        was_cancelled = False
        while proc.poll() is None:
            if cancelled():
                was_cancelled = True
                terminate()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                terminate()
                break
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        stderr_thread.join(timeout=3)
        if proc.stderr is not None:
            proc.stderr.close()
        content = final_content[-1] if final_content else ""
        diagnostics = "\n".join(diagnostic_lines).strip()
        if timed_out:
            return "timeout", {
                "content": content,
                "stderr": diagnostics or "Agent 调用超时。",
                "returncode": 124,
            }
        if was_cancelled or cancelled():
            return "cancelled", {
                "content": content,
                "stderr": diagnostics or "Agent 调用已取消。",
                "returncode": proc.returncode if proc.returncode is not None else 130,
            }
        if proc.returncode == 0 and not content:
            return "error", {
                "content": "",
                "stderr": diagnostics or "Agent 未返回最终响应事件。",
                "returncode": 1,
            }
        return ("done" if proc.returncode == 0 else "error"), {
            "content": content,
            "stderr": diagnostics,
            "returncode": proc.returncode,
        }
    except FileNotFoundError:
        return "error", {
            "content": "",
            "stderr": f"未找到 Agent Python：{python_path}",
            "returncode": 127,
        }
    except Exception as exc:
        return "error", {
            "content": "",
            "stderr": str(exc),
            "returncode": 1,
        }
