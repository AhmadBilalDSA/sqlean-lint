"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Modern desktop GUI for sqlean-lint Pro, built on customtkinter.

Views
-----
* Activation view — a sleek screen gating the whole application behind a
  valid, hardware-bound license key.
* Main dashboard  — two panes: a drag-and-drop SQL zone + scan-directory
  control on the left, and real-time lint results with line numbers and a
  one-click "Auto-Fix All" on the right.
* Settings modal  — dialect selector (DuckDB/Postgres/Snowflake), per-rule
  enable/disable toggles, and a dark/light theme toggle.

All scanning runs on a background ``threading.Thread`` so the UI never
freezes; results are marshalled back to the main Tk thread via ``after``.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk
import sqlglot

from .config import LinterConfig
from .engine import split_statement_offsets, statement_under_cursor
from .dialect_detector import detect_dialect, safe_parse_sql
from .i18n import Translator, get_translator, load_catalog, t
from .chatbot import OfflineOnlineAgent
from .license_manager import LicenseError, LicenseValidator, generate_hardware_id
from .rules import (
    DEFAULT_RULES,
    LintViolation,
    apply_fixes,
    fix_sql,
)
# Enterprise UI/UX components (added for the interactive workspace).
from .ui.animations import CTkAnimationEngine, SlidePanel
from .ui.code_view import SplitCodeDiffViewer
from .ui.chart_view import ChartView
from .ui.command_palette import Command, CommandPalette
from .ui.dag_viewer import DagViewer, parse_sql as _dag_parse
from .ui.data_grid import DataGrid, QueryRunner
from .ui.explorer import InteractiveFileExplorer
from .ui.history_tracker import HistoryTracker
from .ui.intellisense import IntelliSense
from .ui.minimap import CodeMinimap
from .ui.profiler_view import ProfilerView
from .ui.query_history import (
    HistoryEntry,
    QueryHistoryDrawer,
    QueryHistoryLedger,
)
from .ui.rating_widget import SQLHealthScore
from .ui.tab_manager import CTkTabBar
from .ui.workspace import WorkspaceLayout, DraggableSplitter, CollapsibleRail
from .ui.virtual_grid import VirtualGrid
from .ui.dag_canvas import DagCanvas, AstNode as _AstNode
from .ui.toasts import ToastStack, SkeletonPulse

# Executive report export (pdf + xlsx).
from .export.executive import export_executive_report

# Local zero-cloud query engine for the interactive data grid.
try:  # pragma: no cover - optional runtime dependency
    from .connectors.duckdb_local import DuckDBLocalConnector

    _DUCKDB_AVAILABLE = True
except Exception:  # noqa: BLE001 - DuckDB is optional; degrade gracefully
    _DUCKDB_AVAILABLE = False

# Attempt optional drag-and-drop support; degrade gracefully when absent.
try:  # pragma: no cover - optional runtime dependency
    from tkinterdnd2 import TkinterDnD, DND_FILES

    _DND_AVAILABLE = True
except Exception:  # noqa: BLE001
    _DND_AVAILABLE = False

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
# High-DPI awareness: force 1.0 widget/window scaling so the UI renders sharply
# (unblurred) on 4K / high-DPI displays instead of relying on OS bitmap scaling.
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

APP_TITLE = "SQLean Lint Pro"
APP_SIZE = "1180x720"

# ── Centralized design-system tokens (single source of truth) ─────────
from .ui.design_system import TOKENS, SPACING, TYPOGRAPHY, FONTS, ThemeManager as _ThemeManager

_COLOR_BG = TOKENS["CANVAS_BG"]
_COLOR_SURFACE = TOKENS["SURFACE_CARD"]
_COLOR_ACCENT = TOKENS["ACCENT_PRIMARY"]
_COLOR_TEXT = TOKENS["TEXT_PRIMARY"]
_COLOR_MUTED = TOKENS["TEXT_MUTED"]
_COLOR_OK = TOKENS["SEMANTIC_SUCCESS"]
_COLOR_WARN = TOKENS["SEMANTIC_WARNING"]
_COLOR_ERR = TOKENS["SEMANTIC_DANGER"]
_COLOR_ZONE = TOKENS["PANEL_BG"]

# Legacy aliases kept for source compatibility inside this module.
COLOR_BG = _COLOR_BG
COLOR_SURFACE = _COLOR_SURFACE
COLOR_ACCENT = _COLOR_ACCENT
COLOR_TEXT = _COLOR_TEXT
COLOR_MUTED = _COLOR_MUTED
COLOR_OK = _COLOR_OK
COLOR_WARN = _COLOR_WARN
COLOR_ERR = _COLOR_ERR
COLOR_ZONE = _COLOR_ZONE
# Semantic accent aliases (Phase 4 resilient layout).
COLOR_AMBER = TOKENS.get("ACCENT_AMBER", _COLOR_WARN)
COLOR_ROSE = TOKENS.get("ACCENT_ROSE", _COLOR_ERR)
COLOR_EMERALD = TOKENS.get("ACCENT_EMERALD", _COLOR_OK)
COLOR_CYAN = TOKENS.get("ACCENT_CYAN", _COLOR_ACCENT)
COLOR_PURPLE = TOKENS.get("ACCENT_PURPLE", TOKENS["SEMANTIC_INFO"])

# Commonly repeated border arguments for frames / widgets.
_BORDER_WIDTH = 1
_BORDER_COLOR = TOKENS["BORDER_SUBTLE"]


def _now_stamp() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _logger():
    import logging

    return logging.getLogger("sqlean_lint_pro")


# ── Linting backend (runs on a worker thread) ──────────────────────────

class ScanWorker:
    """Process files in a background thread, pushing events to a queue."""

    def __init__(
        self,
        files: List[Path],
        dialect: str,
        enabled_rules: Dict[str, bool],
        out_queue: "queue.Queue",
    ) -> None:
        self._files = files
        self._dialect = dialect
        self._enabled_rules = {k: v for k, v in enabled_rules.items() if v}
        self._queue = out_queue

    def run(self) -> None:
        for path in self._files:
            try:
                sql = path.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                self._queue.put(
                    ("file_error", str(path), f"Could not read file: {err}", ())
                )
                continue
            ast_nodes, resolved_dialect = safe_parse_sql(sql, self._dialect)
            if not ast_nodes:
                self._queue.put(
                    ("file_error", str(path), "Could not parse file with any dialect", (1, 1))
                )
                continue
            violations: List[LintViolation] = []
            for ast in ast_nodes:
                if ast is None:
                    continue
                for rule in DEFAULT_RULES:
                    if not self._enabled_rules.get(rule.rule_id, True):
                        continue
                    violations.extend(rule.run(ast, resolved_dialect))
            self._queue.put(
                ("result", str(path), "",
                 [(v.rule_id, v.message, v.severity, v.line, v.col, v.fixable)
                  for v in violations])
            )
        self._queue.put(("done", "", "", ()))


def _collect_sql_files(paths: List[Path], excludes) -> List[Path]:
    """Expand files/directories to a de-duplicated list of .sql files."""
    seen: set = set()
    out: List[Path] = []
    for candidate in paths:
        if candidate.is_dir():
            matches = sorted(candidate.rglob("*.sql"))
        elif candidate.is_file():
            matches = [candidate]
        else:
            continue
        for match in matches:
            key = str(match.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(match)
    return out


# ── Activation view ────────────────────────────────────────────────────

class ActivationView(ctk.CTkFrame):
    """Startup gate: collects a license key and validates it."""

    def __init__(self, master, on_activated, _tr=None) -> None:
        super().__init__(master, fg_color=COLOR_BG, corner_radius=0,
                         border_width=_BORDER_WIDTH, border_color=_BORDER_COLOR)
        self._on_activated = on_activated
        self._tr = _tr or _default_translator()
        self._validator = LicenseValidator()

        self.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkLabel(
            self,
            text=self._tr("app.title"),
            font=ctk.CTkFont("Segoe UI", 34, "bold"),
            text_color=COLOR_ACCENT,
        )
        brand.grid(row=0, column=0, pady=(70, 4))

        subtitle = ctk.CTkLabel(
            self,
            text=self._tr("app.tagline"),
            font=ctk.CTkFont("Segoe UI", 15),
            text_color=COLOR_MUTED,
        )
        subtitle.grid(row=1, column=0, pady=(0, 28))

        card = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=14,
                            border_width=_BORDER_WIDTH,
                            border_color=TOKENS["BORDER_STRONG"])
        card.grid(row=2, column=0, padx=120, pady=8, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=3)

        title = ctk.CTkLabel(
            card,
            text="Activate your license",
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
        )
        title.grid(row=0, column=0, columnspan=3, pady=(20, 4), padx=24)

        hint = ctk.CTkLabel(
            card,
            text=(
                "Enter the license key issued for this machine.\n"
                f"Hardware ID: {generate_hardware_id()[:16]}..."
            ),
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=COLOR_MUTED,
            justify="center",
        )
        hint.grid(row=1, column=0, columnspan=3, pady=(0, 12))

        key_label = ctk.CTkLabel(card, text="License Key", anchor="w")
        key_label.grid(row=2, column=0, padx=(24, 8), pady=6, sticky="e")

        self._key_entry = ctk.CTkEntry(
            card,
            placeholder_text="XXXX-XXXX-XXXX-XXXX",
            width=380,
            font=ctk.CTkFont("Consolas", 13),
        )
        self._key_entry.grid(row=2, column=1, columnspan=2, padx=(0, 24), pady=6, sticky="ew")

        self._status = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont("Segoe UI", 12), text_color=COLOR_ERR
        )
        self._status.grid(row=3, column=0, columnspan=3, pady=(4, 0))

        self._activate_btn = ctk.CTkButton(
            card,
            text="Activate",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            fg_color=COLOR_ACCENT,
            height=40,
            command=self._on_activate_click,
        )
        self._activate_btn.grid(row=4, column=0, columnspan=3, pady=(12, 22), ipadx=40)

    def _on_activate_click(self) -> None:
        key = self._key_entry.get().strip()
        if not key:
            self._status.configure(text="Please enter a license key.")
            return
        self._activate_btn.configure(state="disabled", text="Validating...")
        threading.Thread(target=self._validate, args=(key,), daemon=True).start()

    def _validate(self, key: str) -> None:
        try:
            hw = generate_hardware_id()
            info = self._validator.validate_key(key, hardware_id=hw)
            if info.valid and not info.is_expired:
                self._validator.save_license(info)
                self.after(0, lambda: self._on_activated(info))
                return
            if info.is_expired:
                self._post_error("This license has expired.")
                return
            self._post_error("License is not valid.")
        except LicenseError as err:
            self._post_error(str(err))
        except Exception as err:  # noqa: BLE001
            _logger().exception("Activation failed")
            self._post_error(f"Activation failed: {err}")

    def _post_error(self, text: str) -> None:
        def _apply():
            self._status.configure(text=text)
            self._activate_btn.configure(state="normal", text="Activate")

        self.after(0, _apply)


# ── Settings modal ─────────────────────────────────────────────────────

class SettingsModal(ctk.CTkToplevel):
    """Modal for dialect, per-rule toggles and dark/light theme."""

    DIALECTS = (
        "duckdb", "postgres", "snowflake", "tsql", "mysql",
        "bigquery", "sqlite", "databricks",
    )

    def __init__(self, master, config: LinterConfig, on_apply, _tr=None) -> None:
        super().__init__(master)
        self._config = config
        self._on_apply = on_apply
        self._tr = _tr or _default_translator()
        self.title(f"{self._tr('settings.title')} — SQLean Lint Pro")
        self.geometry("460x440")
        self.resizable(False, False)
        self.lift()
        self.attributes("-topmost", True)
        self.grab_set()

        container = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=0,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
        container.pack(fill="both", expand=True, padx=24, pady=20)

        heading = ctk.CTkLabel(
            container,
            text="Settings",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
        )
        heading.pack(anchor="w", pady=(0, 12))

        # Dialect
        dialect_row = ctk.CTkFrame(container, fg_color="transparent")
        dialect_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            dialect_row, text="SQL Dialect", font=ctk.CTkFont("Segoe UI", 13)
        ).pack(side="left")
        self._dialect_var = ctk.StringVar(value=config.dialect)
        ctk.CTkOptionMenu(
            dialect_row,
            values=list(self.DIALECTS),
            variable=self._dialect_var,
            width=180,
        ).pack(side="right")

        # Theme
        theme_row = ctk.CTkFrame(container, fg_color="transparent")
        theme_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            theme_row, text="Theme", font=ctk.CTkFont("Segoe UI", 13)
        ).pack(side="left")
        self._theme_var = ctk.StringVar(value=config.theme)
        ctk.CTkOptionMenu(
            theme_row,
            values=["dark", "light"],
            variable=self._theme_var,
            width=180,
            command=self._on_theme_change,
        ).pack(side="right")

        ctk.CTkLabel(
            container,
            text="Rules",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
        ).pack(anchor="w", pady=(16, 6))

        self._rule_vars: Dict[str, ctk.BooleanVar] = {}
        for rule in DEFAULT_RULES:
            var = ctk.BooleanVar(
                value=config.is_rule_enabled(rule.rule_id)
            )
            self._rule_vars[rule.rule_id] = var
            ctk.CTkCheckBox(
                container,
                text=f"{rule.rule_id} — {rule.title}",
                variable=var,
                font=ctk.CTkFont("Segoe UI", 12),
            ).pack(anchor="w", pady=2)

        footer = ctk.CTkFrame(container, fg_color="transparent")
        footer.pack(fill="x", pady=(20, 0))
        ctk.CTkButton(
            footer, text="Cancel", fg_color=COLOR_MUTED, command=self.destroy, width=100
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            footer, text="Apply", fg_color=COLOR_ACCENT, command=self._apply, width=100
        ).pack(side="right")

        self.after(100, self._restore_focus)

    def _restore_focus(self) -> None:
        self.grab_set()

    def _on_theme_change(self, value: str) -> None:
        ctk.set_appearance_mode(value)

    def _apply(self) -> None:
        new_config = self._config.__class__(
            dialect=self._dialect_var.get(),
            excludes=list(self._config.excludes),
            severities=dict(self._config.severities),
            enabled_rules={
                rid: var.get() for rid, var in self._rule_vars.items()
            },
            theme=self._theme_var.get(),
        )
        self._on_apply(new_config)
        self.destroy()


# ── Interactive diagnostics drawer (Phase 3) ───────────────────────────

