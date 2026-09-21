"""Diagnostics & DAG Flow page — findings list + DAG canvas + health gauge.

Composes DagCanvas, the health gauge widget, and a findings table.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Sequence

import customtkinter as ctk

from ..design_system import TOKENS, FONTS, SPACING
from ..dag_canvas import DagCanvas


class DiagnosticsPage(ctk.CTkFrame):
    """Consolidated diagnostics view with severity-sorted findings and DAG."""

    def __init__(
        self,
        master,
        *,
        on_node_clicked: Optional[Callable] = None,
        bg_color: str = "transparent",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, **kwargs)
        self._on_node_clicked = on_node_clicked

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_toolbar()
        self._build_content()

    # ── toolbar ────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = ctk.CTkFrame(self, fg_color=TOKENS["CANVAS_TOOLBAR"], height=SPACING["TOOLBAR_HEIGHT"],
                           corner_radius=0)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tb, text="Diagnostics", font=FONTS["TITLE"],
                     text_color=TOKENS["ACCENT_AMBER"]).grid(row=0, column=0, padx=16, sticky="w")

        self._finding_count = ctk.CTkLabel(
            tb, text="0 finding(s)", font=FONTS["CODE_GUTTER"],
            text_color=TOKENS["TEXT_MUTED"],
        )
        self._finding_count.grid(row=0, column=1, padx=8)

    # ── content: findings list + DAG canvas ─────────────────────────────

    def _build_content(self) -> None:
        pane = ctk.CTkFrame(self, fg_color="transparent")
        pane.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        pane.grid_columnconfigure(0, weight=1)
        pane.grid_columnconfigure(1, weight=2)
        pane.grid_rowconfigure(0, weight=1)

        # Findings list
        self._findings_box = ctk.CTkTextbox(
            pane,
            font=FONTS["CODE"],
            fg_color=TOKENS["CANVAS_SIDEBAR"],
            corner_radius=8,
            wrap="none",
            border_width=1,
            border_color=TOKENS["BORDER_SUBTLE"],
        )
        self._findings_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._findings_box.configure(state="disabled")

        # DAG viewer
        self._dag = DagCanvas(
            pane,
            bg_color=TOKENS["CANVAS_SIDEBAR"],
            on_node_clicked=self._on_node_clicked,
        )
        self._dag.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

    # ── public API ─────────────────────────────────────────────────────

    def set_findings(self, results: Sequence[tuple]) -> None:
        self._findings_box.configure(state="normal")
        self._findings_box.delete("1.0", "end")
        count = 0
        for path, row in results:
            if row[0] == "PARSE-ERR":
                text = f"[ERROR] {Path(path).name}: {row[1]}\n"
                self._findings_box.insert("end", text, "ERROR")
            else:
                rule_id, message, severity, line, col, _fixable = row[:6]
                text = f"[{severity}] {Path(path).name}:{line}:{col}  {rule_id}  {message}\n"
                tag = severity.upper()
                self._findings_box.insert("end", text, tag)
            count += 1
        self._findings_box.configure(state="disabled")
        self._finding_count.configure(text=f"{count} finding(s)")

    def render_dag(self, sql: str, dialect: str = "duckdb") -> None:
        try:
            self._dag.render_sql(sql, dialect)
        except Exception:  # noqa: BLE001
            pass
