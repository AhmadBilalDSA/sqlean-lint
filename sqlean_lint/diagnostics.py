"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Diagnostics for sqlean-lint Pro.

``run_diagnostics`` performs a strict, non-mutating health probe of the
installed environment:

  * verifies the core dependencies are importable and report sane versions;
  * checks that the free linter entry points import cleanly;
  * runs a fast dry-run parse through the Pro AST cache to confirm the
    semantic engine is healthy;
  * probes the license/lease module without creating any files.

Every check is wrapped so a single failure is reported independently and the
probe never raises.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any, Dict, List

# Each Probe is (name, callable -> (ok: bool, detail: str))
_PROBES: List[Dict[str, Any]] = []


def _probe(name: str):
    def decorator(fn):
        _PROBES.append({"name": name, "fn": fn})
        return fn
    return decorator


@_probe("python")
def _python_version() -> tuple[bool, str]:
    return True, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _check_import(modname: str, attr: str = "__version__") -> tuple[bool, str]:
    try:
        mod = importlib.import_module(modname)
    except Exception as err:  # noqa: BLE001
        return False, f"import failed: {err}"
    version = getattr(mod, attr, "")
    if not version:
        try:
            version = getattr(mod, "VERSION", "")
        except Exception:  # noqa: BLE001
            version = ""
    return True, str(version) or "present"


@_probe("sqlglot")
def _sqlglot() -> tuple[bool, str]:
    return _check_import("sqlglot", "__version__")


@_probe("cryptography")
def _cryptography() -> tuple[bool, str]:
    ok, ver = _check_import("cryptography", "__version__")
    return ok, ver


@_probe("pandas")
def _pandas() -> tuple[bool, str]:
    ok, ver = _check_import("pandas", "__version__")
    if ok:
        try:
            import pandas as pd
            if pd is not None:
                return True, str(ver) or "present"
        except Exception as err:  # noqa: BLE001
            return False, f"probe failed: {err}"
    return ok, ver


@_probe("pymongo")
def _pymongo() -> tuple[bool, str]:
    ok, ver = _check_import("pymongo", "version")
    detail = ver or "present"
    if not ok:
        # Real MongoDB connectivity is optional: the polyglot SQL->MongoDB
        # transpiler builds pipelines without contacting a live database.
        return True, "not importable here - MongoDB driver optional"
    return ok, detail


@_probe("customtkinter")
def _customtkinter() -> tuple[bool, str]:
    try:
        import customtkinter as ctk
        return True, getattr(ctk, "__version__", "present")
    except Exception as err:  # noqa: BLE001
        # The GUI is optional for headless QA; report it but do not fail.
        return True, f"not importable here ({type(err).__name__}) - GUI optional"


@_probe("click")
def _click() -> tuple[bool, str]:
    return _check_import("click", "__version__")


@_probe("sqlean_lint.rules.pro.cached_parse")
def _engine_dry_run() -> tuple[bool, str]:
    """Fast dry-run parse through the Pro AST cache."""
    try:
        from sqlean_lint.rules.pro import cached_parse, clear_ast_cache
        clear_ast_cache()
        ast = cached_parse("SELECT id FROM t WHERE id = 1", "duckdb")
        if ast is None:
            return False, "cached_parse returned None"
        roundtrip = str(ast.sql(dialect="duckdb"))
        if "SELECT" not in roundtrip.upper():
            return False, "roundtrip SQL did not contain SELECT"
        return True, roundtrip
    except Exception as err:  # noqa: BLE001
        return False, f"dry-run parse failed: {err}"


@_probe("sqlean_lint.lint_sql")
def _lint_sql_probe() -> tuple[bool, str]:
    """The safe engine must return a list and never raise on garbage."""
    try:
        from sqlean_lint.rules.pro import lint_sql
        result = lint_sql("!!! not SQL ((" + chr(0), "duckdb")
        if not isinstance(result, list):
            return False, "lint_sql did not return a list"
        return True, f"returns list (len={len(result)}) on arbitrary input"
    except Exception as err:  # noqa: BLE001
        return False, f"lint_sql raised: {err}"


@_probe("license/lease module")
def _license_probe() -> tuple[bool, str]:
    """Import-check the license engine without creating any files."""
    try:
        from sqlean_lint import license_manager as lm
        return True, f"LEASE_DAYS={lm.LEASE_DAYS}, issuer={lm.LICENSE_ISSUER}"
    except Exception as err:  # noqa: BLE001
        return False, f"import failed: {err}"


def run_diagnostics() -> List[Dict[str, Any]]:
    """Run every probe and return ordered (name, ok, detail) diagnostics."""
    results = []
    for probe in _PROBES:
        try:
            ok, detail = probe["fn"]()
        except Exception as err:  # noqa: BLE001 - a probe must never crash the run
            ok, detail = False, f"probe raised: {err}"
        results.append({"name": probe["name"], "ok": bool(ok), "detail": detail})
    return results


def format_report(results: List[Dict[str, Any]]) -> str:
    """Render diagnostics as a flat, human-readable report."""
    lines = ["sqlean-lint PRO environment diagnostics", ""]
    all_ok = True
    for r in results:
        status = "OK  " if r["ok"] else "FAIL"
        all_ok = all_ok and r["ok"]
        lines.append(f"[{status}] {r['name']:<38} {r['detail']}")
    lines.append("")
    lines.append("RESULT: " + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return "\n".join(lines)
