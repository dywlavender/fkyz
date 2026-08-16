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


REACT_ROUTE_PREFIXES = (
    "/review/single",
    "/review/batch",
    "/review/scheduled",
    "/requirement-review",
    "/quality/runs",
    "/quality/cases",
    "/quality/experiment",
    "/records",
    "/system/apis",
    "/system/spec",
    "/system/settings",
)


def _is_react_route(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in REACT_ROUTE_PREFIXES)


def _serve_react_app(handler, static_dir: Path) -> None:
    """Serve the built React entry point and fail explicitly when it is absent."""
    react_index = static_dir / "dist" / "index.html"
    if not react_index.exists():
        json_response(handler, 503, {"error": "React build is missing: static/dist/index.html"})
        return
    serve_static(handler, static_dir, "dist/index.html")


def handle_get(handler, path: str, static_dir: Path, outputs_dir: Path) -> bool:
    """Route the built React app, generated artifacts, and shared static assets."""
    if path == "/" or _is_react_route(path):
        _serve_react_app(handler, static_dir)
        return True
    if path == "/favicon.ico":
        text_response(handler, 204, b"", "image/x-icon")
        return True
    if path.startswith("/outputs/"):
        serve_output(handler, outputs_dir, path[len("/outputs/"):])
        return True
    return False