class DiagnosticsPanel(ctk.CTkFrame):
    """Interactive lint-findings drawer with search, details and Solve.

    Renders the diagnostics list (``_list``), a live keyword/rule-ID filter,
    and — when a finding is clicked — a detail pane showing the exact rule ID,
    description and an explicit "How to Solve" block, plus a
    ``[ ⚡ Solve / Auto-Fix ]`` button that rewrites the active editor buffer.
    """

    def __init__(self, master, *, on_solve, bg_color=COLOR_ZONE,
                 surface_color=COLOR_SURFACE, accent_color=COLOR_ACCENT,
                 text_color=COLOR_TEXT, muted_color=COLOR_MUTED,
                 ok_color=COLOR_OK, warn_color=COLOR_WARN,
                 err_color=COLOR_ERR) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_solve = on_solve
        self._accent = accent_color
        self._zone = bg_color
        self._surface = surface_color
        self._text = text_color
        self._muted = muted_color
        self._ok = ok_color
        self._warn = warn_color
        self._err = err_color

        self._findings: List[dict] = []  # rendered finding index -> details

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── header: title + count + search filter ─────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="Diagnostics", font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=self._accent,
        ).grid(row=0, column=0, padx=(0, 8))

        self._result_count = ctk.CTkLabel(
            header, text="0 finding(s)", font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._result_count.grid(row=0, column=1, sticky="w")

        # ── [ Search Rule ] keyword / rule-ID filter ───────────────────
        self._search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            header, textvariable=self._search_var,
            placeholder_text="Search rule / keyword...",
            font=ctk.CTkFont("Segoe UI", 12), fg_color=self._zone,
        )
        self._search_entry.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self._search_entry.bind("<KeyRelease>", lambda _e: self._apply_filter())
        self._search_entry.bind("<Return>", lambda _e: self._apply_filter())

        # ── findings list (clickable) ─────────────────────────────────
        self._list = ctk.CTkTextbox(
            self, font=ctk.CTkFont("Consolas", 11), fg_color=self._zone,
            corner_radius=10, wrap="none", height=90,
            border_width=_BORDER_WIDTH, border_color=_BORDER_COLOR,
        )
        self._list.grid(row=1, column=0, sticky="nsew")
        self._list.configure(state="disabled")
        self._list.tag_config("CRITICAL", foreground=self._err)
        self._list.tag_config("HIGH", foreground=self._warn)
        self._list.tag_config("MEDIUM", foreground=self._accent)
        self._list.tag_config("LOW", foreground=self._muted)
        self._list.tag_config("ERROR", foreground=self._err)
        self._list.bind("<Button-1>", self._on_list_click)

        # ── detail pane: rule info + how-to-solve + Solve button ──────
        detail_row = ctk.CTkFrame(self, fg_color=self._surface, corner_radius=10,
                                  border_width=_BORDER_WIDTH,
                                  border_color=_BORDER_COLOR)
        detail_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        detail_row.grid_columnconfigure(0, weight=1)
        detail_row.grid_columnconfigure(3, weight=0)

        self._detail = ctk.CTkTextbox(
            detail_row, font=ctk.CTkFont("Consolas", 10),
            fg_color=self._zone, corner_radius=8, height=64, state="disabled",
            border_width=_BORDER_WIDTH, border_color=_BORDER_COLOR,
        )
        self._detail.grid(row=0, column=0, columnspan=3, padx=8, pady=8, sticky="ew")

        self._solve_btn = ctk.CTkButton(
            detail_row, text="\u26A1 Solve / Auto-Fix", font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=self._ok, text_color="#111111", width=140, height=30,
            state="disabled", command=self._solve,
        )
        self._solve_btn.grid(row=0, column=3, padx=(8, 8), pady=8)

        self._selected_index: Optional[int] = None

    # ── population from the scan results ──────────────────────────────

    def set_findings(self, results: List[tuple]) -> None:
        """Populate the list from ``[(path, row_tuple), ...]``."""
        self._findings = []
        for path, row in results:
            self._findings.append({"path": path, "row": row})
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        self._list.configure(state="normal")
        self._list.delete("1.0", "end")
        shown = 0
        for idx, finding in enumerate(self._findings):
            path, row = finding["path"], finding["row"]
            rule_id, message, severity, line, col, fixable = self._unpack(row)
            if query and query not in rule_id.lower() and \
                    query not in message.lower() and query not in severity.lower():
                continue
            tag = self._severity_tag(severity)
            self._list.insert(
                "end",
                f"[{severity}] {Path(path).name}:{line}:{col}  {rule_id}  {message}\n",
                tag,
            )
            shown += 1
        self._list.configure(state="disabled")
        self._result_count.configure(text=f"{shown} finding(s)")
        if not shown:
            self._show_detail(None)
        else:
            self._render_selected(self._selected_index)

    # ── click handling ────────────────────────────────────────────────

    def _on_list_click(self, event) -> None:
        try:
            index = self._list.index(f"@{event.x},{event.y}")
            line_no = int(index.split(".")[0])
        except Exception:  # noqa: BLE001
            return
        shown_idx = 0
        resolved = None
        for idx, finding in enumerate(self._findings):
            path, row = finding["path"], finding["row"]
            rule_id, message, severity, line, col, fixable = self._unpack(row)
            if self._visible(rule_id, message, severity):
                shown_idx += 1
                if shown_idx == line_no:
                    resolved = idx
                    break
        if resolved is not None:
            self._selected_index = resolved
            self._render_selected(resolved)

    def _visible(self, rule_id: str, message: str, severity: str) -> bool:
        query = self._search_var.get().strip().lower()
        if not query:
            return True
        return (query in rule_id.lower() or query in message.lower()
                or query in severity.lower())

    def _unpack(self, row) -> tuple:
        if len(row) == 6:
            return row
        # Fallback for legacy 5-tuples.
        rule_id, message, severity, line, col = row
        return rule_id, message, severity, line, col, False

    def _render_selected(self, index) -> None:
        if index is None or not (0 <= index < len(self._findings)):
            self._show_detail(None)
            return
        finding = self._findings[index]
        self._show_detail(finding)

    def _show_detail(self, finding) -> None:
        self._detail.configure(state="normal")
        self._detail.delete("1.0", "end")
        fixable = False
        if finding is None:
            self._detail.insert("end", "Click a finding to see rule details.")
            self._solve_btn.configure(state="disabled")
        else:
            row = finding["row"]
            rule_id, message, severity, line, col, fixable = self._unpack(row)
            howto = self._how_to_solve(rule_id)
            self._detail.insert(
                "end",
                f"Rule: {rule_id} ({severity})  [{Path(finding['path']).name}:{line}:{col}]\n"
                f"{message}\n\n"
                f"How to Solve:\n{howto}",
            )
            self._solve_btn.configure(
                state="normal" if fixable else "disabled"
            )
        self._detail.configure(state="disabled")

    @staticmethod
    def _how_to_solve(rule_id: str) -> str:
        try:
            from .knowledge import RULE_EDUCATION as _EDU

            info = _EDU.get(rule_id)
            if info:
                return f"{info.get('why', '')}\n\n{info.get('fix', '')}"
        except Exception:  # noqa: BLE001 - guidance is best-effort
            pass
        return "Review the flagged expression, rewrite it to avoid the anti-pattern, then re-run the scan to confirm it is resolved."

    def _solve(self) -> None:
        if self._selected_index is None:
            return
        if self._on_solve is not None:
            try:
                self._on_solve()
            except Exception:  # noqa: BLE001 - Solve degrades gracefully
                pass

    def selected_rule_id(self) -> Optional[str]:
        if self._selected_index is None or not (
                0 <= self._selected_index < len(self._findings)):
            return None
        row = self._findings[self._selected_index]["row"]
        return self._unpack(row)[0]

    @staticmethod
    def _severity_tag(severity: str) -> str:
        return severity.upper()


# ── Main dashboard ─────────────────────────────────────────────────────


def _char_offset_for(sql: str, line: int, col: int) -> int:
    """Convert 1-based ``(line, col)`` into a 0-based character offset."""
    lines = sql.split("\n")
    offset = 0
    for i in range(1, line):
        if i - 1 < len(lines):
            offset += len(lines[i - 1]) + 1
    col = max(0, min(col, len(lines[line - 1]) if line - 1 < len(lines) else 0))
    return offset + col


def _breadcrumb_for(sql: str) -> str:
    """Render a short AST breadcrumb (statement type ▸ target table)."""
    if not sql or not sql.strip():
        return ""
    dialect_map = {"D": "DuckDB", "T": "T-SQL", "PO": "PostgreSQL"}
    try:
        nodes, _dialect = safe_parse_sql(sql)
    except Exception:  # noqa: BLE001 - parse may fail on partial input
        return "—"
    if not nodes:
        return "—"
    crumbs = []
    for node in list(nodes)[:4]:
        try:
            kind = getattr(node, "key", "") or getattr(node, "key_name", "") or "?"
            table = ""
            for expr in node.find_all(sqlglot.exp.Table):
                try:
                    table = expr.name or ""
                except Exception:  # noqa: BLE001
                    table = ""
                if table:
                    break
            label = kind.upper()
            if table:
                label += " ▸ " + table
            crumbs.append(label)
        except Exception:  # noqa: BLE001
            continue
    return "  ›  ".join(crumbs) if crumbs else "—"


def _explain_plan_for(sql: str) -> str:
    """Return a human-readable EXPLAIN-style plan (best-effort AST walk)."""
    if not sql or not sql.strip():
        return "No statement selected."
    try:
        from sqlglot.optimizer import optimize

        nodes, _dialect = safe_parse_sql(sql)
        if not nodes:
            return "Could not parse the selected statement."
        lines = []
        for node in list(nodes)[:1]:
            for expr in node.walk():
                try:
                    nname = getattr(expr, "key", "") or expr.__class__.__name__
                    if nname in ("select", "from", "join", "where", "group",
                                 "order", "limit", "having"):
                        lines.append(f"  {nname.upper()}")
                except Exception:  # noqa: BLE001
                    pass
        if not lines:
            return "Statement parsed; no executable plan nodes found."
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Plan unavailable: {exc}"


def _param_literal(value: str) -> str:
    """Quote a parameter value as a SQL string literal."""
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


