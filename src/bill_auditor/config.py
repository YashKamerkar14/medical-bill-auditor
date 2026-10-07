"""Project paths, read from the environment.

Code lives in the repository (inside OneDrive). Everything write-heavy or
licensed lives under DATA_DIR, outside the repository: raw CMS downloads, the
SQLite database, the Chroma index and local evaluation cases. Every other module
gets its paths from here, so there is exactly one place that decides where data
goes.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when the environment is missing or unsafe."""


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "bill_auditor.sqlite"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def eval_dir(self) -> Path:
        return self.data_dir / "eval"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def load_paths(env_file: Path | None = None) -> Paths:
    """Read DATA_DIR from the environment (or .env) and check it is safe to use.

    Values already in the environment win over the .env file.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    raw_value = os.environ.get("DATA_DIR", "").strip()
    if not raw_value:
        raise ConfigError("DATA_DIR is not set. Copy .env.example to .env and set it.")

    data_dir = Path(raw_value).expanduser().resolve()
    if not data_dir.is_dir():
        raise ConfigError(f"DATA_DIR does not exist: {data_dir}")

    # Licensed CMS tables must never end up somewhere git could commit them.
    if data_dir == PROJECT_ROOT or PROJECT_ROOT in data_dir.parents:
        raise ConfigError(f"DATA_DIR must be outside the repository, got {data_dir}")

    return Paths(data_dir=data_dir)
