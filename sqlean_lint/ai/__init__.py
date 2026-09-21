# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""AI & MLOps layer for sqlean-lint PRO.

This package is the home of the new MLOps pipeline (``mlops.py``). Because
the legacy ``sqlean_lint/ai.py`` module would be shadowed by this package,
we re-export its public surface (``AISuggestion``, ``optimize_with_ai``) so
the existing top-level ``sqlean_lint/__init__.py`` imports keep working.

The legacy module is loaded under the internal name ``sqlean_lint._ai_legacy``
via importlib to avoid the packaging shadowing issue and is registered in
``sys.modules`` so its dataclass decorator can resolve correctly.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LEGACY_NAME = "sqlean_lint._ai_legacy"
_legacy_path = Path(__file__).resolve().parent.parent / "ai.py"

if _legacy_path.exists():  # pragma: no cover - legacy module relocation guard
    _spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _legacy_path)
    _module = None
    if _spec and _spec.loader:
        _module = importlib.util.module_from_spec(_spec)
        _module.__package__ = "sqlean_lint"
        sys.modules[_LEGACY_NAME] = _module
        try:
            _spec.loader.exec_module(_module)
        except Exception:  # noqa: BLE001 - legacy imports are non-fatal
            sys.modules.pop(_LEGACY_NAME, None)
            _module = None
else:  # pragma: no cover
    _module = None

_AISuggestion = getattr(_module, "AISuggestion", None) if _module is not None else None
_optimize_with_ai = (
    getattr(_module, "optimize_with_ai", None) if _module is not None else None
)

# Public aliases (the package imports cleanly regardless of legacy module).
AISuggestion = _AISuggestion
optimize_with_ai = _optimize_with_ai

from .mlops import (  # noqa: E402
    LLMClient,
    OptimizationCacheEntry,
    RefactorSuggestion,
    SQLRefactoringPipeline,
    VectorCache,
    ai_enabled,
)

__all__ = [
    "AISuggestion",
    "optimize_with_ai",
    "LLMClient",
    "OptimizationCacheEntry",
    "RefactorSuggestion",
    "SQLRefactoringPipeline",
    "VectorCache",
    "ai_enabled",
]
