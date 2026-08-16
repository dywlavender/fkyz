from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the production requirements include it
    import os

    def load_dotenv(dotenv_path=None, *, override=False, **_kwargs):
        """Load simple KEY=VALUE dotenv files when python-dotenv is unavailable."""
        path = Path(dotenv_path) if dotenv_path else None
        if path is None or not path.is_file():
            return False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or (not override and key in os.environ):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value
        return True


def load_project_env(path: Path | None = None) -> Path | None:
    """Load the project dotenv file without overriding process variables."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.is_file():
        return None
    load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8")
    return env_path
