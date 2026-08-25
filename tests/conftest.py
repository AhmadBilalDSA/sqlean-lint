"""Shared pytest bootstrap: import path plus a sandbox-friendly scratch dir.

The stock ``tmp_path`` fixture manages its root through Windows extended
``\\\\?\\`` paths, which the local file sandbox rejects. The ``scratch``
fixture below provides equivalent per-test isolation using only plain
relative paths inside the project workspace.
"""
import pathlib
import shutil
import sys
import uuid

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def scratch() -> pathlib.Path:
    root = PROJECT_ROOT / ".pytest-scratch"
    root.mkdir(exist_ok=True)
    target = root / f"t-{uuid.uuid4().hex[:10]}"
    target.mkdir()
    yield target
    shutil.rmtree(target, ignore_errors=True)
