from __future__ import annotations

import platform
import uuid
from pathlib import Path
from typing import Callable, Iterable


def file_health(path: Path, *, root: Path | None = None) -> dict:
    """Return existence and basic metadata for one required project file."""
    try:
        display_path = path.relative_to(root).as_posix() if root else str(path)
    except ValueError:
        display_path = str(path)
    return {
        "path": display_path,
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
    }


def outputs_health(outputs_dir: Path, *, root: Path | None = None) -> dict:
    """Check output-directory readability, writability, and artifact count once."""
    try:
        display_path = outputs_dir.relative_to(root).as_posix() if root else str(outputs_dir)
    except ValueError:
        display_path = str(outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    probe = outputs_dir / f".health-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"path": display_path, "exists": True, "writable": True, "note": ""}
    except Exception as exc:
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "path": display_path,
            "exists": outputs_dir.exists(),
            "writable": False,
            "note": str(exc),
        }


def api_docs_health(list_api_docs: Callable[[], Iterable[dict]]) -> dict:
    """Summarize configured API documents without duplicating the discovery scan."""
    try:
        docs = list(list_api_docs())
    except Exception as exc:
        return {"count": 0, "by_category": {}, "note": str(exc)}
    by_category: dict[str, int] = {}
    for doc in docs:
        category = doc.get("category") or "其他"
        by_category[category] = by_category.get(category, 0) + 1
    return {"count": len(docs), "by_category": by_category, "note": ""}


def build_system_health(
    *,
    required_paths: list[Path],
    root: Path,
    outputs_dir: Path,
    list_api_docs: Callable[[], Iterable[dict]],
    model_runtime: dict,
    runtime_settings: dict,
    task_pool: dict,
    mock_mode: bool,
    review_history: dict,
) -> dict:
    """Build the aggregate health payload, evaluating each expensive check once."""
    files = {
        path.relative_to(root).as_posix(): file_health(path, root=root)
        for path in required_paths
    }
    outputs = outputs_health(outputs_dir, root=root)
    api_docs = api_docs_health(list_api_docs)
    checks = {
        "required_files": all(item["exists"] for item in files.values()),
        "outputs_writable": outputs["writable"],
        "model_available": bool(model_runtime.get("available")) or mock_mode,
        "api_docs_loaded": api_docs["count"] > 0,
        "review_history_healthy": bool(review_history.get("ok")),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "working_dir": str(root),
        "mock_mode": mock_mode,
        "modelRuntime": model_runtime,
        "files": files,
        "outputs": outputs,
        "api_docs": api_docs,
        "task_pool": task_pool,
        "runtime_settings": runtime_settings,
        "review_history": review_history,
    }
