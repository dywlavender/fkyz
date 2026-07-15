#!/bin/bash
set -e

PORT="${1:-8765}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 内置默认 Claude 命令；外部仍可用 RISK_REVIEW_CLAUDE_CMD 覆盖。
# Windows / Git Bash 通常使用 claude.cmd；macOS / Linux 通常使用 claude。
DEFAULT_CLAUDE_CMD="${DEFAULT_CLAUDE_CMD:-claude.cmd}"

echo "==========================================="
echo "  风控评审平台 Risk Review Server v2"
echo "==========================================="

for f in risk_review_web/server_v2.py risk_review_web/static/index.html; do
    [ -f "$f" ] && echo "✓ $f" || { echo "✗ $f 缺失"; exit 1; }
done

mkdir -p outputs
echo "✓ outputs/ 目录就绪"
echo "启动服务: http://127.0.0.1:${PORT}"

export PORT="$PORT"

if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -ti tcp:${PORT} 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
        echo "⚠ 端口 ${PORT} 已被占用，正在结束进程: ${PIDS}"
        kill $PIDS 2>/dev/null || true
        sleep 1
        LEFT="$(lsof -ti tcp:${PORT} 2>/dev/null || true)"
        if [ -n "$LEFT" ]; then
            echo "⚠ 进程仍占用端口，强制结束: ${LEFT}"
            kill -9 $LEFT 2>/dev/null || true
            sleep 1
        fi
        echo "✓ 端口 ${PORT} 已释放"
    fi
else
    echo "⚠ 未找到 lsof，跳过端口占用清理"
fi

if [ -z "${RISK_REVIEW_CLAUDE_CMD:-}" ]; then
    if command -v "${DEFAULT_CLAUDE_CMD}" >/dev/null 2>&1; then
        export RISK_REVIEW_CLAUDE_CMD="${DEFAULT_CLAUDE_CMD}"
    elif command -v claude >/dev/null 2>&1; then
        export RISK_REVIEW_CLAUDE_CMD="claude"
    elif command -v claude.cmd >/dev/null 2>&1; then
        export RISK_REVIEW_CLAUDE_CMD="claude.cmd"
    fi
fi

if [ -n "${RISK_REVIEW_CLAUDE_CMD:-}" ]; then
    echo "✓ Claude 命令: ${RISK_REVIEW_CLAUDE_CMD}"
else
    echo "⚠ 未找到 claude/claude.cmd；可设置 RISK_REVIEW_CLAUDE_CMD 后再启动"
fi

if command -v python >/dev/null 2>&1; then
    exec python -m risk_review_web.server_v2
fi

exec python3 -m risk_review_web.server_v2
