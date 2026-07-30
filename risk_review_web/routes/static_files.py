from __future__ import annotations

from pathlib import Path

from risk_review_web.services.http_responses import json_response, text_response


def serve_static(handler, static_dir: Path, name: str) -> None:
    """Serve a file below the static root after rejecting path traversal."""
    name = name or "index.html"
    safe = Path(name)
    if safe.is_absolute() or ".." in safe.parts:
        json_response(handler, 403, {"error": "forbidden"})
        return
    path = static_dir / safe
    if not path.exists() or not path.is_file():
        json_response(handler, 404, {"error": "not found"})
        return
    content_type = "text/html; charset=utf-8"
    if path.suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif path.suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    elif path.suffix == ".svg":
        content_type = "image/svg+xml"
    text_response(handler, 200, path.read_bytes(), content_type)


def serve_output(handler, outputs_dir: Path, name: str) -> None:
    """Serve one generated Markdown artifact by safe basename."""
    safe = Path(name)
    if safe.is_absolute() or ".." in safe.parts:
        json_response(handler, 403, {"error": "forbidden"})
        return
    path = outputs_dir / safe
    if not path.exists() or not path.is_file():
        json_response(handler, 404, {"error": "not found"})
        return
    text_response(handler, 200, path.read_bytes(), "text/markdown; charset=utf-8")


def handle_get(handler, path: str, static_dir: Path, outputs_dir: Path) -> bool:
    """Route frontend entry points, generated artifacts, and legacy static assets."""
    if path == "/":
        react_index = static_dir / "dist" / "index.html"
        serve_static(handler, static_dir, "dist/index.html" if react_index.exists() else "index.html")
        return True
    if path in ("/legacy", "/legacy/"):
        serve_static(handler, static_dir, "index.html")
        return True
    if path == "/favicon.ico":
        text_response(handler, 204, b"", "image/x-icon")
        return True
    if path.startswith("/outputs/"):
        serve_output(handler, outputs_dir, path[len("/outputs/"):])
        return True
    if any(path.startswith(prefix) for prefix in ("/review/", "/requirement-review", "/quality/", "/records", "/system/")):
        react_index = static_dir / "dist" / "index.html"
        serve_static(handler, static_dir, "dist/index.html" if react_index.exists() else "index.html")
        return True
    return False
