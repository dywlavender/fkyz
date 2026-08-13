from __future__ import annotations

from pathlib import Path

from .agent_runtime import run_agent


def run_agent_call(
    *,
    prompt: str,
    system_prompt: str = "",
    phase_label: str,
    workspace: Path,
    cancelled,
    event_callback=None,
) -> tuple[str, dict]:
    """Normalize an in-process Agent call to the platform task-result contract."""
    if cancelled():
        return "cancelled", {"content": "", "stderr": f"{phase_label} 已取消。", "returncode": 130}
    try:
        content = run_agent(
            prompt,
            workspace.resolve(),
            system_prompt=system_prompt,
            enable_tools=phase_label not in {"Direct Review", "Model Test"},
            event_callback=event_callback,
            cancelled=cancelled,
        )
        if cancelled():
            return "cancelled", {"content": content, "stderr": f"{phase_label} 已取消。", "returncode": 130}
        return "done", {"content": content, "stderr": "", "returncode": 0}
    except InterruptedError as exc:
        return "cancelled", {"content": "", "stderr": str(exc), "returncode": 130}
    except Exception as exc:
        return "error", {
            "content": "",
            "stderr": f"Agent 执行失败：{type(exc).__name__}: {exc}",
            "returncode": 1,
        }
