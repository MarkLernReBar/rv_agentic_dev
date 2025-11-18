import os
import pathlib
from typing import Optional


def load_env_files(
    *,
    root_dir: Optional[str] = None,
    env_files: Optional[list[str]] = None,
) -> None:
    """Load environment variables from .env.local and .env if present.

    Mirrors the behaviour used in the Streamlit app and workers so each
    process observes the same configuration without duplicating parsing
    logic everywhere.
    """

    def _parse_and_set(path: str) -> None:
        p = pathlib.Path(path)
        if not p.exists():
            return
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :]
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
        except Exception:
            # Fail open: identical to app.py behaviour
            pass

    if root_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        # workers -> rv_agentic -> src -> repo root
        root_dir = os.path.abspath(os.path.join(base_dir, os.pardir, os.pardir, os.pardir))

    if env_files is None:
        env_files = [".env.local", ".env"]

    for rel in env_files:
        _parse_and_set(os.path.join(root_dir, rel))