class Dashboard(ctk.CTkFrame):
    """Two-pane linting workbench."""

    def __init__(self, master, config: LinterConfig, on_open_settings, _tr=None,
                 on_open_assistant=None) -> None:
        super().__init__(master, fg_color=COLOR_BG, corner_radius=0,
                         border_width=_BORDER_WIDTH, border_color=_BORDER_COLOR)
        self._config = config
        self._tr = _tr or _default_translator()
        self._on_open_settings = on_open_settings
        self._on_open_assistant = on_open_assistant
        self._queue: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._results: List[tuple] = []
        self._current_file: Optional[Path] = None
        self._current_sql: str = ""
        self._anim_engine: Optional[CTkAnimationEngine] = None
        # Enterprise workspace state (VS Code / KNIME / Excel experiences).
        self._open_paths: List[str] = []
        self._table_catalog: List[str] = []
        self._column_catalog: List[str] = []
        self._is_dirty: bool = False
        self._palette: Optional[CommandPalette] = None
        self._intellisense: Optional[IntelliSense] = None
        self._query_runner: Optional[QueryRunner] = None
        self._last_grid_columns: List[str] = []
        self._last_grid_rows: List[dict] = []
        # Enterprise panel visibility flags (command palette toggles).
        self._dag_visible: bool = True
        self._grid_visible: bool = True
        # Phase 4: persistent query history ledger + searchable sidebar.
        self._history_ledger = QueryHistoryLedger()
        self._history_drawer: Optional[QueryHistoryDrawer] = None
        self._history_visible: bool = False
        # Selected finding storage for the DiagnosticsPanel.
        self._selected_detail_index: Optional[int] = None
        # Phase 2: non-blocking notifications + skeleton loader.
        self._toasts = ToastStack(self)
        self._skeleton = None
        # Phase 2: injectable SQL parameters (``:name``) resolved before run.
        self._param_values: Dict[str, str] = {}
        # Phase 3: rolling undo/redo snapshot tracker (last 20, 30 s budget).
        self._history_tracker = HistoryTracker()
        # Bugfix: recursion guard that breaks the _load_file_preview ⇄
        # _on_tab_changed circular event loop (both call _tabs.open).
        self._switching_tabs = False
        # Bottom dock visibility (Ctrl+J toggles the Query Results/Chart/Logs
        # tray so the code editor can reclaim the vertical space).
        self._dock_visible: bool = True
        # Guard against overlapping background DuckDB runs (Phase 4 threading).
        self._query_running: bool = False

        self._build_header()
        self._build_panes()
        self._build_status_bar()
        self._setup_workspace()
        self._bind_shortcuts()
        self.after(100, self._poll_queue)

    # ── layout ────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0,
                              border_width=_BORDER_WIDTH,
                              border_color=_BORDER_COLOR)
        header.pack(fill="x")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text=self._tr("app.title"),
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COLOR_ACCENT,
        )
        title.grid(row=0, column=0, padx=20, pady=12, sticky="w")

        self._dialect_badge = ctk.CTkLabel(
            header,
            text=f"{self._tr('dashboard.dialect')}: {self._config.dialect}",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COLOR_ZONE,
            corner_radius=SPACING["RADIUS_SM"],
            padx=SPACING["BANNER_PAD_X"],
        )
        self._dialect_badge.grid(row=0, column=1, padx=8)

        ctk.CTkButton(
            header,
            text=self._tr("dashboard.settings"),
            font=ctk.CTkFont("Segoe UI", 12),
            width=90,
            height=32,
            command=self._on_open_settings,
        ).grid(row=0, column=2, padx=8)

        ctk.CTkButton(
            header,
            text=self._tr("dashboard.scan_directory"),
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            width=140,
            height=32,
            fg_color=COLOR_ACCENT,
            command=self._on_scan_directory,
        ).grid(row=0, column=3, padx=(8, 8))

        ctk.CTkButton(
            header,
            text="Support",
            font=ctk.CTkFont("Segoe UI", 12),
            width=90,
            height=32,
            fg_color=COLOR_SURFACE,
            border_width=1,
            command=self._on_toggle_assistant,
        ).grid(row=0, column=4, padx=(0, 8))

        ctk.CTkButton(
            header,
            text="Documentation",
            font=ctk.CTkFont("Segoe UI", 12),
            width=110,
            height=32,
            fg_color=COLOR_SURFACE,
            border_width=1,
            command=self._on_open_documentation,
        ).grid(row=0, column=5, padx=(0, 8))

        ctk.CTkButton(
            header,
            text="About",
            font=ctk.CTkFont("Segoe UI", 12),
            width=90,
            height=32,
            fg_color=COLOR_SURFACE,
            border_width=1,
            command=self._on_open_about,
        ).grid(row=0, column=6, padx=(0, 20))

    def _build_panes(self) -> None:
        # New responsive multi-pane docking workspace.
        self._workspace = WorkspaceLayout(
            self,
            on_tab_select=self._on_tab_selected,
            on_tab_close=self._on_tab_closed,
            on_new_tab=self._new_tab,
        )
        self._workspace.pack(fill="both", expand=True, padx=2, pady=2)

        self._build_left_pane()
        self._build_right_pane()

    def _build_left_pane(self) -> None:
        left = ctk.CTkFrame(self._workspace, fg_color=COLOR_SURFACE, corner_radius=8,
                            width=260, border_width=_BORDER_WIDTH,
                            border_color=_BORDER_COLOR)
        self._workspace.add_left_content(left)
        left.grid_propagate(False)
        left.grid_columnconfigure(0, weight=1)

        # Drop zone
        self._drop_zone = ctk.CTkFrame(left, fg_color=COLOR_ZONE, corner_radius=12,
                                       height=190, border_width=_BORDER_WIDTH,
                                       border_color=_BORDER_COLOR)
        self._drop_zone.grid(row=0, column=0, padx=18, pady=(20, 8), sticky="ew")
        self._drop_zone.grid_propagate(False)

        self._drop_zone_label = ctk.CTkLabel(
            self._drop_zone,
            text=(
                "Drag and Drop SQL Files"
                if _DND_AVAILABLE
                else "SQL Files\n\n(click to browse)"
            ),
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=COLOR_MUTED,
            justify="center",
        )
        self._drop_zone_label.place(relx=0.5, rely=0.40, anchor="center")

        self._browse_btn = ctk.CTkButton(
            self._drop_zone,
            text="Browse SQL Files...",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COLOR_ACCENT,
            width=200,
            command=self._on_browse,
        )
        self._browse_btn.place(relx=0.5, rely=0.72, anchor="center")

        if _DND_AVAILABLE:
            self._drop_zone.drop_target_register(DND_FILES)
            self._drop_zone.dnd_bind("<<Drop>>", self._on_drop)

        # KNIME/Excel-style interactive explorer.
        self._explorer = InteractiveFileExplorer(
            left,
            on_select=self._on_explorer_select,
            on_toggle=self._on_section_toggle,
            bg_color=COLOR_SURFACE,
            zone_color=COLOR_ZONE,
            accent_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
            width=260,
        )
        self._explorer.grid(row=1, column=0, padx=14, pady=(4, 8), sticky="nsew")
        left.grid_rowconfigure(1, weight=1)

        # Kept as the internal backing store for apply_fixes / clear logic.
        self._file_listbox = ctk.CTkTextbox(
            left, font=ctk.CTkFont("Consolas", 11), fg_color=COLOR_SURFACE, height=1
        )
        self._file_listbox.grid(row=2, column=0, sticky="ew", padx=18)
        self._file_listbox.configure(state="disabled")
        self._file_listbox.grid_remove()

        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(row=3, column=0, padx=18, pady=(0, 18), sticky="ew")
        actions.grid_columnconfigure(0, weight=1)

        self._clear_btn = ctk.CTkButton(
            actions,
            text="Clear",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COLOR_MUTED,
            width=110,
            command=self._on_clear,
        )
        self._clear_btn.grid(row=0, column=0, sticky="e")

    def _build_right_pane(self, master=None) -> None:
        master = master or self._workspace
        right = ctk.CTkFrame(master, fg_color=COLOR_SURFACE, corner_radius=8,
                             border_width=_BORDER_WIDTH,
                             border_color=_BORDER_COLOR)
        self._workspace.add_center_content(right)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=4)
        right.grid_rowconfigure(3, weight=3)

        toolbar = ctk.CTkFrame(
            right, fg_color="transparent", height=SPACING["TOOLBAR_HEIGHT"]
        )
        toolbar.grid(row=0, column=0, padx=18, pady=(16, 8), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)
        toolbar.grid_propagate(False)

        self._summary_label = ctk.CTkLabel(
            toolbar,
            text="Ready",
            font=ctk.CTkFont(TYPOGRAPHY["BODY_BOLD"][0], TYPOGRAPHY["BODY_BOLD"][1], "bold"),
            text_color=COLOR_MUTED,
        )
        self._summary_label.grid(row=0, column=0, sticky="w")

        self._autofix_btn = ctk.CTkButton(
            toolbar,
            text="Auto-Fix All",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COLOR_OK,
            text_color=TOKENS["TEXT_INVERSE"],
            width=120,
            height=SPACING["BTN_HEIGHT"],
            corner_radius=SPACING["RADIUS_SM"],
            state="disabled",
            command=self._on_autofix,
        )
        self._autofix_btn.grid(row=0, column=1, padx=(8, 0))

        # Run the active query through the local (zero-cloud) DuckDB engine.
        self._run_query_btn = ctk.CTkButton(
            toolbar,
            text="Run Query (F5)",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=COLOR_ACCENT,
            text_color=TOKENS["TEXT_INVERSE"],
            width=120,
            height=SPACING["BTN_HEIGHT"],
            corner_radius=SPACING["RADIUS_SM"],
            command=self._run_query,
        )
        self._run_query_btn.grid(row=0, column=2, padx=(8, 0))

        # One-click executive export (PDF + XLSX).
        self._export_btn = ctk.CTkButton(
            toolbar,
            text="Export Report",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
            width=110,
            height=SPACING["BTN_HEIGHT"],
            corner_radius=SPACING["RADIUS_SM"],
            command=self._export_report,
        )
        self._export_btn.grid(row=0, column=3, padx=(8, 0))

        # Phase 3: toggle between the data grid and the chart view.
        self._chart_toggle_btn = ctk.CTkButton(
            toolbar,
            text="Chart",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
            width=84,
            height=SPACING["BTN_HEIGHT"],
            corner_radius=SPACING["RADIUS_SM"],
            command=self._toggle_chart_view,
        )
        self._chart_toggle_btn.grid(row=0, column=5, padx=(8, 0))

        # Live SQL health score (0-100 + letter badge).
        self._health_score = SQLHealthScore(
            toolbar,
            score=0,
            radius=26,
            bg_color=COLOR_SURFACE,
            zone_color=COLOR_ZONE,
            accent_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
        )
        self._health_score.grid(row=0, column=4, padx=(12, 0))

        # ── VS Code multi-tab bar above the editor ──────────────────────
        self._tabs = CTkTabBar(
            right,
            bg_color=COLOR_SURFACE,
            zone_color=COLOR_ZONE,
            accent_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
            on_select=self._on_tab_selected,
            on_close=self._on_tab_closed,
            on_change=self._on_tab_changed,
        )
        self._tabs.grid(row=1, column=0, padx=14, pady=(0, 6), sticky="ew")

        # ── Code & Diff Editor Canvas (must never collapse to 0px) ─────
        # A dedicated container owns the editor row so that on every layout
        # pass the split diff viewer + minimap are packed with expand=True.
        # The row above (grade/weight) ensures the editor always receives a
        # positive fraction of the vertical space, even when the results
        # grid or analytics rows are collapsed by the user.
        self.editor_container = ctk.CTkFrame(
            right, fg_color="transparent",
        )
        self.editor_container.grid(
            row=2, column=0, padx=14, pady=(0, 8), sticky="nsew",
        )
        self.editor_container.grid_columnconfigure(0, weight=1)
        self.editor_container.grid_rowconfigure(1, weight=1)

        # Phase 2: live AST breadcrumb trail above the editor (updated on
        # cursor movement so it always reflects the query under the caret).
        self._crumb_bar = ctk.CTkFrame(self.editor_container, fg_color="transparent",
                                       height=22)
        self._crumb_bar.grid(row=0, column=0, columnspan=2, sticky="ew",
                             pady=(0, 2))
        self._crumb_bar.grid_propagate(False)
        self._crumb_label = ctk.CTkLabel(
            self._crumb_bar,
            text="",
            font=ctk.CTkFont("Consolas", 11),
            text_color=COLOR_MUTED,
            anchor="w",
        )
        self._crumb_label.pack(fill="x", padx=2)

        self._diff_viewer = SplitCodeDiffViewer(
            self.editor_container,
            left_title="Original",
            right_title="Optimized",
            bg_color=COLOR_SURFACE,
            zone_color=COLOR_ZONE,
            text_color=COLOR_TEXT,
            accent_color=COLOR_ACCENT,
            ok_color=COLOR_OK,
            muted_color=COLOR_MUTED,
            on_run_selection=self._run_statement_under_cursor,
            on_format_sql=self._on_autofix,
            on_explain_plan=self._open_explain_plan,
        )
        self._diff_viewer.grid(row=1, column=0, padx=(0, 6), sticky="nsew")
        self._diff_viewer.grid_propagate(False)
        # Enforce a hard minimum so the editor can never collapse away.
        try:
            self._diff_viewer.configure(height=140)
        except Exception:  # noqa: BLE001
            pass
        self.editor_container.grid_rowconfigure(1, weight=1, minsize=140)

        editor_pane = self._diff_viewer.get_original_pane()
        self._minimap = CodeMinimap(
            self.editor_container,
            editor=editor_pane,
            width=88,
            bg_color=COLOR_ZONE,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
            accent_color=COLOR_ACCENT,
        )
        self._minimap.grid(row=1, column=1, sticky="ns")

        # ── Bottom Dock: tabbed Query Results / Quick Chart / Terminal ──
        # Wraps the results grid, analytics and data-grid rows behind a
        # collapsible dock (Ctrl+J toggles visibility).
        self._dock = ctk.CTkFrame(right, fg_color="transparent")
        self._dock.grid(row=3, column=0, padx=14, pady=(0, 8), sticky="nsew")
        self._dock.grid_columnconfigure(0, weight=1)
        self._dock.grid_rowconfigure(2, weight=2)
        self._dock.grid_rowconfigure(4, weight=3)
        self._dock.grid_rowconfigure(6, weight=2)

        dock_tabs = ctk.CTkFrame(self._dock, fg_color=COLOR_ZONE,
                                 corner_radius=8, height=30)
        dock_tabs.grid(row=0, column=0, sticky="ew")
        dock_tabs.grid_columnconfigure(3, weight=1)

        self._dock_tab_labels: Dict[str, ctk.CTkLabel] = {}
        self._dock_tab_frames: Dict[str, ctk.CTkFrame] = {}
        self._dock_active: str = "results"
        for idx, (_key, label) in enumerate(
            (("results", "▸ Query Results"),
             ("chart", "▸ Chart View"),
             ("terminal", "▸ Engine Console"))
        ):
            tab_lbl = ctk.CTkLabel(
                dock_tabs,
                text=label,
                font=ctk.CTkFont("Consolas", 11, "bold"),
                text_color=COLOR_MUTED,
                fg_color=COLOR_SURFACE,
                corner_radius=6,
                padx=10,
                pady=4,
                cursor="hand2",
            )
            tab_lbl.grid(row=0, column=idx, padx=(8, 0), pady=4)
            tab_lbl.bind("<Button-1>", lambda _e, k=_key: self._select_dock_tab(k))
            tab_lbl.bind("<Enter>", lambda _e, k=_key: tab_lbl.configure(
                fg_color=TOKENS["SURFACE_HOVER"]))
            tab_lbl.bind("<Leave>", lambda _e, k=_key: tab_lbl.configure(
                fg_color=TOKENS["SURFACE_CARD"]
                if self._dock_active == k else COLOR_SURFACE))
            self._dock_tab_labels[_key] = tab_lbl

        self._dock_collapse_btn = ctk.CTkButton(
            dock_tabs,
            text="\u25BE",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            width=28,
            height=24,
            corner_radius=6,
            fg_color=COLOR_SURFACE,
            border_width=1,
            border_color=_BORDER_COLOR,
            command=self._toggle_dock,
        )
        self._dock_collapse_btn.grid(row=0, column=3, sticky="e", padx=(8, 8))

        # Results table (lives inside the dock, starts visible).
        self._results_text = ctk.CTkTextbox(
            self._dock,
            font=ctk.CTkFont("Consolas", 11),
            fg_color=COLOR_ZONE,
            corner_radius=10,
            wrap="none",
            height=110,
            border_width=_BORDER_WIDTH,
            border_color=_BORDER_COLOR,
        )
        self._results_text.grid(row=2, column=0, padx=0, pady=(6, 0), sticky="nsew")
        self._results_text.configure(state="disabled")
        self._results_text.tag_config("CRITICAL", foreground=COLOR_ERR)
        self._results_text.tag_config("HIGH", foreground=COLOR_WARN)
        self._results_text.tag_config("MEDIUM", foreground=COLOR_ACCENT)
        self._results_text.tag_config("LOW", foreground=COLOR_MUTED)
        self._results_text.tag_config("ERROR", foreground=COLOR_ERR)

        # ── KNIME analytics row: DAG viewer + data profiler ─────────────
        analytics = ctk.CTkFrame(self._dock, fg_color="transparent")
        self._dock_analytics = analytics
        analytics.grid(row=4, column=0, padx=0, pady=(8, 0), sticky="nsew")
        analytics.grid_columnconfigure(0, weight=2)
        analytics.grid_columnconfigure(1, weight=1)
        analytics.grid_rowconfigure(0, weight=1)

        self._dag_viewer = DagCanvas(
            analytics,
            bg_color=COLOR_ZONE,
            on_node_clicked=self._on_ast_node_clicked,
        )
        self._dag_viewer.grid(row=0, column=0, padx=(0, 6), sticky="nsew")

        self._profiler = ProfilerView(
            analytics,
            bg_color=COLOR_SURFACE,
            zone_color=COLOR_ZONE,
            accent_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
            ok_color=COLOR_OK,
            warn_color=COLOR_WARN,
        )
        self._profiler.grid(row=0, column=1, sticky="nsew")

        # ── Excel data grid (live query preview) ────────────────────────
        self._data_grid = DataGrid(
            self._dock,
            bg_color=COLOR_ZONE,
            surface_color=COLOR_SURFACE,
            accent_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
        )
        self._data_grid.grid(row=6, column=0, padx=0, pady=(8, 0), sticky="nsew")

        # Phase 3: Excel/KNIME-style chart view shares row 6 with the grid;
        # only one of the two is visible at a time (grid|chart toggle).
        self._chart_view = ChartView(
            self._dock,
            bg_color=COLOR_ZONE,
            text_color=COLOR_TEXT,
            muted_color=COLOR_MUTED,
            accent_color=COLOR_ACCENT,
        )
        self._chart_view.grid(row=6, column=0, padx=0, pady=(8, 0), sticky="nsew")
        self._chart_view.grid_remove()
        self._chart_visible = False

        # ── Engine console (terminal tab of the bottom dock) ────────────
        self._console_text = ctk.CTkTextbox(
            self._dock,
            font=ctk.CTkFont("Consolas", 11),
            fg_color=COLOR_ZONE,
            text_color=COLOR_TEXT,
            corner_radius=8,
            wrap="none",
            height=110,
            border_width=_BORDER_WIDTH,
            border_color=_BORDER_COLOR,
        )
        self._console_text.grid(row=2, column=0, padx=0, pady=(6, 0), sticky="nsew")
        self._console_text.grid_remove()
        self._console_text.configure(state="disabled")
        self._console_text.tag_config("OK", foreground=COLOR_OK)
        self._console_text.tag_config("WARN", foreground=COLOR_WARN)
        self._console_text.tag_config("ERR", foreground=COLOR_ERR)
        self._console_text.tag_config("INFO", foreground=COLOR_ACCENT)

        # Register the dock content groups so tab switching can show/hide them.
        self._dock_tab_frames = {
            "results": self._results_text,
            "chart": self._chart_view,
            "terminal": self._console_text,
        }

        # Pulse skeleton loader shown during background operations.
        self._skeleton = SkeletonPulse(
            right, width=160, height=18, corner_radius=4
        )

    def _setup_workspace(self) -> None:
        """Wire command palette, IntelliSense and the DuckDB query runner."""
        # Command palette (Ctrl+Shift+P) with every IDE action.
        self._palette = CommandPalette(
            self, commands=self._build_commands(), animation_engine=self._anim_engine
        )
        # Register the full Phase 2 IDE action set (Spotlight palette).
        self._palette.register_ide_actions(
            new_query_tab=self._new_tab,
            open_sql_file=self._open_sql_file_dialog,
            save_active_query=self._on_save_file,
            clear_workspace=self._clear_cache,
            run_query=self._run_query,
            lint_buffer=self._scan_current_file,
            auto_fix_all=self._on_autofix,
            toggle_nav_rail=self._toggle_nav_rail,
            toggle_diagnostics=self._toggle_data_grid,
            toggle_ast_canvas=self._toggle_dag_viewer,
            switch_duckdb=lambda: self._set_dialect("duckdb"),
            switch_tsql=lambda: self._set_dialect("tsql"),
            switch_postgres=lambda: self._set_dialect("postgres"),
            switch_snowflake=lambda: self._set_dialect("snowflake"),
            apply_vercel_dark=lambda: self._apply_palette_theme("vercel_dark"),
            apply_nord_slate=lambda: self._apply_palette_theme("nord_slate"),
            apply_high_contrast=lambda: self._apply_palette_theme("high_contrast"),
        )

        # IntelliSense bound to the editor pane.
        try:
            self._intellisense = IntelliSense(
                self._diff_viewer.get_original_pane(), root=self
            )
            self._intellisense.set_tables([])
            self._intellisense.set_columns([])
        except Exception:  # noqa: BLE001 - feature degrades gracefully
            self._intellisense = None

        # Local zero-cloud query engine for the data grid.
        if _DUCKDB_AVAILABLE:
            try:
                connector = DuckDBLocalConnector()
                connector.connect({"path": None})
                self._query_runner = QueryRunner(connector)
            except Exception:  # noqa: BLE001 - DuckDB optional
                self._query_runner = None
        else:
            self._query_runner = None

        # Phase 2/4 wiring: cursor-aware editor extras, menu bar and session.
        self._bind_editor_nav()
        self._refresh_ruler()
        self._build_menu_bar()
        self.after(150, self._restore_session)

    def _bind_editor_nav(self) -> None:
        """Update AST breadcrumbs + diagnostics ruler on caret movement."""
        try:
            text = self._underlying_editor()
            text.bind("<<CursorMoved>>",
                      lambda e: self._schedule_crumbs(), add="+")
            text.bind("<KeyRelease>", lambda e: self._schedule_crumbs(), add="+")
            # Phase 3: feed edits into the rolling undo snapshot tracker.
            text.bind("<KeyRelease>", lambda e: self._track_edit(), add="+")
            # Phase 4: live cursor line/col readout in the status footer.
            text.bind("<KeyRelease>", lambda e: self._update_cursor_readout(),
                      add="+")
            text.bind("<Button-1>", lambda e: self._update_cursor_readout(),
                      add="+")
        except Exception:  # noqa: BLE001 - nav extras degrade gracefully
            pass
        if self._diff_viewer is not None:
            try:
                self._diff_viewer.set_ruler_violations(self._current_violations())
            except Exception:  # noqa: BLE001
                pass

    def _update_cursor_readout(self) -> None:
        """Refresh the Ln/Col readout in the status footer from the caret."""
        try:
            text = self._underlying_editor()
            if text is None:
                return
            index = text.index("insert")
            line, col = index.split(".")
            self._cursor_label.configure(text=f"Ln {line}, Col {int(col) + 1}")
        except Exception:  # noqa: BLE001
            pass

    def _underlying_editor(self):
        """Return the Tk Text widget of the Original editor pane."""
        try:
            return self._diff_viewer.get_original_pane()._textbox
        except Exception:  # noqa: BLE001
            return None

    # ── Phase 2: statement-under-cursor execution ─────────────────────

    def _run_statement_under_cursor(self) -> None:
        """Execute only the statement under the caret via DuckDB."""
        text = self._underlying_editor()
        if text is None:
            self._run_query()
            return
        try:
            offset = int(text.index("insert").split(".")[0])  # 1-based line
        except Exception:  # noqa: BLE001
            offset = 1
        sql = text.get("1.0", "end-1c")
        # Convert line + column to a character offset.
        col = 0
        try:
            col = int(text.index("insert").split(".")[1])
        except Exception:  # noqa: BLE001
            col = 0
        char_offset = _char_offset_for(sql, offset, col)
        statement = statement_under_cursor(sql, char_offset)
        if not statement or not statement.strip():
            self._set_status("No statement under cursor", idle=True)
            return
        self._current_sql = statement
        self._run_query()

    # ── Phase 2: AST breadcrumbs ──────────────────────────────────────

    def _schedule_crumbs(self) -> None:
        try:
            if getattr(self, "_crumb_after", None) is not None:
                self.after_cancel(self._crumb_after)
        except Exception:  # noqa: BLE001
            pass
        self._crumb_after = self.after(150, self._update_breadcrumbs)

    def _update_breadcrumbs(self) -> None:
        try:
            text = self._underlying_editor()
            if text is None:
                return
            sql = text.get("1.0", "end-1c")
            node = _breadcrumb_for(sql)
            if hasattr(self, "_crumb_label"):
                self._crumb_label.configure(text=node)
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 1 ruler + diagnostics ───────────────────────────────────

    def _current_violations(self):
        """Return ``(line, severity)`` pairs for the overview ruler."""
        pairs = []
        for res in self._results:
            try:
                line = int(res[2])  # line index
                sev = str(res[3]).upper()
            except Exception:  # noqa: BLE001
                continue
            if line > 0:
                pairs.append((line, sev))
        return pairs

    def _refresh_ruler(self) -> None:
        if self._diff_viewer is not None:
            try:
                self._diff_viewer.set_ruler_violations(self._current_violations())
            except Exception:  # noqa: BLE001
                pass

    # ── Phase 3: grid / chart toggle ──────────────────────────────────

    # ── Bottom dock toggle (Ctrl+J) ────────────────────────────────────

    def _select_dock_tab(self, key: str) -> None:
        """Switch the bottom dock between Query Results / Chart / Console."""
        if key not in ("results", "chart", "terminal"):
            return
        self._dock_active = key
        # Repaint the tab strip (active tab highlighted).
        for k, lbl in self._dock_tab_labels.items():
            try:
                active = k == key
                lbl.configure(
                    fg_color=TOKENS["SURFACE_CARD"] if active else COLOR_SURFACE,
                    text_color=COLOR_TEXT if active else COLOR_MUTED,
                )
            except Exception:  # noqa: BLE001
                pass
        # Show the dock so the user sees the switched content.
        try:
            self._dock_visible = True
            self._dock.grid()
            self._dock_collapse_btn.configure(text="\u25BE")
        except Exception:  # noqa: BLE001
            pass
        try:
            if key == "results":
                self._results_text.grid()
                self._console_text.grid_remove()
                self._chart_view.grid_remove()
                self._data_grid.grid()
                self._dock_analytics.grid()
            elif key == "chart":
                self._results_text.grid_remove()
                self._console_text.grid_remove()
                self._data_grid.grid_remove()
                self._dock_analytics.grid_remove()
                self._chart_view.grid()
                self._feed_chart(self._last_grid_columns, self._last_grid_rows)
            else:  # terminal / engine console
                self._results_text.grid_remove()
                self._data_grid.grid_remove()
                self._chart_view.grid_remove()
                self._dock_analytics.grid_remove()
                self._console_text.grid()
            self._set_status(f"Dock: {key}", idle=True)
        except Exception:  # noqa: BLE001 - dock switching is best-effort
            pass

    def _log_console(self, text: str, kind: str = "INFO") -> None:
        """Append a line to the engine console (terminal dock tab)."""
        try:
            if not hasattr(self, "_console_text") or self._console_text is None:
                return
            tag = kind.upper()
            if tag not in ("OK", "WARN", "ERR", "INFO"):
                tag = "INFO"
            self._console_text.configure(state="normal")
            self._console_text.insert("end", f"[{tag}] {text}\n", tag)
            self._console_text.see("end")
            self._console_text.configure(state="disabled")
        except Exception:  # noqa: BLE001 - logging is best-effort
            pass

    def _toggle_dock(self) -> None:
        """Show or hide the bottom dock (Query Results / Chart / Console)."""
        self._dock_visible = not self._dock_visible
        try:
            if self._dock_visible:
                self._dock.grid()
                self._dock_collapse_btn.configure(text="\u25BE")
                self._set_status("Dock shown", idle=True)
            else:
                self._dock.grid_remove()
                self._dock_collapse_btn.configure(text="\u25B2")
                self._set_status("Dock hidden - editor maximized", idle=True)
        except Exception:  # noqa: BLE001 - dock toggle is best-effort
            pass

    def _toggle_chart_view(self) -> None:
        """Swap the results between the data grid and the chart view."""
        try:
            self._chart_visible = not self._chart_visible
            if self._chart_visible:
                self._data_grid.grid_remove()
                self._chart_view.grid()
                self._feed_chart(self._last_grid_columns, self._last_grid_rows)
                self._chart_toggle_btn.configure(text="Grid")
            else:
                self._chart_view.grid_remove()
                self._data_grid.grid()
                self._chart_toggle_btn.configure(text="Chart")
        except Exception:  # noqa: BLE001 - chart toggle is best-effort
            pass

    def _feed_chart(self, columns: List[str], rows: List[dict]) -> None:
        try:
            if self._chart_view is not None:
                self._chart_view.set_data(list(columns), list(rows))
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 2: parameter injection drawer ───────────────────────────

    def _find_params(self, sql: str) -> List[str]:
        """Return unique ``:name`` / ``{{name}}`` SQL parameters (validated).

        Only well-formed identifiers are accepted (ASCII letters, digits and
        underscore, not starting with a digit). This hard-walling rejects any
        ``:`` / ``{{`` placeholder that could smuggle SQL, so parameter
        injection (Phase 4) is impossible regardless of the drawer input.
        """
        names: List[str] = []
        try:
            for token in re.findall(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", sql):
                if token and token not in names:
                    names.append(token)
            for token in re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", sql):
                if token and token not in names:
                    names.append(token)
        except Exception:  # noqa: BLE001
            pass
        return names

    def _resolve_params(self, sql: str) -> str:
        """Substitute ``:name`` / ``{{name}}`` placeholders with the values.

        Values are always interpolated through :func:`_param_literal` which
        SQL-escapes single quotes, neutralising SQL-injection attempts carried
        in a parameter value (e.g. ``'; DROP TABLE...`` becomes a plain string).
        Only names returned by :meth:`_find_params` (already identifier-walled)
        are ever substituted.
        """
        resolved = sql
        for name, value in self._param_values.items():
            if value == "":
                continue
            imported = _param_literal(value)
            resolved = resolved.replace(f":{name}", imported)
            resolved = resolved.replace("{{" + name + "}}", imported)
        return resolved

    def _toggle_parameter_drawer(self) -> None:
        """Open a modal to fill ``:name`` parameters before running the query."""
        try:
            text = self._underlying_editor()
            sql = text.get("1.0", "end-1c") if text is not None else self._current_sql
            names = self._find_params(sql)
            if not names:
                self._set_status("No :param / {{param}} found in the buffer", idle=True)
                return
            modal = ctk.CTkToplevel(self)
            modal.title("Query Parameters")
            modal.geometry("460x380")
            modal.lift()
            frame = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=10,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
            frame.pack(fill="both", expand=True, padx=14, pady=14)
            ctk.CTkLabel(frame, text="Parameter values",
                         font=ctk.CTkFont("Segoe UI", 14, "bold"),
                         text_color=COLOR_ACCENT).pack(anchor="w", padx=12, pady=6)
            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            entries: Dict[str, ctk.CTkEntry] = {}
            for i, name in enumerate(names):
                ctk.CTkLabel(body, text=f":{name}",
                             font=ctk.CTkFont("Consolas", 12),
                             text_color=COLOR_TEXT).grid(
                    row=i, column=0, sticky="w", pady=4)
                entry = ctk.CTkEntry(body, font=ctk.CTkFont("Consolas", 12),
                                     width=240)
                entry.insert(0, self._param_values.get(name, ""))
                entry.grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=4)
                entries[name] = entry
            body.grid_columnconfigure(1, weight=1)

            def apply_params() -> None:  # noqa: ANN202
                for name, entry in entries.items():
                    self._param_values[name] = entry.get()
                modal.destroy()
                self._set_status(f"Set {len(entries)} parameter(s)", idle=True)

            ctk.CTkButton(frame, text="Apply & Write Back", height=32,
                          command=apply_params).pack(pady=(0, 12))
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 3: undo snapshot history ────────────────────────────────

    def _track_edit(self) -> None:
        try:
            text = self._underlying_editor()
            if text is not None:
                self._history_tracker.push(text.get("1.0", "end-1c"))
        except Exception:  # noqa: BLE001
            pass

    def _undo_edit(self) -> None:
        try:
            snapshot = self._history_tracker.take_undo()
            text = self._underlying_editor()
            if snapshot is not None and text is not None:
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.insert("1.0", snapshot)
                text.configure(state="disabled")
                self._set_status("Restored snapshot", idle=True)
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 2: AST explain plan ─────────────────────────────────────

    def _open_explain_plan(self) -> None:
        """Open a lightweight EXPLAIN-style modal for the caret statement."""
        try:
            sql = self._current_sql or (
                self._diff_viewer.get_optimized_pane().get("1.0", "end-1c")
                if self._diff_viewer is not None else ""
            )
            modal = ctk.CTkToplevel(self)
            modal.title("Explain Plan")
            modal.geometry("640x420")
            modal.lift()
            frame = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=10,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
            frame.pack(fill="both", expand=True, padx=14, pady=14)
            ctk.CTkLabel(frame, text="Query plan", font=ctk.CTkFont("Segoe UI", 14,
                                                                   "bold"),
                         text_color=COLOR_ACCENT).pack(anchor="w", padx=12, pady=6)
            body = ctk.CTkTextbox(frame, font=ctk.CTkFont("Consolas", 12),
                                  fg_color=COLOR_ZONE, text_color=COLOR_TEXT,
                                  wrap="none")
            body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            body.insert("1.0", _explain_plan_for(sql))
            body.configure(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 4: menu bar ─────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        """Build a classic menu bar (File / Edit / Dialect / View / Help).

        ``CTkMenuBar`` is unavailable in this customtkinter build, so the
        menus are assembled from :class:`tk.Menu` attached to the toplevel.
        """
        top = self.winfo_toplevel()
        try:
            if top is not None and getattr(top, "tk", None):
                from tkinter import Menu as _Menu

                menubar = _Menu(top)
                file_m = _Menu(menubar, tearoff=0)
                file_m.add_command(label="New Query Tab\tCtrl+T",
                                   command=self._new_tab)
                file_m.add_command(label="Open SQL File\tCtrl+O",
                                   command=self._on_browse)
                file_m.add_command(label="Save\tCtrl+S",
                                   command=self._on_save_file)
                file_m.add_separator()
                file_m.add_command(label="Scan Directory",
                                   command=self._on_scan_directory)
                file_m.add_command(label="Run Query\tF5",
                                   command=self._run_query)
                file_m.add_separator()
                file_m.add_command(label="Exit",
                                   command=self._on_quit)
                menubar.add_cascade(label="File", menu=file_m)

                edit_m = _Menu(menubar, tearoff=0)
                edit_m.add_command(label="Auto-Fix Lint Errors\tCtrl+R",
                                   command=self._on_autofix)
                edit_m.add_command(label="Format SQL", command=self._format_sql)
                menubar.add_cascade(label="Edit", menu=edit_m)

                dialect_m = _Menu(menubar, tearoff=0)
                for name in ("duckdb", "tsql", "postgres", "snowflake", "mysql",
                             "bigquery"):
                    dialect_m.add_command(
                        label=name.capitalize(),
                        command=lambda d=name: self._set_dialect(d),
                    )
                menubar.add_cascade(label="Dialect", menu=dialect_m)

                view_m = _Menu(menubar, tearoff=0)
                view_m.add_command(label="Toggle Data Grid",
                                   command=self._toggle_data_grid)
                view_m.add_command(label="Toggle AST Canvas",
                                   command=self._toggle_dag_viewer)
                view_m.add_command(label="Command Palette\tCtrl+Shift+P",
                                   command=self._open_command_palette)
                menubar.add_cascade(label="View", menu=view_m)

                help_m = _Menu(menubar, tearoff=0)
                help_m.add_command(label="Keyboard Shortcuts\tCtrl+H",
                                   command=self._open_shortcuts_modal)
                help_m.add_command(label="Documentation",
                                   command=self._on_open_documentation)
                help_m.add_command(label="About", command=self._on_open_about)
                menubar.add_cascade(label="Help", menu=help_m)

                top.configure(menu=menubar)
        except Exception:  # noqa: BLE001
            pass

    def _on_quit(self) -> None:
        self._save_session()
        top = self.winfo_toplevel()
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 4: session persistence ──────────────────────────────────

    def _session_path(self):
        return Path.home() / ".sqlean_lint_session.json"

    def _save_session(self) -> None:
        payload = {
            "dialect": getattr(self._config, "dialect", "duckdb"),
            "theme": getattr(self._config, "theme", "dark"),
            "current_file": str(self._current_file)
            if self._current_file is not None else None,
            "open_paths": list(self._open_paths),
            "table_catalog": list(self._table_catalog),
            "column_catalog": list(self._column_catalog),
        }
        try:
            self._session_path().write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    def _restore_session(self) -> None:
        try:
            path = self._session_path()
            if not path.exists():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("dialect"):
                try:
                    self._set_dialect(payload["dialect"])
                except Exception:  # noqa: BLE001
                    pass
            if payload.get("open_paths"):
                for p in payload["open_paths"]:
                    if Path(p).is_file() and p not in self._open_paths:
                        self._open_paths.append(p)
        except Exception:  # noqa: BLE001
            pass

    def _build_commands(self) -> List[Command]:
        """Return the command-palette actions (VS Code Ctrl+Shift+P)."""
        return [
            # Scan / lint
            Command("Scan Current File", self._scan_current_file, hint="Ctrl+Enter",
                    family="Scan"),
            Command("Scan Directory", self._on_scan_directory, family="Scan"),
            Command("Auto-Fix Lint Errors", self._on_autofix, hint="Ctrl+R",
                    family="Edit"),
            Command("Format SQL Query", self._format_sql, family="Edit"),
            # Data / execution
            Command("Run DuckDB Query", self._run_query, hint="F5/Ctrl+Enter",
                    family="Data"),
            Command("Run Statement Under Cursor", self._run_statement_under_cursor,
                    hint="Ctrl+Enter", family="Data"),
            Command("Toggle Data Grid Panel", self._toggle_data_grid,
                    family="Data"),
            Command("Toggle Chart View", self._toggle_chart_view,
                    hint="Toolbar", family="Data"),
            Command("Query Parameters", self._toggle_parameter_drawer,
                    hint="Ctrl+P", family="Data"),
            Command("Undo Snapshot", self._undo_edit, hint="Ctrl+Z", family="Edit"),
            # File / tabs
            Command("New SQL Tab", self._new_tab, hint="Ctrl+T", family="File"),
            Command("Close Current Tab", self._close_active_tab, hint="Ctrl+W",
                    family="File"),
            Command("Open File", self._on_browse, hint="Ctrl+O", family="File"),
            Command("Save File", self._on_save_file, hint="Ctrl+S", family="File"),
            Command("Clear Output Console", self._clear_output_console,
                    family="File"),
            Command("Clear Workspace", self._on_clear, family="File"),
            # Lineage / view
            Command("Toggle Lineage DAG Viewer", self._toggle_dag_viewer,
                    family="View"),
            Command("Keyboard Shortcuts", self._open_shortcuts_modal, hint="Ctrl+H",
                    family="View"),
            Command("Open Command Palette", self._open_command_palette,
                    hint="Ctrl+Shift+P", family="View"),
            # Config
            Command("Open Settings / Rule Config", self._on_open_settings,
                    family="Config"),
            Command("Change Dialect to DuckDB", lambda: self._set_dialect("duckdb"),
                    family="Config"),
            Command("Change Dialect to Snowflake",
                    lambda: self._set_dialect("snowflake"), family="Config"),
            Command("Change Dialect to Postgres",
                    lambda: self._set_dialect("postgres"), family="Config"),
            # Report
            Command("Export Executive PDF", lambda: self._export_report("pdf"),
                    family="Report"),
            Command("Export Executive Excel (XLSX)",
                    lambda: self._export_report("xlsx"), family="Report"),
            Command("Export Report (PDF + XLSX)", self._export_report,
                    family="Report"),
        ]

    def _set_dialect(self, dialect: str) -> None:
        """Switch the active lint dialect from the command palette."""
        try:
            from .config import LinterConfig

            updated = LinterConfig(
                dialect=dialect,
                theme=self._config.theme,
                excludes=self._config.excludes,
                enabled_rules=self._config.enabled_rules,
            )
        except Exception:  # noqa: BLE001
            updated = None
        if updated is not None:
            self.apply_config(updated)
        self._set_status(f"Dialect -> {dialect}", idle=True)
        if hasattr(self, "_toasts"):
            self._notify(f"Dialect set to {dialect}", kind="info")

    def _cycle_palette_theme(self) -> None:
        """Cycle the active theme preset (Vercel → Nord → Oceanic → Mono → …)."""
        order = ["vercel_dark", "nord_slate", "material_oceanic", "high_contrast"]
        try:
            current = getattr(self, "_config", None)
            cur = getattr(current, "theme", "dark") or "dark"
            idx = 0
            for i, n in enumerate(order):
                if n in str(cur):
                    idx = i
                    break
            nxt = order[(idx + 1) % len(order)]
            from .config import LinterConfig

            updated = LinterConfig(
                dialect=self._config.dialect,
                theme=nxt,
                excludes=self._config.excludes,
                enabled_rules=self._config.enabled_rules,
            )
            self.apply_config(updated)
            top = self.winfo_toplevel()
            try:
                from .ui.design_system import TOKENS, ThemeManager

                tm = ThemeManager(variant=nxt)
                tm.apply_theme(top)
            except Exception:  # noqa: BLE001
                pass
            self._notify(f"Theme: {nxt}", kind="info")
        except Exception:  # noqa: BLE001
            pass

    def _clear_cache(self) -> None:
        """Best-effort clear of workspace caches."""
        self._history_ledger.clear()
        try:
            self._data_grid.clear()
        except Exception:  # noqa: BLE001
            pass
        self._notify("Cache cleared", kind="info")

    def _apply_palette_theme(self, preset: str) -> None:
        """Apply a specific theme preset from the Spotlight palette."""
        try:
            from .config import LinterConfig

            updated = LinterConfig(
                dialect=self._config.dialect,
                theme=preset,
                excludes=self._config.excludes,
                enabled_rules=self._config.enabled_rules,
            )
            self.apply_config(updated)
            top = self.winfo_toplevel()
            try:
                from .ui.design_system import TOKENS, ThemeManager

                tm = ThemeManager(variant=preset)
                tm.apply_theme(top)
            except Exception:  # noqa: BLE001
                pass
            self._notify(f"Theme: {preset}", kind="info")
        except Exception:  # noqa: BLE001
            pass

    def _open_sql_file_dialog(self) -> None:
        """Open an SQL file via a native file dialog."""
        try:
            from tkinter import filedialog

            path = filedialog.askopenfilename(
                title="Open SQL File",
                filetypes=[("SQL files", "*.sql"), ("All files", "*.*")],
            )
            if path:
                self._load_file_preview(Path(path))
                self._notify(f"Opened {Path(path).name}", kind="info")
        except Exception:  # noqa: BLE001
            pass

    def _toggle_nav_rail(self) -> None:
        """Toggle the left navigation rail."""

        if hasattr(self, "_workspace") and self._workspace is not None:
            try:
                self._workspace.toggle_left_rail()
                return
            except Exception:  # noqa: BLE001
                pass
        self._notify("Navigation rail toggle unavailable", kind="warning")

    def _notify(self, text: str, kind: str = "info") -> None:
        """Push a non-blocking toast pinned to the bottom-right corner."""
        if hasattr(self, "_toasts") and self._toasts is not None:
            self._toasts.notify(text, kind=kind)

    def _begin_loading(self) -> None:
        """Start a pulsing skeleton loader behind a background task."""
        if self._skeleton is None:
            return
        try:
            self._skeleton.place(relx=0.98, rely=0.98, anchor="se",
                                 x=-16, y=-14)
            self._skeleton.lift()
            self._skeleton.start()
        except Exception:  # noqa: BLE001
            pass

    def _end_loading(self) -> None:
        if self._skeleton is not None:
            try:
                self._skeleton.stop()
                self._skeleton.place_forget()
            except Exception:  # noqa: BLE001
                pass

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, height=26,
                           border_width=_BORDER_WIDTH, border_color=_BORDER_COLOR)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        bar.grid_columnconfigure(4, weight=1)

        # Shift everything up by one column to make room for the cursor
        # coordinates + latency on the far right.
        self._trunc_label = ctk.CTkLabel(
            bar,
            text="",
            font=ctk.CTkFont("Consolas", 10, "bold"),
            text_color=COLOR_AMBER,
            fg_color=COLOR_ZONE,
            corner_radius=4,
            padx=6,
        )
        self._trunc_label.grid(row=0, column=6, padx=(10, 4), pady=3, sticky="e")
        self._trunc_label.grid_remove()

        self._status_text = ctk.CTkLabel(
            bar,
            text="Idle",
            font=ctk.CTkFont("Consolas", 10, "bold"),
            text_color=COLOR_ACCENT,
        )
        self._status_text.grid(row=0, column=0, padx=(10, 0), pady=3, sticky="w")

        self._dialect_status = ctk.CTkLabel(
            bar,
            text=f"Dialect: {self._config.dialect}",
            font=ctk.CTkFont("Consolas", 10, "bold"),
            text_color=COLOR_ACCENT,
        )
        self._dialect_status.grid(row=0, column=1, padx=(10, 0), pady=3, sticky="w")

        self._file_status = ctk.CTkLabel(
            bar,
            text="Identity: Ahmad Bilal",
            font=ctk.CTkFont("Consolas", 10),
            text_color=COLOR_MUTED,
        )
        self._file_status.grid(row=0, column=2, padx=(10, 0), pady=3, sticky="w")

        # ── Live metrics (right-aligned) ────────────────────────────────
        # Query latency + cursor coordinates (updated on editor caret move).
        self._cursor_label = ctk.CTkLabel(
            bar,
            text="Ln 1, Col 1",
            font=ctk.CTkFont("Consolas", 10),
            text_color=COLOR_MUTED,
        )
        self._cursor_label.grid(row=0, column=7, padx=(8, 4), pady=3, sticky="e")

        self._latency_label = ctk.CTkLabel(
            bar,
            text="Latency: --",
            font=ctk.CTkFont("Consolas", 10),
            text_color=COLOR_MUTED,
        )
        self._latency_label.grid(row=0, column=8, padx=(8, 4), pady=3, sticky="e")

        self._count_label = ctk.CTkLabel(
            bar,
            text="Chars: 0   Words: 0",
            font=ctk.CTkFont("Consolas", 10),
            text_color=COLOR_MUTED,
        )
        self._count_label.grid(row=0, column=9, padx=(8, 4), pady=3, sticky="e")

        # Green "Privacy: Zero-Cloud Active" data-usage badge.
        self._privacy_badge = ctk.CTkLabel(
            bar,
            text="Privacy: Zero-Cloud Active",
            font=ctk.CTkFont("Consolas", 10, "bold"),
            text_color=TOKENS["TEXT_INVERSE"],
            fg_color=COLOR_OK,
            corner_radius=SPACING["RADIUS_SM"],
            padx=SPACING["BANNER_PAD_X"],
            pady=SPACING["BANNER_PAD_Y"],
        )
        self._privacy_badge.grid(row=0, column=10, padx=(8, 10), pady=3, sticky="e")

    def _set_status(self, status: str, idle: bool = True) -> None:
        self._status_text.configure(
            text=status,
            text_color=COLOR_MUTED if idle else COLOR_ACCENT,
        )

    def _bind_shortcuts(self) -> None:
        # Bind at the window (Tk) level: customtkinter's CTkFrame rejects
        # ``bind_all``, but the underlying Tk/CTk root accepts it.
        top = self.winfo_toplevel()
        top.bind_all("<Control-o>", lambda e: self._on_browse())
        top.bind_all("<Control-O>", lambda e: self._on_browse())
        # The spec mandates F5 / Ctrl+Enter to run the active query through
        # the local DuckDB engine (scan remains available via the command
        # palette and the toolbar "Scan Directory" button).
        top.bind_all("<F5>", lambda e: self._run_query())
        # Ctrl+Enter runs only the statement currently under the cursor;
        # F5 runs the whole active buffer.
        top.bind_all("<Control-Return>", lambda e: self._run_statement_under_cursor())
        top.bind_all("<Control-r>", lambda e: self._on_autofix())
        top.bind_all("<Control-R>", lambda e: self._on_autofix())
        top.bind_all("<Control-h>", lambda e: self._open_shortcuts_modal())
        top.bind_all("<Control-H>", lambda e: self._open_shortcuts_modal())
        # Enterprise workspace shortcuts.
        top.bind_all("<Control-Shift-P>", lambda e: self._open_command_palette())
        top.bind_all("<Control-k>", lambda e: self._toggle_command_palette())
        top.bind_all("<Control-K>", lambda e: self._toggle_command_palette())
        top.bind_all("<Control-t>", lambda e: self._new_tab())
        top.bind_all("<Control-T>", lambda e: self._new_tab())
        top.bind_all("<Control-w>", lambda e: self._close_active_tab())
        top.bind_all("<Control-W>", lambda e: self._close_active_tab())
        top.bind_all("<Control-s>", lambda e: self._on_save_file())
        top.bind_all("<Control-S>", lambda e: self._on_save_file())
        # Tabbed document interface shortcuts: Ctrl+Tab / Ctrl+Shift+Tab.
        top.bind_all("<Control-Tab>", lambda e: self._cycle_tab(1))
        top.bind_all("<Control-ISO_Left_Tab>", lambda e: self._cycle_tab(-1))
        # Phase 3/4: undo snapshot + injectable query parameters.
        top.bind_all("<Control-z>", lambda e: self._undo_edit())
        top.bind_all("<Control-Z>", lambda e: self._undo_edit())
        top.bind_all("<Control-p>", lambda e: self._toggle_parameter_drawer())
        top.bind_all("<Control-P>", lambda e: self._toggle_parameter_drawer())
        # Bottom dock collapse/expand (docs + results tray) on Ctrl+J.
        top.bind_all("<Control-j>", lambda e: self._toggle_dock())
        top.bind_all("<Control-J>", lambda e: self._toggle_dock())
        # IntelliSense manual trigger (Ctrl+Space) is bound to the editor
        # pane itself in the IntelliSense provider.

        # Phase 4: persist the session when the window is dismissed.
        try:
            top.protocol("WM_DELETE_WINDOW", self._on_quit)
        except Exception:  # noqa: BLE001
            pass

    def _cycle_tab(self, direction: int) -> None:
        """Switch to the next (or previous) tab document."""
        paths = self._tabs.open_paths()
        if not paths:
            return
        active = self._tabs.active()
        try:
            idx = paths.index(active) if active in paths else 0
        except ValueError:  # noqa: BLE001
            idx = 0
        target = paths[(idx + direction) % len(paths)]
        self._tabs.select(target)

    # ── command palette & multi-tab workspace handlers ────────────────

    def _open_command_palette(self) -> None:
        if self._palette is not None:
            self._palette.show()

    def _toggle_command_palette(self) -> None:
        if self._palette is not None:
            self._palette.toggle()

    def _new_tab(self) -> None:
        # Double-click empty TDI area creates a new blank query buffer.
        self._current_file = None
        self._current_sql = ""
        self._diff_viewer.render("", "")
        self._minimap.refresh()
        self._update_counts()
        if hasattr(self, "_tabs"):
            self._tabs.close_all()
        if hasattr(self, "_workspace"):
            try:
                self._workspace.open_tab("/scratch/untitled.sql")
            except Exception:  # noqa: BLE001
                pass
        self._set_status("New query buffer", idle=True)

    def _close_active_tab(self) -> None:
        active = self._tabs.active()
        if active:
            self._tabs.close(active)

    def _on_tab_selected(self, path: str) -> None:
        self._set_status(f"Active: {Path(path).name}", idle=True)

    def _on_tab_changed(self, path: str) -> None:
        # Bugfix: drop re-entrant tab-change events while a preview is already
        # opening a tab, otherwise _load_file_preview ⇄ _on_tab_changed loops
        # forever (each side re-triggers the other).
        if self._switching_tabs:
            return
        if not path:
            return
        try:
            candidate = Path(path)
            if candidate.is_file():
                self._load_file_preview(candidate)
        except Exception:  # noqa: BLE001
            pass

    def _on_tab_closed(self, path: str) -> None:
        try:
            if str(path) in self._open_paths:
                self._open_paths.remove(path)
        except (ValueError, AttributeError):  # noqa: BLE001
            pass
        self._set_status("Tab closed", idle=True)

    # ── live query execution + executive export ────────────────────────

    def _run_query(self) -> None:
        """Execute the active SQL via DuckDB and render the data grid.

        The local DuckDB execution is offloaded to a daemon thread so the UI
        stays responsive (Phase 4 thread isolation); the (pure) result rows
        are marshalled back to the Tk main thread via ``after`` for rendering.
        """
        if getattr(self, "_query_running", False):
            self._set_status("Already running a query", idle=True)
            return
        sql = self._current_sql
        if not sql.strip() and self._diff_viewer is not None:
            try:
                sql = self._diff_viewer.get_optimized_pane().get(
                    "1.0", "end-1c"
                )
            except Exception:  # noqa: BLE001
                sql = ""
        if not sql.strip():
            self._set_status("No query to run", idle=True)
            return
        # Phase 2: inject :name parameter values (if any were supplied).
        sql = self._resolve_params(sql)
        self._set_status("Running local query...", idle=False)
        self._begin_loading()
        self._query_running = True

        def _execute() -> None:
            # Runs on a daemon thread: capture result data only, never touch
            # Tk widgets from here.
            error: Optional[str] = None
            rows: List[dict] = []
            types: List[str] = []
            elapsed_ms: int = 0
            try:
                if self._query_runner is not None:
                    try:
                        rows = self._query_runner.run(sql)
                        types = list(self._query_runner.last_types)
                        elapsed_ms = int(self._query_runner.last_elapsed_ms)
                        error = self._query_runner.last_error
                    except Exception as exc:  # noqa: BLE001 - query degrades safely
                        error = str(exc)
            finally:
                # Guarantee the running flag is cleared on the main thread
                # even if the worker died mid-run.
                self.after(0, lambda: setattr(self, "_query_running", False))
            self.after(0, lambda: self._apply_query_result(
                rows, types, elapsed_ms, error
            ))

        threading.Thread(target=_execute, daemon=True).start()

    def _apply_query_result(
        self,
        rows: List[dict],
        types: List[str],
        elapsed_ms: int,
        error: Optional[str],
    ) -> None:
        """Render collected query results on the Tk main thread."""
        self._query_running = False
        self._end_loading()
        if error:
            self._set_status(f"Query error - {error}", idle=True)
            self._data_grid.clear()
            self._notify(f"Query error - {error}", kind="error")
            self._log_console(f"Query error: {error}", kind="ERR")
            return
        # Phase 4: surface query latency in the status footer.
        if hasattr(self, "_latency_label"):
            try:
                self._latency_label.configure(text=f"Latency: {elapsed_ms} ms")
            except Exception:  # noqa: BLE001
                pass
        if rows:
            columns = list(rows[0].keys())
            self._last_grid_columns = columns
            self._last_grid_rows = rows
            self._feed_chart(columns, rows)
            # Feed the RAW records straight into the virtualized grid, which
            # only materialises the visible viewport (no pre-capping needed).
            self._data_grid.set_dataset(
                columns, rows, types=types, execution_time_ms=elapsed_ms
            )
            self._profiler.update_rows(rows)
            self._set_status(
                f"{len(rows):,} rows loaded in {elapsed_ms} ms", idle=True
            )
            self._notify(f"Query OK - {len(rows)} rows in {elapsed_ms} ms",
                         kind="success")
            self._log_console(
                f"Query OK - {len(rows)} rows in {elapsed_ms} ms", kind="OK"
            )
        else:
            self._data_grid.clear()
            self._set_status(f"Query returned 0 rows in {elapsed_ms} ms",
                             idle=True)
            self._notify("Query returned 0 rows", kind="warning")
            self._log_console(
                f"Query returned 0 rows in {elapsed_ms} ms", kind="WARN"
            )

    def _query_nodes(self) -> List[dict]:
        """Return the DAG nodes for the active SQL (used by the exporter)."""
        try:
            return [n.to_dict() for n in _dag_parse(
                self._current_sql, self._config.dialect)]
        except Exception:  # noqa: BLE001
            return []

    def _export_report(self, fmt: Optional[str] = None) -> None:
        """Executive export (PDF / XLSX / both) of health/DAG/grid.

        ``fmt`` may be ``"pdf"`` or ``"xlsx"`` to force a single format (used
        by the command palette); when ``None`` the user picks via the dialog.
        """
        from tkinter import filedialog, messagebox

        default = "sqlean_lint_executive_report"
        # The save dialog shows the relevant filters for the requested format.
        if fmt == "pdf":
            filetypes = [("PDF report", "*.pdf")]
        elif fmt == "xlsx":
            filetypes = [("Excel workbook", "*.xlsx")]
            default = "sqlean_lint_executive_report.xlsx"
        else:
            filetypes = [
                ("PDF report", "*.pdf"),
                ("Excel workbook", "*.xlsx"),
                ("Both (PDF + XLSX)", "*.pdf"),
            ]
        ext = ".xlsx" if fmt == "xlsx" else ".pdf"
        target = filedialog.asksaveasfilename(
            title="Export Executive Report",
            defaultextension=ext,
            initialfile=default,
            filetypes=filetypes,
        )
        if not target:
            return
        base = Path(target)
        score = int(self._health_score.score())
        nodes = self._query_nodes()
        details = {
            "findings": len(self._results),
            "rows_previewed": self._data_grid.row_count(),
            "dialect": self._config.dialect,
            "prepared_by": "Ahmad Bilal (AhmadBilalDSA)",
            "scan_timestamp": _now_stamp(),
        }
        try:
            formats = (fmt,) if fmt in ("pdf", "xlsx") else ("pdf", "xlsx")
            written = export_executive_report(
                base,
                score=score,
                letter=self._health_score.letter() if hasattr(
                    self._health_score, "letter") else "",
                dialect=self._config.dialect,
                nodes=nodes,
                columns=self._last_grid_columns,
                rows=self._last_grid_rows,
                health_details=details,
                formats=formats,
            )
            messagebox.showinfo(
                "Export Complete",
                "Report written to:\n" + "\n".join(str(w) for w in written),
            )
        except Exception as exc:  # noqa: BLE001 - report export is best-effort
            messagebox.showerror("Export Failed", str(exc))

    def _scan_current_file(self) -> None:
        """Lint only the currently opened file (command-palette action)."""
        if self._current_file is None:
            if self._current_sql:
                self._start_scan([Path("/dev/stdin")])  # falls back to preview
            self._set_status("No file open - use Browse/Scan Directory",
                             idle=True)
            return
        self._on_clear()
        self._add_paths([self._current_file])
        self._set_status(f"Scanning {Path(self._current_file).name}",
                         idle=False)

    def _format_sql(self) -> None:
        """Pretty-print the active SQL through sqlglot (command-palette)."""
        sql = self._current_sql
        if not sql.strip():
            self._set_status("Nothing to format", idle=True)
            return
        try:
            ast = sqlglot.parse_one(sql, read=self._config.dialect)
            formatted = ast.sql(dialect=self._config.dialect, pretty=True)
        except Exception as exc:  # noqa: BLE001 - formatting is best-effort
            self._set_status(f"Could not format: {exc}", idle=True)
            return
        self._current_sql = formatted
        optimized = self._optimize_for_preview(formatted)
        self._diff_viewer.render(formatted, optimized)
        self._minimap.refresh()
        self._update_counts()
        self._set_status("Query formatted", idle=True)

    def _on_save_file(self) -> None:
        """Persist the active SQL back to its file (or browse for a target)."""
        from tkinter import filedialog, messagebox

        content = self._current_sql
        if not content and self._diff_viewer is not None:
            try:
                content = self._diff_viewer.get_original_pane().get(
                    "1.0", "end-1c"
                )
            except Exception:  # noqa: BLE001
                content = self._current_sql
        if self._current_file is not None:
            target = self._current_file
        else:
            target = filedialog.asksaveasfilename(
                title="Save SQL file",
                defaultextension=".sql",
                filetypes=[("SQL files", "*.sql"), ("All files", "*.*")],
            )
            if not target:
                return
            target = Path(target)
        try:
            target.write_text(content + ("\n" if content and not
                                        content.endswith("\n") else ""),
                              encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            messagebox.showerror("Save Failed", str(exc))
            return
        self._current_file = target
        self._tabs.open(str(target))
        self._set_status(f"Saved {target.name}", idle=True)

    def _clear_output_console(self) -> None:
        """Clear the lint/results console (command-palette action)."""
        try:
            self._results_text.configure(state="normal")
            self._results_text.delete("1.0", "end")
            self._results_text.configure(state="disabled")
        except Exception:  # noqa: BLE001
            pass
        self._set_status("Output console cleared", idle=True)

    def _toggle_data_grid(self) -> None:
        """Show/hide the query-results data grid panel."""
        self._grid_visible = not self._grid_visible
        if hasattr(self, "_data_grid"):
            try:
                if self._grid_visible:
                    self._data_grid.grid()
                else:
                    self._data_grid.grid_remove()
            except Exception:  # noqa: BLE001
                pass
        self._set_status(
            f"Data grid {'shown' if self._grid_visible else 'hidden'}",
            idle=True,
        )

    def _toggle_dag_viewer(self) -> None:
        """Show/hide the Lineage DAG panel."""
        self._dag_visible = not self._dag_visible
        if hasattr(self, "_dag_viewer"):
            try:
                if self._dag_visible:
                    self._dag_viewer.master.grid()
                else:
                    self._dag_viewer.master.grid_remove()
            except Exception:  # noqa: BLE001
                pass
        self._set_status(
            f"Lineage DAG {'shown' if self._dag_visible else 'hidden'}",
            idle=True,
        )

    def _open_shortcuts_modal(self) -> None:
        """Show a sleek help modal documenting the keyboard shortcuts."""
        modal = ctk.CTkToplevel(self)
        modal.title("Keyboard Shortcuts — SQLean Lint Pro")
        modal.geometry("460x340")
        modal.resizable(False, False)
        modal.lift()
        modal.attributes("-topmost", True)
        modal.grab_set()

        container = ctk.CTkFrame(modal, fg_color=COLOR_BG, corner_radius=0,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            container,
            text="Keyboard Shortcuts",
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
            text_color=COLOR_ACCENT,
        ).pack(anchor="w", pady=(0, 12))

        rows = (
            ("Ctrl + O", "Browse / select SQL files"),
            ("Ctrl + Enter", "Scan a directory"),
            ("Ctrl + R", "Auto-Fix All"),
            ("Ctrl + H", "Toggle this shortcuts help"),
        )
        for key, action in rows:
            line = ctk.CTkFrame(container, fg_color="transparent")
            line.pack(fill="x", pady=4)
            ctk.CTkLabel(
                line,
                text=key,
                font=ctk.CTkFont("Consolas", 13, "bold"),
                text_color=COLOR_OK,
                width=140,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                line,
                text=action,
                font=ctk.CTkFont("Segoe UI", 13),
                anchor="w",
            ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            container,
            text="Created by Ahmad Bilal (AhmadBilalDSA) — kierninja@gmail.com",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", pady=(14, 0))

        ctk.CTkButton(
            container,
            text="Close",
            width=120,
            height=32,
            fg_color=COLOR_ACCENT,
            command=modal.destroy,
        ).pack(pady=(10, 0), anchor="e")

    def _on_open_about(self) -> None:
        """Modal with software info and author attribution."""
        modal = self._new_modal("About SQLean Lint Pro", "480x360")
        container = ctk.CTkFrame(modal, fg_color=COLOR_BG, corner_radius=0,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
        container.pack(fill="both", expand=True, padx=24, pady=20)

        ctk.CTkLabel(
            container,
            text="SQLean Lint Pro",
            font=ctk.CTkFont("Segoe UI", 24, "bold"),
            text_color=COLOR_ACCENT,
        ).pack(anchor="w", pady=(0, 2))

        ctk.CTkLabel(
            container,
            text="Enterprise SQL Performance Linter & AST Auto-Optimizer",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", pady=(0, 16))

        info = (
            ("Version", "1.0.0"),
            ("Engine", "SQLGlot AST"),
            ("License", "MIT"),
            ("Hardware Binding", "Active"),
            ("Privacy", "Zero-Cloud (air-gapped)"),
        )
        for label, value in info:
            line = ctk.CTkFrame(container, fg_color="transparent")
            line.pack(fill="x", pady=3)
            ctk.CTkLabel(
                line,
                text=label,
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                text_color=COLOR_TEXT,
                width=150,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                line,
                text=value,
                font=ctk.CTkFont("Consolas", 12),
                text_color=COLOR_MUTED,
                anchor="w",
            ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            container,
            text="\nCreated by Ahmad Bilal (AhmadBilalDSA)\n"
                 "kierninja@gmail.com\n"
                 "https://www.linkedin.com/in/ahmadbilaldes",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=COLOR_OK,
            justify="left",
        ).pack(anchor="w", pady=(14, 0))

        ctk.CTkButton(
            container,
            text="Close",
            width=120,
            height=32,
            fg_color=COLOR_ACCENT,
            command=modal.destroy,
        ).pack(pady=(16, 0), anchor="e")

    def _on_open_documentation(self) -> None:
        """Modal summarizing the built-in documentation and attribution."""
        modal = self._new_modal("Documentation — SQLean Lint Pro", "560x420")
        container = ctk.CTkFrame(modal, fg_color=COLOR_BG, corner_radius=0,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
        container.pack(fill="both", expand=True, padx=24, pady=20)

        ctk.CTkLabel(
            container,
            text="Documentation",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=COLOR_ACCENT,
        ).pack(anchor="w", pady=(0, 12))

        docs = (
            ("Keyboard Shortcuts", "Ctrl+O browse · Ctrl+Enter scan · Ctrl+R auto-fix · Ctrl+H help"),
            ("Linting", "Real-time AST linting with SQL-WILD-001, SQL-CART-001, SQL-SARG-001"),
            ("Auto-Fix", "One-click rewrites with provably-safe semantic preservation"),
            ("Licensing", "Hardware-bound RSA license + 30-day offline lease"),
            ("Privacy", "100% local — zero cloud telemetry, no phoning home"),
            ("Build", "Nuitka single-file Windows executable (build_exe.py)"),
        )
        for title, body in docs:
            line = ctk.CTkFrame(container, fg_color=COLOR_SURFACE, corner_radius=8)
            line.pack(fill="x", pady=4)
            ctk.CTkLabel(
                line,
                text=title,
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                text_color=COLOR_OK,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(
                line,
                text=body,
                font=ctk.CTkFont("Segoe UI", 11),
                text_color=COLOR_TEXT,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(2, 8))

        ctk.CTkLabel(
            container,
            text="Documentation © Ahmad Bilal (AhmadBilalDSA) — kierninja@gmail.com",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", pady=(8, 0))

        ctk.CTkButton(
            container,
            text="Close",
            width=120,
            height=32,
            fg_color=COLOR_ACCENT,
            command=modal.destroy,
        ).pack(pady=(12, 0), anchor="e")

    @staticmethod
    def _new_modal(title: str, geometry: str) -> ctk.CTkToplevel:
        modal = ctk.CTkToplevel()
        modal.title(title)
        modal.geometry(geometry)
        modal.resizable(False, False)
        modal.lift()
        modal.attributes("-topmost", True)
        modal.grab_set()
        return modal

    def _on_toggle_assistant(self) -> None:
        on_assistant = getattr(self, "_on_open_assistant", None)
        if on_assistant is not None:
            on_assistant()

    def _on_run_scan(self) -> None:
        self._on_scan_directory()

    # ── file intake ────────────────────────────────────────────────────

    def _on_drop(self, event) -> None:
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        self._add_paths(paths)

    def _on_browse(self) -> None:
        from tkinter import filedialog

        self.lift()
        self.focus_force()
        selected = filedialog.askopenfilenames(
            parent=self,
            title="Select SQL files",
            filetypes=[("SQL files", "*.sql"), ("All files", "*.*")],
        )
        try:
            self._add_paths([Path(p) for p in selected])
        except Exception as e:
            self._set_status(f"Error loading files: {e}", idle=True)

    def _on_scan_directory(self) -> None:
        from tkinter import filedialog

        self.lift()
        self.focus_force()
        directory = filedialog.askdirectory(
            parent=self, title="Select directory to scan"
        )
        if directory:
            try:
                files = _collect_sql_files([Path(directory)], self._config.excludes)
                self._add_paths(files)
            except Exception as e:
                self._set_status(f"Error loading directory: {e}", idle=True)

    def _add_paths(self, paths: List[Path]) -> None:
        files = _collect_sql_files(paths, self._config.excludes)
        self._file_listbox.configure(state="normal")
        for f in files:
            self._file_listbox.insert("end", str(f) + "\n")
        self._file_listbox.configure(state="disabled")
        # Render into the interactive explorer.
        if files:
            self._explorer.set_directory(
                Path(files[0]).parent, [p.name for p in files]
            )
        for f in files:
            self._explorer.add_recent(f)
        if files:
            self._load_file_preview(files[0])
            self._start_scan(files)

    def _on_clear(self) -> None:
        self._file_listbox.configure(state="normal")
        self._file_listbox.delete("1.0", "end")
        self._file_listbox.configure(state="disabled")
        self._explorer.clear_all()
        self._results = []
        self._current_file = None
        self._current_sql = ""
        self._open_paths = []
        self._table_catalog = []
        self._column_catalog = []
        try:
            self._tabs.close_all()
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "_workspace"):
            try:
                self._workspace.close_all_tabs()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._data_grid.clear()
        except Exception:  # noqa: BLE001
            pass
        self._update_counts()
        self._summary_label.configure(text="Ready", text_color=COLOR_MUTED)
        self._autofix_btn.configure(state="disabled")

    # ── scanning ───────────────────────────────────────────────────────

    def _start_scan(self, files: List[Path]) -> None:
        self._results = []
        self._summary_label.configure(text=f"Scanning {len(files)} file(s)...",
                                      text_color=COLOR_ACCENT)
        self._set_status("Scanning", idle=False)
        self._autofix_btn.configure(state="disabled")
        disabled = {
            rid: self._config.is_rule_enabled(rid)
            for rid in (r.rule_id for r in DEFAULT_RULES)
        }
        worker = ScanWorker(files, self._config.dialect, disabled, self._queue)
        self._worker = threading.Thread(target=worker.run, daemon=True)
        self._worker.start()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, path, extra, payload = self._queue.get_nowait()
                if kind == "result":
                    for row in payload:
                        self._results.append((path, row))
                    self._render_results()
                elif kind == "file_error":
                    message = extra
                    self._results.append((path, ("PARSE-ERR", message, "ERROR", 1, 1, False)))
                    self._render_results()
                elif kind == "done":
                    self._summary_label.configure(
                        text=f"Done — {len(self._results)} finding(s)",
                        text_color=COLOR_WARN if self._results else COLOR_OK,
                    )
                    self._set_status(
                        f"Done ({len(self._results)} finding(s))", idle=True
                    )
                    if self._results:
                        self._autofix_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _render_results(self) -> None:
        self._results_text.configure(state="normal")
        self._results_text.delete("1.0", "end")
        final_issues = self._results  # capture snapshot before mutation below
        # Surface findings into the interactive explorer.
        self._explorer.clear_section("Violations Found")
        severity_weights = {"CRITICAL": 25, "HIGH": 18, "MEDIUM": 9, "ERROR": 20}
        penalty = 0
        for path, row in final_issues:
            if row[0] == "PARSE-ERR":
                line_no, message = 1, row[1]
                self._results_text.insert("end", f"[ERROR] {Path(path).name}:{line_no} {message}\n")
                self._explorer.add_violation(path, "PARSE-ERR", "ERROR")
                penalty += severity_weights.get("ERROR", 20)
                continue
            rule_id, message, severity, line, col, fixable = row
            tag = self._severity_tag(severity)
            self._results_text.insert(
                "end",
                f"[{severity}] {Path(path).name}:{line}:{col}  {rule_id}  {message}\n",
                tag,
            )
            self._explorer.add_violation(path, rule_id, severity)
            penalty += severity_weights.get(severity.upper(), 0)
        self._results_text.configure(state="disabled")
        self._update_health_score(penalty)

    @staticmethod
    def _severity_tag(severity: str) -> str:
        return severity.upper()

    # ── enterprise UI helpers ──────────────────────────────────────────

    def _load_file_preview(self, path: Path) -> None:
        """Load a SQL file into the split-pane diff viewer + live counters.

        Large files are capped to ``MAX_PREVIEW_BYTES`` so the split-pane
        render stays well under 100 ms, and the heavy AST/DAG computation is
        deferred to a daemon thread so the Tkinter event loop never freezes.
        """
        MAX_PREVIEW_BYTES = 120_000  # ~120 KB
        try:
            file_size = os.path.getsize(path)
        except OSError:
            return
        is_truncated = file_size > MAX_PREVIEW_BYTES
        try:
            if is_truncated:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    sql = fh.read(MAX_PREVIEW_BYTES)
            else:
                sql = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self._current_file = path

        # Auto-detect dialect from the content.
        detected = detect_dialect(sql)
        self._config.dialect = detected
        if hasattr(self, "_dialect_badge") and self._dialect_badge is not None:
            try:
                badge_text = f"Dialect: {detected}"
                if is_truncated:
                    orig_kb = file_size / 1024
                    badge_text += " (Truncated Preview)"
                self._dialect_badge.configure(text=badge_text)
            except Exception:  # noqa: BLE001
                pass

        # Phase 4: resilient truncation + file path status-footer badges.
        if hasattr(self, "_trunc_label"):
            try:
                if is_truncated:
                    orig_kb = file_size / 1024
                    self._trunc_label.configure(
                        text=f"Truncated: {orig_kb:.0f} KB",
                        fg_color=COLOR_AMBER,
                    )
                    self._trunc_label.grid()
                else:
                    self._trunc_label.grid_remove()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(self, "_file_status"):
            try:
                self._file_status.configure(text=f"File: {path.name}")
            except Exception:  # noqa: BLE001
                pass

        if is_truncated:
            orig_kb = file_size / 1024
            preview_sql = (
                sql
                + f"\n\n-- [PREVIEW MODE: Displaying first 120 KB of {orig_kb:.0f} KB."
                   " Full file will be analyzed in background.]"
            )
        else:
            preview_sql = sql
        self._current_sql = preview_sql

        # Render immediately (cheap) so the UI responds in under 100 ms.
        self._diff_viewer.render(preview_sql, preview_sql)
        # Bugfix: raise the re-entrancy guard around _tabs.open() so the
        # on_tab_changed callback it fires cannot loop back into here.
        self._switching_tabs = True
        try:
            self._tabs.open(str(path))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._switching_tabs = False
        if hasattr(self, "_workspace"):
            try:
                self._workspace.open_tab(str(path))
            except Exception:  # noqa: BLE001
                pass
        if path not in self._open_paths:
            self._open_paths.append(str(path))

        self._update_counts()
        self._update_health_score(0)

        # Offload heavy AST + DAG computation off the main thread.
        threading.Thread(
            target=self._async_process_preview, args=(sql,), daemon=True
        ).start()

    def _async_process_preview(self, sql: str) -> None:
        """Compute optimized SQL + AST views on a daemon thread.

        Runs the potentially slow ``sqlglot`` parse/optimize and workspace
        refresh off the UI thread, then pushes each UI update back onto the
        main thread via ``self.after(0, ...)``.
        """
        try:
            optimized = self._optimize_for_preview(sql)
        except Exception as err:  # noqa: BLE001 - preview is non-critical
            print(f'[WARN] Preview optimization skipped: {err}')
            optimized = sql
        self.after(0, lambda: self._apply_optimized_preview(optimized))
        self.after(0, self._update_workspace_views)

    def _apply_optimized_preview(self, optimized: str) -> None:
        """Swap the pending optimized query into the diff viewer (main thread)."""
        try:
            current = self._current_sql or ""
            self._diff_viewer.render(current, optimized)
        except Exception:  # noqa: BLE001
            pass

    def _update_workspace_views(self) -> None:
        """Refresh DAG, profiler, IntelliSense catalog and minimap."""
        if not self._current_sql.strip():
            return
        # KNIME execution-plan DAG highlights cartesian/join problems.
        try:
            self._dag_viewer.render_sql(
                self._current_sql, self._config.dialect
            )
        except Exception:  # noqa: BLE001 - DAG is a best-effort view
            pass
        # KNIME column profile (stat placeholders when no rows yet).
        try:
            self._profiler.set_sql_source(
                self._current_sql, self._config.dialect
            )
        except Exception:  # noqa: BLE001
            pass
        # IntelliSense catalog of referenced tables/columns.
        try:
            self._register_catalog_from_sql()
        except Exception:
            pass
        # Minimap reflects the active editor content.
        try:
            self._minimap.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _on_ast_node_clicked(self, node) -> None:
        """Highlight the clicked AST node's SQL token range in the editor."""
        try:
            sql_range = getattr(node, "sql_range", None)
            if not sql_range:
                return
            start, end = sql_range
            editor = self._diff_viewer.get_original_pane()
            start_idx = self._char_to_text_index(self._current_sql, start)
            end_idx = self._char_to_text_index(self._current_sql, end)
            editor.tag_remove("sel", "1.0", "end")
            editor.tag_add("sel", start_idx, end_idx)
            editor.mark_set("insert", start_idx)
            editor.see(start_idx)
            title = getattr(node, "label", "node") or "node"
            self._set_status(f"AST: {title}", idle=True)
        except Exception:  # noqa: BLE001 - highlight is best-effort
            pass

    @staticmethod
    def _char_to_text_index(sql: str, char_pos: int) -> str:
        """Convert a character offset to a Tk ``line.column`` index."""
        char_pos = max(0, min(len(sql), char_pos))
        prefix = sql[:char_pos]
        line = prefix.count("\n") + 1
        last_nl = prefix.rfind("\n")
        col = (char_pos - last_nl) if last_nl != -1 else char_pos + 1
        return f"{line}.{max(0, col - 1)}"

    def _register_catalog_from_sql(self) -> None:
        """Feed referenced tables + columns into the IntelliSense provider."""
        if self._intellisense is None:
            return
        tables: List[str] = []
        columns: List[str] = []
        ast_nodes, _ = safe_parse_sql(self._current_sql, self._config.dialect)
        if not ast_nodes:
            return
        for node in ast_nodes[0].walk():
            if type(node).__name__ == "Table":
                name = getattr(node, "name", "")
                if name and name not in tables:
                    tables.append(name)
            elif type(node).__name__ == "Column":
                name = getattr(node, "name", "")
                if name and name not in columns:
                    columns.append(name)
        self._table_catalog = tables
        self._column_catalog = columns
        self._intellisense.set_tables(tables)
        self._intellisense.set_columns(columns)

    def _optimize_for_preview(self, sql: str) -> str:
        """Best-effort optimized rendering; safe engine equivalent."""
        ast_nodes, resolved_dialect = safe_parse_sql(sql, self._config.dialect)
        if ast_nodes:
            try:
                return ast_nodes[0].sql(dialect=resolved_dialect)
            except Exception:  # noqa: BLE001
                return sql
        return sql

    def _update_counts(self) -> None:
        chars = len(self._current_sql)
        words = len(self._current_sql.split()) if self._current_sql else 0
        self._count_label.configure(text=f"Chars: {chars}   Words: {words}")

    def _update_health_score(self, penalty: int) -> None:
        base = 100
        if self._current_sql:
            base -= min(50, penalty)
        self._health_score.set_score(max(0, base))

    def _on_explorer_select(self, section: str, path: str) -> None:
        candidate = Path(path)
        if candidate.is_file():
            self._load_file_preview(candidate)

    def _on_section_toggle(self, section: str, expanded: bool) -> None:
        self._set_status(f"{section} {'expanded' if expanded else 'collapsed'}",
                         idle=True)

    # ── auto-fix ───────────────────────────────────────────────────────

    def _on_autofix(self) -> None:
        from tkinter import messagebox

        paths = self._file_listbox.get("1.0", "end").splitlines()
        if not paths:
            messagebox.showwarning("Auto-Fix", "Nothing to fix.")
            return
        changed = self._apply_fixes([Path(p) for p in paths if p.strip()])
        messagebox.showinfo(
            "Auto-Fix All",
            f"Rewritten {changed} file(s) in place.\n\n"
            "Review the output before committing — always keep a backup.",
        )
        self._start_scan([Path(p) for p in paths if p.strip()])

    def _apply_fixes(self, files: List[Path]) -> int:
        changed = 0
        for path in files:
            try:
                sql = path.read_text(encoding="utf-8", errors="replace")
                ast = sqlglot.parse_one(sql, read=self._config.dialect)
            except Exception:  # noqa: BLE001 - skip unparseable files
                continue
            before = ast.sql(dialect=self._config.dialect)
            apply_fixes(ast, self._config.dialect, DEFAULT_RULES)
            after = ast.sql(dialect=self._config.dialect)
            if before != after:
                path.write_text(after + "\n", encoding="utf-8")
                changed += 1
        return changed

    def apply_config(self, config: LinterConfig) -> None:
        self._config = config
        ctk.set_appearance_mode(config.theme)
        self._dialect_badge.configure(text=f"Dialect: {config.dialect}")
        if hasattr(self, "_dialect_status"):
            self._dialect_status.configure(text=f"Dialect: {config.dialect}")


# ── Application ────────────────────────────────────────────────────────

# ── Enterprise Multi-Page Router Shell (SQLean Lint PRO 3.0) ───────────

class RouterShell(ctk.CTkFrame):
    """Enterprise multi-page router shell wrapping the existing Dashboard.

    Provides a 260px left nav rail with page-switching buttons and a dynamic
    main stage.  The original Dashboard is always present (hidden when another
    page is active) so all existing functionality is preserved.
    """

    _PAGES = (
        ("studio",     "SQL Studio",     TOKENS["ACCENT_CYAN"]),
        ("explorer",   "File Explorer",   TOKENS["ACCENT_EMERALD"]),
        ("diagnostics","Diagnostics",     TOKENS["ACCENT_AMBER"]),
        ("cloud_sync", "Cloud Sync",      TOKENS["ACCENT_PURPLE"]),
        ("dashboard",  "Dashboard (Legacy)", TOKENS["TEXT_MUTED"]),
    )

    @staticmethod
    def _truncate(text: str, max_chars: int = 28) -> str:
        """Clip *text* to *max_chars* and append an ellipsis when truncated."""
        return text if len(text) <= max_chars else text[: max_chars - 1] + "\u2026"

    def __init__(self, master, dashboard: Dashboard, **kwargs) -> None:
        super().__init__(master, fg_color=TOKENS["CANVAS_BG"], corner_radius=0, **kwargs)
        self._dashboard = dashboard
        self._active_page = "studio"
        self._pages: dict = {}
        self._nav_buttons: dict = {}

        self.grid_columnconfigure(0, weight=0, minsize=SPACING["NAV_WIDTH"])
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_nav_rail()
        self._build_stage()
        self._wire_pages()

        # Start on the dashboard (legacy) page to preserve current UX.
        self.show_page("dashboard")

    # ── nav rail ───────────────────────────────────────────────────────

    def _build_nav_rail(self) -> None:
        rail = ctk.CTkFrame(self, fg_color=TOKENS["CANVAS_SIDEBAR"], width=SPACING["NAV_WIDTH"],
                             corner_radius=0)
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)

        # Brand (truncated for long names)
        ctk.CTkLabel(
            rail, text=self._truncate("SQLean", 28), font=FONTS["TITLE"],
            text_color=TOKENS["ACCENT_CYAN"],
        ).grid(row=0, column=0, padx=16, pady=(18, 4), sticky="w")

        ctk.CTkLabel(
            rail, text=self._truncate("PRO 3.0", 28), font=FONTS["CAPTION"],
            text_color=TOKENS["TEXT_MUTED"],
        ).grid(row=1, column=0, padx=16, pady=(0, 18), sticky="w")

        # Page buttons
        for idx, (key, label, color) in enumerate(self._PAGES):
            btn = ctk.CTkButton(
                rail, text=f"  {label}", anchor="w",
                font=FONTS["BODY"], height=36,
                fg_color="transparent", text_color=TOKENS["TEXT_MUTED"],
                hover_color=TOKENS["SURFACE_HOVER"],
                corner_radius=SPACING["RADIUS_SM"],
                command=lambda k=key: self.show_page(k),
            )
            btn.grid(row=idx + 2, column=0, padx=8, pady=2, sticky="ew")
            self._nav_buttons[key] = (btn, color)

        rail.grid_columnconfigure(0, weight=1)

    # ── stage ──────────────────────────────────────────────────────────

    def _build_stage(self) -> None:
        self._stage = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self._stage.grid(row=0, column=1, sticky="nsew")
        self._stage.grid_columnconfigure(0, weight=1)
        self._stage.grid_rowconfigure(0, weight=1)

    # ── page wiring ────────────────────────────────────────────────────

    def _wire_pages(self) -> None:
        from .ui.pages.studio import StudioPage
        from .ui.pages.explorer import ExplorerPage
        from .ui.pages.diagnostics import DiagnosticsPage
        from .ui.pages.cloud_sync import CloudSyncPage

        self._pages["dashboard"] = self._dashboard

        self._pages["studio"] = StudioPage(
            self._stage,
            on_run_query=self._dashboard._run_query,
            on_autofix=self._dashboard._on_autofix,
            on_format=self._dashboard._format_sql,
        )

        self._pages["explorer"] = ExplorerPage(
            self._stage,
            on_browse=self._dashboard._on_browse,
            on_scan=self._dashboard._on_scan_directory,
            on_clear=self._dashboard._on_clear,
        )

        self._pages["diagnostics"] = DiagnosticsPage(
            self._stage,
            on_node_clicked=self._dashboard._on_ast_node_clicked,
        )

        # Lazy-init CloudSyncManager
        try:
            from .cloud.sync import CloudSyncManager
            sync_mgr = CloudSyncManager()
        except Exception:  # noqa: BLE001
            sync_mgr = None

        self._pages["cloud_sync"] = CloudSyncPage(
            self._stage,
            sync_manager=sync_mgr,
        )

        # Mount all pages into the stage (hidden initially).
        for key, page in self._pages.items():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

    # ── page switching ─────────────────────────────────────────────────

    def show_page(self, key: str) -> None:
        if key not in self._pages:
            return
        # Hide current
        if self._active_page in self._pages:
            try:
                self._pages[self._active_page].grid_remove()
            except Exception:  # noqa: BLE001
                pass
        # Highlight active nav button
        for k, (btn, color) in self._nav_buttons.items():
            try:
                if k == key:
                    btn.configure(fg_color=TOKENS["SURFACE_HOVER"], text_color=color)
                else:
                    btn.configure(fg_color="transparent", text_color=TOKENS["TEXT_MUTED"])
            except Exception:  # noqa: BLE001
                pass
        # Show target page
        self._active_page = key
        try:
            self._pages[key].grid()
        except Exception:  # noqa: BLE001
            pass

    # ── public delegation ──────────────────────────────────────────────

    @property
    def dashboard(self) -> Dashboard:
        return self._dashboard

    def refresh_diagnostics(self) -> None:
        """Push latest findings from the Dashboard into the Diagnostics page."""
        diag = self._pages.get("diagnostics")
        if diag and hasattr(diag, "set_findings"):
            diag.set_findings(self._dashboard._results)

    def refresh_studio(self) -> None:
        """Push current SQL into the Studio page editor."""
        studio = self._pages.get("studio")
        if studio and hasattr(studio, "render"):
            sql = self._dashboard._current_sql or ""
            studio.render(sql, sql)


class ChatAssistant(ctk.CTkToplevel):
    """Floating 'Support Assistant' widget backed by :class:`OfflineOnlineAgent`."""

    def __init__(self, master, _tr=None, online: Optional[bool] = None) -> None:
        super().__init__(master)
        self._tr = _tr or _default_translator()
        self._agent = OfflineOnlineAgent(online=online)
        self.title("Support Assistant — SQLean Lint Pro")
        self.geometry("520x560")
        self.minsize(420, 420)
        self.lift()
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        container = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=0,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            container,
            text="Support Assistant",
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
            text_color=COLOR_ACCENT,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self._history = ctk.CTkTextbox(
            container,
            font=ctk.CTkFont("Segoe UI", 13),
            fg_color=COLOR_ZONE,
            text_color=COLOR_TEXT,
            wrap="word",
            border_width=_BORDER_WIDTH,
            border_color=_BORDER_COLOR,
        )
        self._history.grid(row=1, column=0, sticky="nsew")
        self._history.configure(state="disabled")
        self._history.insert("end", self._agent.answer(
            "Hello! I'm the zero-cost support assistant. Ask about installation, "
            "licensing, the GUI shortcuts, or SQL rules like SQL-SARG-001 and "
            "SQL-CART-001.\n"
        ) + "\n")

        input_row = ctk.CTkFrame(container, fg_color="transparent")
        input_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        input_row.grid_columnconfigure(0, weight=1)
        self._entry = ctk.CTkEntry(
            input_row,
            placeholder_text="Type your question and press Enter...",
            font=ctk.CTkFont("Segoe UI", 13),
        )
        self._entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._entry.bind("<Return>", lambda e: self._send())
        ctk.CTkButton(
            input_row,
            text="Send",
            width=80,
            fg_color=COLOR_ACCENT,
            command=self._send,
        ).grid(row=0, column=1)

        self._entry.focus_set()
        self.after(100, self._restore_focus)

    def _restore_focus(self) -> None:
        try:
            self.lift()
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass

    def _send(self) -> None:
        message = self._entry.get().strip()
        if not message:
            return
        self._entry.delete(0, "end")
        self._history.configure(state="normal")
        self._history.insert("end", f"\nYou: {message}\n")
        answer = self._agent.answer(message)
        self._history.insert("end", f"Assistant: {answer}\n\n")
        self._history.see("end")
        self._history.configure(state="disabled")

    def _on_close(self) -> None:
        self.destroy()


class App:
    """Top-level container that switches between activation and dashboard."""

    def __init__(self, language: str = "en", theme: Optional[str] = None) -> None:
        self._tr = get_translator(language)
        load_catalog(language)

        root_cls = TkinterDnD.Tk if _DND_AVAILABLE else ctk.CTk
        self.root = root_cls()
        self.root.title(self._tr("app.title"))
        self.root.geometry(APP_SIZE)
        self.root.minsize(980, 600)

        self._validator = LicenseValidator()
        self._config = LinterConfig.from_env_or_default()
        if theme:
            self._config.theme = theme
        self._content: Optional[ctk.CTkFrame] = None
        self._assistant: Optional[ChatAssistant] = None

        # Global Tk exception handler: surface any error raised by a UI event or
        # background thread callback as a visible modal instead of failing
        # silently in the console.
        self.root.report_callback_exception = self._on_unhandled_error

        # Centralized WCAG 2.2 design system.
        self._theme_manager = _ThemeManager(variant=self._config.theme or "dark")
        self._load_theme()
        if self._validator.is_licensed():
            self._show_dashboard()
        else:
            self._show_activation()

    def _load_theme(self) -> None:
        ctk.set_appearance_mode(self._config.theme)
        try:
            self._theme_manager.apply_theme(self.root)
        except Exception:  # noqa: BLE001 - theme application is best-effort
            pass
        # Phase 4: native dark-window styling (pywinstyles) on Windows so the
        # title bar and tray chrome match the in-app dark palette.
        if self._config.theme != "light":
            try:
                import pywinstyles

                pywinstyles.apply_style(self.root, "dark")
            except Exception:  # noqa: BLE001 - platform styling is best-effort
                pass

    def _on_unhandled_error(self, exc_type, exc_value, exc_tb) -> None:
        """Render an unhandled Tk/thread exception in a sleek error modal."""
        try:
            import traceback

            if exc_tb is not None:
                detail = "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                )
            else:
                detail = f"{exc_type.__name__}: {exc_value}"
            _logger().error("Unhandled GUI error:\n%s", detail)
            self._show_error_modal(
                f"{exc_type.__name__}: {exc_value}", detail
            )
        except Exception:  # noqa: BLE001 - never recurse into the handler
            try:
                _logger().exception("Error while handling GUI exception.")
            except Exception:  # noqa: BLE001
                pass

    def _show_error_modal(self, title: str, detail: str) -> None:
        """Pop up a CustomTkinter modal showing the error and its traceback."""
        try:
            modal = ctk.CTkToplevel(self.root)
            modal.title("Error")
            modal.geometry("720x420")
            modal.resizable(True, True)
            modal.lift()
            modal.attributes("-topmost", True)
            modal.configure(fg_color=COLOR_BG)

            frame = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=10,
                                 border_width=_BORDER_WIDTH,
                                 border_color=_BORDER_COLOR)
            frame.pack(fill="both", expand=True, padx=16, pady=16)

            ctk.CTkLabel(
                frame,
                text=title or "Unexpected error",
                font=ctk.CTkFont("Segoe UI", 16, "bold"),
                text_color=COLOR_ERR,
            ).pack(anchor="w", padx=16, pady=(16, 6))

            textbox = ctk.CTkTextbox(
                frame,
                font=ctk.CTkFont("Consolas", 12),
                fg_color=COLOR_ZONE,
                text_color=COLOR_TEXT,
                wrap="none",
            )
            textbox.pack(fill="both", expand=True, padx=16, pady=(0, 8))
            textbox.insert("1.0", detail or "")

            ctk.CTkButton(
                frame,
                text="Dismiss",
                width=120,
                height=32,
                command=modal.destroy,
            ).pack(pady=(0, 12))
        except Exception:  # noqa: BLE001 - the modal itself must never crash
            try:
                _logger().exception("Could not display the error modal.")
            except Exception:  # noqa: BLE001
                pass

    def _show_activation(self) -> None:
        self._clear_content()
        self._content = ActivationView(self.root, self._on_activated, self._tr)
        self._content.pack(fill="both", expand=True)

    def _show_dashboard(self) -> None:
        self._clear_content()
        dashboard = Dashboard(
            self.root, self._config, self._open_settings, self._tr,
            on_open_assistant=self._toggle_assistant,
        )
        self._content = RouterShell(self.root, dashboard)
        self._content.pack(fill="both", expand=True)

    def _toggle_assistant(self) -> None:
        if self._assistant is not None and self._assistant.winfo_exists():
            self._assistant.destroy()
            self._assistant = None
            return
        self._assistant = ChatAssistant(self.root, self._tr)
        self._assistant.protocol(
            "WM_DELETE_WINDOW", self._close_assistant
        )

    def _close_assistant(self) -> None:
        if self._assistant is not None:
            self._assistant.destroy()
            self._assistant = None

    def _clear_content(self) -> None:
        if self._content is not None:
            self._content.destroy()
            self._content = None

    def _on_activated(self, _info) -> None:
        self._show_dashboard()

    def _open_settings(self) -> None:
        SettingsModal(self.root, self._config, self._apply_settings, self._tr)

    def _apply_settings(self, config: LinterConfig) -> None:
        self._config = config
        ctk.set_appearance_mode(config.theme)
        try:
            self._theme_manager.switch(config.theme, self.root)
        except Exception:  # noqa: BLE001 - theme switch is best-effort
            pass
        if self._content is not None and isinstance(self._content, RouterShell):
            self._content.dashboard.apply_config(config)
        elif self._content is not None and isinstance(self._content, Dashboard):
            self._content.apply_config(config)

    def run(self) -> None:
        self.root.mainloop()


def _default_translator() -> Translator:
    return get_translator(None)


def main(language: str = "en", theme: Optional[str] = None) -> None:
    app = App(language=language, theme=theme)
    app.run()


if __name__ == "__main__":
    main()
