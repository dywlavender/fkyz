from __future__ import annotations

from pathlib import Path


def list_outputs(outputs_dir: Path, project_root: Path) -> list[dict]:
    """Return supported review artifacts ordered from newest to oldest."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        path
        for path in outputs_dir.iterdir()
        if path.is_file()
        and path.name.startswith(("standard-", "review-", "risk-review-"))
        and path.name.endswith((".md", ".json", ".txt"))
    ]
    files = []
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            display_path = path.relative_to(project_root).as_posix()
        except ValueError:
            display_path = path.as_posix()
        files.append(
            {
                "name": path.name,
                "path": display_path,
                "mtime": int(path.stat().st_mtime),
                "size": path.stat().st_size,
            }
        )
    return files
