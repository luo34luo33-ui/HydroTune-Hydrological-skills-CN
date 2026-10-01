from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "repo copy（测试）"
    shutil.copytree(
        REPO_ROOT,
        destination,
        ignore=shutil.ignore_patterns(".git", ".cache", ".pytest_cache", "__pycache__", "*.pyc", "*.egg-info"),
    )
    return destination


@pytest.fixture
def utf8_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return environment

