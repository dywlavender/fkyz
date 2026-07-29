from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


def read_text(path: Path) -> str:
    """Read UTF-8 text from a filesystem path."""
    return path.read_text(encoding="utf-8")


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入：先写临时文件再 rename，避免并发读看到截断内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_frontmatter(text: str) -> dict:
    """Parse the simple frontmatter format used by project Markdown files."""
    meta = {}
    match = re.search(r"^---\n(.+?)\n---", text, re.S)
    if not match:
        return meta
    for line in match.group(1).split("\n"):
        kv = re.match(r"^(\w+):\s*(.+?)\s*$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                val = [x.strip().strip("'\"") for x in val[1:-1].split(",")]
            meta[key] = val
    return meta
