"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Interactive chart view (Excel/KNIME-style) for grid-formatted query results.

Renders categorical and numeric datasets with ``matplotlib`` embedded through
the Tk canvas backend (:class:`FigureCanvasTkAgg`). The widget only *builds*
the canvas; it never starts ``mainloop``, so it is safe to import in headless
QA (matplotlib must be installed for rendering to actually run).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import customtkinter as ctk

from .design_system import TOKENS

_CANVAS = TOKENS["CANVAS_BG"]
_TEXT = TOKENS["TEXT_PRIMARY"]
_MUTED = TOKENS["TEXT_MUTED"]
_ACCENT = TOKENS["ACCENT_PRIMARY"]
_GRID = TOKENS["BORDER_SUBTLE"]


class ChartView(ctk.CTkFrame):
    """A lightweight chart canvas that mirrors the current result dataset.

    The chart engine imports ``matplotlib`` lazily inside :meth:`render` so
    the module remains importable even when matplotlib is unavailable; in that
    case the view renders a friendly "Chart engine unavailable" placeholder.
    """

    SUPPORTED = {"bar", "line", "pie"}

    def __init__(self, master, *, bg_color: str = _CANVAS,
                 text_color: str = _TEXT, muted_color: str = _MUTED,
                 accent_color: str = _ACCENT) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8)
        self._text = text_color
        self._muted = muted_color
        self._accent = accent_color
        self._kind = "bar"
        self._columns: List[str] = []
        self._rows: List[Dict[str, Any]] = []
        self._canvas = None

        self._toolbar = ctk.CTkFrame(self, fg_color="transparent")
        self._toolbar.pack(fill="x", padx=8, pady=(6, 2))
        self._title = ctk.CTkLabel(
            self._toolbar, text="Chart", font=ctk.CTkFont("Segoe UI", 10),
            text_color=self._muted,
        )
        self._title.pack(side="left")
        for kind in ("bar", "line", "pie"):
            ctk.CTkButton(
                self._toolbar, text=kind.capitalize(), width=44, height=22,
                font=ctk.CTkFont("Segoe UI", 10),
                fg_color=_CANVAS, text_color=self._text,
                command=lambda k=kind: self.set_kind(k),
            ).pack(side="right", padx=(2, 0))

        self._host = ctk.CTkFrame(self, fg_color=_CANVAS)
        self._host.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def set_kind(self, kind: str) -> None:
        kind = (kind or "bar").lower()
        if kind not in self.SUPPORTED:
            kind = "bar"
        self._kind = kind
        self.render()

    def set_data(self, columns: List[str], rows: List[Dict[str, Any]]) -> None:
        self._columns = list(columns or [])
        self._rows = list(rows or [])
        self.render()

    def render(self) -> None:
        """Draw (or clear) the chart from the cached dataset."""
        try:
            self._destroy_canvas()
            if not self._columns or not self._rows:
                self._placeholder()
                return
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            numeric = _numeric_series(self._columns, self._rows)
            if not numeric:
                self._placeholder("No numeric column to chart.")
                return
            label_cols = [c for c in self._columns if c not in numeric]
            labels_col = label_cols[0] if label_cols else self._columns[0]
            value_col = numeric[0]
            labels = [_str(self._rows[i].get(labels_col)) for i in range(len(self._rows))]
            values = [_num(self._rows[i].get(value_col)) for i in range(len(self._rows))]

            fig = Figure(figsize=(5, 3), dpi=100, facecolor=_CANVAS)
            ax = fig.add_subplot(111)
            ax.set_facecolor(_CANVAS)
            ax.tick_params(colors=_TEXT)
            ax.xaxis.label.set_color(_TEXT)
            ax.yaxis.label.set_color(_TEXT)
            for spine in ax.spines.values():
                spine.set_color(_GRID)

            if self._kind == "pie":
                ax.pie(values, labels=labels, autopct="%1.1f%%",
                       colors=[_ACCENT, "#4ADE80", "#F59E0B", _TEXT, "#A78BFA"])
            elif self._kind == "line":
                ax.plot(range(len(values)), values, marker="o",
                        color=_ACCENT, linewidth=2)
                ax.set_xticks(range(len(values)))
                ax.set_xticklabels(labels, rotation=30, ha="right", color=_TEXT)
            else:
                ax.bar(range(len(values)), values, color=_ACCENT)
                ax.set_xticks(range(len(values)))
                ax.set_xticklabels(labels, rotation=30, ha="right", color=_TEXT)

            ax.set_ylabel(value_col, color=_TEXT)

            for ii in range(0, len(fig.axes), 1):
                fig.axes[ii].tick_params(
                    colors=_TEXT,
                    labelcolor=_TEXT,
                    grid_color=_GRID,
                )

            self._canvas = FigureCanvasTkAgg(fig, master=self._host)
            self._canvas.get_tk_widget().pack(fill="both", expand=True)
            self._canvas.draw()
        except Exception:  # noqa: BLE001 - chart must never crash the GUI
            self._placeholder("Chart engine unavailable.")

    def _destroy_canvas(self) -> None:
        if self._canvas is not None:
            try:
                self._canvas.get_tk_widget().destroy()
            except Exception:  # noqa: BLE001
                pass
            self._canvas = None
        for child in self._host.winfo_children():
            try:
                child.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _placeholder(self, msg: str = "No data to chart.") -> None:
        try:
            ctk.CTkLabel(
                self._host,
                text=msg,
                font=ctk.CTkFont("Segoe UI", 12, "italic"),
                text_color=self._muted,
            ).pack(expand=True)
        except Exception:  # noqa: BLE001
            pass


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _numeric_series(columns: List[str], rows: List[Dict[str, Any]]) -> List[str]:
    numeric = []
    for c in columns:
        if not rows:
            continue
        try:
            float(rows[0].get(c))
            numeric.append(c)
        except (TypeError, ValueError):
            continue
    return numeric
