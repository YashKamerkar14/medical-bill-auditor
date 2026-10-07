from pathlib import Path

import pytest

from bill_auditor.config import PROJECT_ROOT, ConfigError, load_paths


@pytest.fixture
def no_env_file(tmp_path: Path) -> Path:
    """A .env path that does not exist, so only the test's environment counts."""
    return tmp_path / "missing.env"


def test_missing_data_dir_is_rejected(monkeypatch, no_env_file):
    monkeypatch.delenv("DATA_DIR", raising=False)
    with pytest.raises(ConfigError, match="not set"):
        load_paths(no_env_file)


def test_nonexistent_data_dir_is_rejected(monkeypatch, tmp_path, no_env_file):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="does not exist"):
        load_paths(no_env_file)


def test_data_dir_inside_repository_is_rejected(monkeypatch, no_env_file):
    monkeypatch.setenv("DATA_DIR", str(PROJECT_ROOT / "src"))
    with pytest.raises(ConfigError, match="outside the repository"):
        load_paths(no_env_file)


def test_paths_are_derived_from_data_dir(monkeypatch, tmp_path, no_env_file):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    paths = load_paths(no_env_file)
    assert paths.data_dir == tmp_path.resolve()
    assert paths.raw_dir == tmp_path.resolve() / "raw"
    assert paths.sqlite_path.name == "bill_auditor.sqlite"


def test_env_file_is_read(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_DIR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATA_DIR={tmp_path}\n")
    assert load_paths(env_file).data_dir == tmp_path.resolve()
