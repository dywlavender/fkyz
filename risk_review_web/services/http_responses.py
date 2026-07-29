from __future__ import annotations

import json
from typing import Any


def json_response(handler, status: int, payload: dict[str, Any]) -> None:
    """Write a UTF-8 JSON response through a BaseHTTPRequestHandler-compatible object."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def success_response(handler, data: Any, *, status: int = 200) -> None:
    """Write the platform's standard successful API envelope."""
    json_response(handler, status, {"success": True, "data": data})


def error_response(handler, status: int, message: str, detail: Any = None) -> None:
    """Write the platform's standard error envelope with optional structured detail."""
    payload: dict[str, Any] = {"success": False, "error": message}
    if detail is not None:
        payload["detail"] = detail
    json_response(handler, status, payload)


def text_response(handler, status: int, content: bytes, content_type: str) -> None:
    """Write an already encoded text or static-file response."""
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)
