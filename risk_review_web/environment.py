from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_project_env(path: Path | None = None) -> Path | None:
    """Load the project dotenv file without overriding process variables."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.is_file():
        return None
    load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8")
    return env_path
