"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

Lightweight, dependency-free localization layer for sqlean-lint Pro.

Message catalogs live in ``locales/<lang>.json`` next to the repository root.
A fallback English catalog is embedded in this module so the desktop binary
keeps working even when the ``locales/`` directory is not shipped (e.g. a
stripped one-file Nuitka build without data files).
"""
from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

_FALLBACK_ENGLISH: Dict[str, Any] = {
    "app": {
        "title": "SQLean Lint Pro",
        "tagline": "Commercial SQL Performance Linter",
    },
    "activation": {
        "heading": "Activate your license",
        "hint": "Enter the license key issued for this machine.",
        "license_key": "License Key",
        "activate": "Activate",
        "validating": "Validating...",
        "empty": "Please enter a license key.",
        "expired": "This license has expired.",
        "invalid": "License is not valid.",
        "failed": "Activation failed",
        "hardware_id": "Hardware ID",
    },
    "dashboard": {
        "dialect": "Dialect",
        "settings": "Settings",
        "scan_directory": "Scan Directory",
        "queued_files": "Queued Files",
        "drop_zone": "Drag and Drop SQL Files",
        "browse": "Browse SQL Files...",
        "browse_hint": "SQL Files\n\n(click to browse)",
        "clear": "Clear",
        "ready": "Ready",
        "fix_all": "Auto-Fix All",
        "no_files_to_fix": "Nothing to fix.",
        "scanning": "Scanning {count} file(s)...",
        "done": "Done - {count} finding(s)",
        "nothing_fixable": "Nothing to fix.",
        "fix_applied": "Rewritten {count} file(s) in place.",
    },
    "settings": {
        "title": "Settings",
        "sql_dialect": "SQL Dialect",
        "theme": "Theme",
        "language": "Language",
        "rules": "Rules",
        "cancel": "Cancel",
        "apply": "Apply",
    },
    "cli": {
        "check_started": "Checking {count} file(s)...",
        "clean": "No violations found.",
        "language": "Language",
    },
    "theme": {"dark": "Dark", "light": "Light"},
    "language_names": {"en": "English", "ur": "Urdu"},
}

SUPPORTED_LANGUAGES: tuple = ("en", "ur")
DEFAULT_LANGUAGE: str = "en"


def _locales_dir() -> Path:
    """Return the repository ``locales/`` directory when it exists.

    Resolution favours bundled resources (Nuitka one-file / PyInstaller) via
    :func:`sqlean_lint.paths.resource_path`, then falls back to the source
    tree and the current working directory. Never raises in a packed context.
    """
    from .paths import resource_path  # local import avoids a cycle at import time

    candidates = (
        resource_path("locales"),
        Path(__file__).resolve().parents[1] / "locales",
        Path.cwd() / "locales",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _embedded_en() -> Dict[str, Any]:
    """Load the fallback English catalog embedded in this module."""
    return json.loads(json.dumps(_FALLBACK_ENGLISH))


def load_catalog(language: str) -> Dict[str, Any]:
    """Load the message catalog for *language*.

    Resolution order:
      1. ``locales/<language>.json`` on disk (repo root / CWD);
      2. embedded English fallback.

    Unknown languages silently degrade to the English fallback.
    """
    language = (language or DEFAULT_LANGUAGE).strip().lower()
    disk = _locales_dir() / f"{language}.json"
    try:
        data = json.loads(disk.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("_meta", {}).get("language") == language:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _embedded_en()


def deep_get(catalog: Mapping[str, Any], dotted_key: str, default: str = "") -> str:
    """Fetch ``a.b.c``-style keys from a nested catalog."""
    value: Any = catalog
    for part in dotted_key.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value if isinstance(value, str) else default


class Translator:
    """Locale-aware string resolver with ``{key}`` format interpolation."""

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = (language or DEFAULT_LANGUAGE).strip().lower()
        self.catalog: Dict[str, Any] = load_catalog(self.language)

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate *key* (dotted) with optional ``{name}`` formatting."""
        text = deep_get(self.catalog, key, "")
        if not text:
            fallback = deep_get(_FALLBACK_ENGLISH, key, "?{key}?")
            text = fallback
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):  # noqa: BLE001 - never crash the UI
                pass
        return text

    def __call__(self, key: str, **kwargs: Any) -> str:
        return self.t(key, **kwargs)

    @staticmethod
    def language_name(language: str) -> str:
        return deep_get(_FALLBACK_ENGLISH, f"language_names.{language}", language)


_DEFAULT_TRANSLATOR = Translator(DEFAULT_LANGUAGE)


def t(key: str, **kwargs: Any) -> str:
    """Module-level convenience wrapper over the English fallback."""
    return _DEFAULT_TRANSLATOR.t(key, **kwargs)


def get_translator(language: str | None) -> Translator:
    """Build a :class:`Translator`, falling back to the module default."""
    if not language:
        return _DEFAULT_TRANSLATOR
    return Translator(language)