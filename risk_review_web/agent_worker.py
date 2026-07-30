from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from langchain.chat_models import init_chat_model


from risk_review_web.environment import load_project_env


EVENT_PREFIX = "RISK_REVIEW_AGENT_EVENT\t"
MAX_FILE_CHARS = 200_000
MAX_GLOB_RESULTS = 500
MAX_GREP_MATCHES = 500


def emit(event: str, payload: object) -> None:
    record = json.dumps({"event": event, "payload": payload}, ensure_ascii=False, default=str)
    sys.stderr.write(EVENT_PREFIX + record + "\n")
    sys.stderr.flush()


def safe_relative_path(workspace: Path, raw_path: str, *, must_exist: bool = True) -> Path:
    value = str(raw_path or "").strip()
    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("只允许访问隔离目录内的相对路径。")
    unresolved = workspace / candidate
    current = workspace
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("不允许通过符号链接读取文件。")
    resolved = unresolved.resolve(strict=must_exist)
    resolved.relative_to(workspace)
    return resolved


def build_tools(workspace: Path):
    from langchain_core.tools import tool

    @tool
    def read_file(path: str) -> str:
        """Read one UTF-8 text file from the isolated workspace by relative path."""
        resolved = safe_relative_path(workspace, path)
        if not resolved.is_file():
            raise ValueError("目标不是普通文件。")
        text = resolved.read_text(encoding="utf-8")
        if len(text) > MAX_FILE_CHARS:
            raise ValueError(f"文件超过单次读取上限 {MAX_FILE_CHARS} 字符。")
        return text

    @tool
    def glob_files(pattern: str) -> str:
        """List files matching a glob pattern inside the isolated workspace."""
        value = str(pattern or "").strip()
        if not value or Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("Glob 只允许隔离目录内的相对模式。")
        matches: list[str] = []
        for path in workspace.glob(value):
            resolved = path.resolve(strict=True)
            resolved.relative_to(workspace)
            if resolved.is_file() and not path.is_symlink():
                matches.append(resolved.relative_to(workspace).as_posix())
                if len(matches) >= MAX_GLOB_RESULTS:
                    break
        return "\n".join(sorted(matches))

    @tool
    def grep_files(query: str, pattern: str = "**/*") -> str:
        """Search text files in the isolated workspace using a regular expression."""
        matcher = re.compile(str(query or ""))
        file_list = glob_files.invoke({"pattern": pattern}).splitlines()
        matches: list[str] = []
        for relative in file_list:
            path = safe_relative_path(workspace, relative)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if matcher.search(line):
                    matches.append(f"{relative}:{line_number}:{line}")
                    if len(matches) >= MAX_GREP_MATCHES:
                        return "\n".join(matches)
        return "\n".join(matches)

    return [read_file, glob_files, grep_files]


def serialize_message(message: Any) -> dict:
    return {
        "type": type(message).__name__,
        "content": getattr(message, "content", ""),
        "tool_calls": getattr(message, "tool_calls", []),
    }


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                parts.append(str(block.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def create_model():
    provider = os.environ.get("RISK_REVIEW_AGENT_PROVIDER", "anthropic").strip().lower()
    model_name = os.environ.get("RISK_REVIEW_AGENT_MODEL", "").strip()
    if not model_name:
        raise RuntimeError("RISK_REVIEW_AGENT_MODEL 未配置。")
    temperature = float(os.environ.get("RISK_REVIEW_AGENT_TEMPERATURE", "0"))
    max_tokens = int(os.environ.get("RISK_REVIEW_AGENT_MAX_TOKENS", "1200000"))
    timeout = float(os.environ.get("RISK_REVIEW_AGENT_TIMEOUT", "600"))
    return init_chat_model(model=model_name,model_provider="anthropic",temperature=temperature,max_retries=0,max_tokens=max_tokens)

    # raise RuntimeError(f"不支持的 Agent Provider：{provider}")


def run_agent(prompt: str, workspace: Path, *, enable_tools: bool = True) -> str:
    from langchain_core.messages import HumanMessage, ToolMessage

    tools = build_tools(workspace) if enable_tools else []
    tools_by_name = {item.name: item for item in tools}
    base_model = create_model()
    model = base_model.bind_tools(tools) if tools else base_model
    max_steps = max(1, int(os.environ.get("RISK_REVIEW_AGENT_MAX_STEPS", "20")))
    messages: list[Any] = [HumanMessage(content=prompt)]
    emit(
        "agent_start",
        {
            "workspace": str(workspace),
            "prompt": prompt,
            "max_steps": max_steps,
            "tools": sorted(tools_by_name),
        },
    )
    for round_number in range(1, max_steps + 1):
        emit(
            "model_input",
            {"round": round_number, "messages": [serialize_message(item) for item in messages]},
        )
        response = model.invoke(messages)
        emit("model_output", {"round": round_number, "message": serialize_message(response)})
        messages.append(response)
        tool_calls = list(getattr(response, "tool_calls", []) or [])
        if not tool_calls:
            markdown = extract_text(response.content)
            if not markdown:
                raise RuntimeError("Agent 最终响应为空。")
            emit("agent_final", {"round": round_number, "markdown": markdown})
            return markdown
        for call in tool_calls:
            name = str(call.get("name") or "")
            arguments = call.get("args") or {}
            call_id = str(call.get("id") or "")
            emit(
                "tool_call",
                {"round": round_number, "id": call_id, "name": name, "arguments": arguments},
            )
            tool = tools_by_name.get(name)
            if tool is None:
                tool_result = f"工具不存在：{name}"
                is_error = True
            else:
                try:
                    tool_result = str(tool.invoke(arguments))
                    is_error = False
                except Exception as exc:
                    tool_result = f"{type(exc).__name__}: {exc}"
                    is_error = True
            emit(
                "tool_result",
                {
                    "round": round_number,
                    "id": call_id,
                    "name": name,
                    "is_error": is_error,
                    "result": tool_result,
                },
            )
            messages.append(ToolMessage(content=tool_result, tool_call_id=call_id))
    raise RuntimeError(f"Agent 超过最大交互轮数：{max_steps}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    load_project_env()
    workspace = Path(args.workspace).resolve(strict=True)
    prompt = sys.stdin.read()
    try:
        run_agent(
            prompt,
            workspace,
            enable_tools=args.phase not in {"Direct Review", "Model Test"},
        )
    except Exception as exc:
        emit("agent_error", {"type": type(exc).__name__, "message": str(exc)})
        sys.stderr.write(f"Agent 执行失败：{type(exc).__name__}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
