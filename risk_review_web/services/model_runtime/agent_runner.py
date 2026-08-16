from __future__ import annotations

from pathlib import Path

from risk_review_web.domain.agent_result import AgentResult
from .agent_runtime import run_agent


def run_agent_call(
    *,
    prompt: str,
    system_prompt: str = "",
    phase_label: str,
    workspace: Path,
    cancelled,
    event_callback=None,
) -> AgentResult:
    """Run one in-process Agent call and return the typed model result."""
    if cancelled():
        return AgentResult.cancelled(f"{phase_label} 已取消。")
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
            return AgentResult.cancelled(f"{phase_label} 已取消。", metadata={"partial_content": content})
        return AgentResult.succeeded(content)
    except InterruptedError as exc:
        return AgentResult.cancelled(str(exc))
    except Exception as exc:
        return AgentResult.failed(f"Agent 执行失败：{type(exc).__name__}: {exc}")
