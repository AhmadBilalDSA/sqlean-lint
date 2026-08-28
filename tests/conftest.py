"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Shared pytest bootstrap: import path plus a sandbox-friendly scratch dir.

The stock ``tmp_path`` fixture manages its root through Windows extended
``\\\\?\\`` paths, which the local file sandbox rejects. The ``scratch``
fixture below provides equivalent per-test isolation using only plain
relative paths inside the project workspace.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import uuid

# Redirect pytest's temp-file root *before* any pytest internals import
# to avoid Windows ``PermissionError [WinError 5]`` on the system %TEMP% dir.
os.environ["PYTEST_DEBUG_TEMPROOT"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".pytest_tmp",
)

import pytest  # noqa: E402

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
