# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""Bundled-resource path resolution for sqlean-lint PRO.

When the desktop GUI is compiled into a standalone executable (Nuitka one-file,
or PyInstaller on a sibling branch), relative resources such as the ``locales/``
message catalogs and CustomTkinter assets are extracted to a *runtime* directory
that is different from the source tree at build time.

This module centralizes that resolution so every consumer resolves paths the
same way and never crashes because a resource "moved" in a packed context. It
is fully offline, dependency-free and never raises on arbitrary environments.

Resolution order (first match wins):
  1. ``sys._MEIPASS``  — PyInstaller one-file extraction directory.
  2. ``parent of __file__`` — Nuitka one-file / standalone unpack location.
  3. ``sys.executable`` directory — a ``--onefile`` app placed in a folder.
  4. Current working directory (source-tree fallback, e.g. ``python -m``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def bundled_or_source() -> Path:
    """Return the runtime root that holds shipped resources (``locales/``...).

    Returns the best-effort base directory regardless of packaging mode. This
    is the single source of truth for "where are my data files".
    """
    # 1. PyInstaller one-file extraction dir.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)

    # 2. Nuitka / unpacked: directory of the entry module.
    module_file = Path(__file__).resolve()
    if module_file.name and module_file.parent.name:
        # `sqlean_lint/paths.py` -> package dir -> project root (source mode)
        # In a bundled run `__file__` lives under the unpack dir; the package
        # parent is the unpack root that also holds `locales/`.
        return module_file.parents[1]

    # 3. Directory of the running executable (onefile next to data dir).
    exe_dir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if exe_dir:
        return Path(exe_dir)

    # 4. CWD fallback.
    return Path.cwd()


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource, e.g. ``resource_path("locales", "en.json")``.

    Returns the resolved absolute ``Path`` whether or not it currently exists,
    so callers can decide how to react (fall back to embedded catalogs, etc.).
    """
    return bundled_or_source().joinpath(*parts)


def locales_dir() -> Path:
    """Return the resolved bundle ``locales/`` directory."""
    return resource_path("locales")


def set_env_dirs() -> None:
    """Best-effort: surface the runtime root for external tools & Tk assets.

    Some third-party runtimes (CustomTkinter asset discovery, Tcl/Tk) cache a
    path from ``__file__`` at import time. Setting an environment hint here
    costs nothing and helps a compiled CLI/GUI bind the correct data tree.
    """
    root = str(bundled_or_source())
    os.environ.setdefault("SQLEAN_LINT_DATA_DIR", root)
    os.environ.setdefault("SQLEAN_LINT_LOCALES_DIR", str(locales_dir()))


def register(extra: Optional[Path] = None) -> Path:
    """Call at the top of the packaged entry point; returns the base root.

    * Creates the base dir if needed (for writable workspace outputs).
    * Records ``SQLEAN_LINT_DATA_DIR`` and ``SQLEAN_LINT_LOCALES_DIR`` hints.
    * Places *extra* candidate dirs ahead of the default search is out of scope
      here (callers may pass an explicit base via :func:`set_base`).
    """
    set_env_dirs()
    return bundled_or_source()


_SEARCH_EXTRA: list = []


def _candidates() -> list:
    """Ordered list of candidate base dirs (deduplicated)."""
    ordered: list = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        ordered.append(Path(meipass))
    ordered.append(Path(__file__).resolve().parents[1])
    exe_dir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if exe_dir:
        ordered.append(Path(exe_dir))
    ordered.append(Path.cwd())
    ordered.extend(_SEARCH_EXTRA)

    seen: set = set()
    result: list = []
    for p in ordered:
        key = os.path.normcase(str(p.resolve()))
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result
