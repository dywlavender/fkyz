from __future__ import annotations

import argparse
import json
import sys
import time


PREFIX = "RISK_REVIEW_AGENT_EVENT\t"


def emit(event: str, payload: object) -> None:
    sys.stderr.write(
        PREFIX + json.dumps({"event": event, "payload": payload}, ensure_ascii=False) + "\n"
    )
    sys.stderr.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--workspace", required=True)
    parser.parse_args()
    prompt = sys.stdin.read()
    emit("model_input", {"round": 1, "prompt": prompt})
    emit("tool_call", {"round": 1, "name": "read_file"})
    emit("tool_result", {"round": 1, "result": "规则正文"})
    time.sleep(0.6)
    emit("agent_final", {"round": 2, "markdown": "# 实时日志测试"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
