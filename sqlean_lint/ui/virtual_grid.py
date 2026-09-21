"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Virtualized high-performance data grid for the sqlean-lint PRO GUI.

Renders DuckDB query results (100,000+ rows) without instantiating a widget
per cell. The grid uses **viewport-based lazy rendering**: only the cell
widgets visible in the current vertical scroll window are created, and they
are recycled as the user scrolls.

Features:
* Sortable column headers with visual direction indicators (``▲`` / ``▼``).
* Drag-to-resize column grips on header boundaries.
* Zebra striping from ``TOKENS["PANEL_BG"]`` / ``TOKENS["GRID_ROW_ALT"]``.
* Missing / ``None`` values rendered in italic with ``TOKENS["TEXT_MUTED"]``.

Headless-safe: only builds ``CTk`` children; never starts ``mainloop``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import customtkinter as ctk

from .design_system import TOKENS

ROW_HEIGHT = 22
HEADER_HEIGHT = 30
FONT = "Consolas"
CELL_WIDTH = 120
MONO = ("Consolas", 10)
MONO_BOLD = ("Consolas", 10, "bold")
MONO_ITALIC = ("Consolas", 10, "italic")


def sort_rows(
    rows: List[Dict[str, Any]],
    column: str,
    ascending: bool = True,
) -> List[Dict[str, Any]]:
    """Return a new list of *rows* sorted by *column* (best-effort, stable)."""
    def key(r):
        v = r.get(column)
        if v is None:
            return (1, "")
        try:
            return (0, v)
        except Exception:  # noqa: BLE001
            return (1, str(v))

    return sorted(rows, key=key, reverse=not ascending)


class VirtualGrid(ctk.CTkFrame):
    """Virtualized, sortable, resizable grid for large result sets."""

    def __init__(
        self,
        master,
        *,
        bg_color: str = TOKENS["PANEL_BG"],
        surface_color: str = TOKENS["SURFACE_CARD"],
        accent_color: str = TOKENS["ACCENT_PRIMARY"],
        text_color: str = TOKENS["TEXT_PRIMARY"],
        muted_color: str = TOKENS["TEXT_MUTED"],
        header_bg: str = TOKENS["SURFACE_ELEVATED"],
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8)
        self._bg = bg_color
        self._surface = surface_color
        self._accent = accent_color
        self._text = text_color
        self._muted = muted_color
        self._header_bg = header_bg

        self._columns: List[str] = []
        self._types: List[str] = []
        self._rows: List[Dict[str, Any]] = []

        # Row/column dimensions (columns use shared widths tracking resizing).
        self._col_widths: Dict[str, int] = {}
        self._sort_col: Optional[str] = None
        self._sort_asc: bool = True

        # ── toolbar ─────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(6, 2))
        self._count = ctk.CTkLabel(
            toolbar, text="0 rows", font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._count.pack(side="left")
        self._status = ctk.CTkLabel(
            toolbar, text="", font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._status.pack(side="left", padx=(12, 0))
        ctk.CTkButton(
            toolbar, text="Clear", width=60, height=22,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=self._surface, text_color=self._text,
            command=self.clear,
        ).pack(side="right")

        # ── canvas + scrollbar for viewport rendering ───────────────────
        self._canvas = ctk.CTkCanvas(
            self, bg=bg_color, highlightthickness=0, borderwidth=0,
        )
        self._canvas.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))

        self._vsb = ctk.CTkScrollbar(self, orientation="vertical",
                                     command=self._on_scrollbar)
        self._vsb.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))

        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.bind("<Configure>", self._on_configure)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

        # Resize/grip state.
        self._resizing: Optional[tuple] = None  # (col_index, start_x, start_w)

        self._virtual_height = 0
        self._drawing = False

    # ── public API ─────────────────────────────────────────────────────

    def set_columns(self, columns: Sequence[str]) -> None:
        self._columns = list(columns)
        for c in self._columns:
            self._col_widths.setdefault(c, CELL_WIDTH)

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)
        self._virtual_height = max(HEADER_HEIGHT, len(self._rows) * ROW_HEIGHT)
        self._canvas.delete("all")
        self._configure_vscroll()
        self._canvas.yview_moveto(0)
        self._count.configure(text=f"{len(self._rows):,} rows")
        self._draw()

    def set_types(self, types: Sequence[str]) -> None:
        self._types = list(types)

    def load(
        self,
        columns: Sequence[str],
        rows: List[Dict[str, Any]],
        types: Optional[Sequence[str]] = None,
    ) -> None:
        """Populate columns, rows and optional type badges in one call."""
        self.set_columns(columns)
        if types is not None:
            self.set_types(types)
        self.set_rows(rows)

    def clear(self) -> None:
        self._columns = []
        self._types = []
        self._rows = []
        self._col_widths = {}
        self._virtual_height = 0
        self._canvas.delete("all")
        self._configure_vscroll()
        self._status.configure(text="")
        self._count.configure(text="0 rows")

    def row_count(self) -> int:
        return len(self._rows)

    def max_rows(self) -> int:
        return 1_000_000

    def current_rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    # ── scrolling / viewport ───────────────────────────────────────────

    def _configure_vscroll(self) -> None:
        if self._virtual_height <= 0:
            self._vsb.set(0.0, 1.0)
            return
        view = max(1, self._canvas.winfo_height())
        frac = min(1.0, view / self._virtual_height)
        top, bottom = self._canvas.yview()
        self._vsb.set(top, top + frac)

    def _on_scrollbar(self, *args) -> None:
        self._canvas.yview(*args)
        self._draw()

    def _on_wheel(self, event) -> None:
        step = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(step, "units")
        self._draw()

    def _on_configure(self, _event) -> None:
        self._configure_vscroll()
        self._draw()

    # ── drawing ────────────────────────────────────────────────────────

    def _row_bg(self, row_index: int) -> str:
        return TOKENS["GRID_ROW_ALT"] if row_index % 2 else self._bg

    def _visible_rows(self, view_h: int) -> tuple:
        top_frac, _ = self._canvas.yview()
        first = int(top_frac * self._virtual_height // ROW_HEIGHT)
        visible_count = int(view_h // ROW_HEIGHT) + 2
        return max(0, first - 1), min(len(self._rows), first + visible_count)

    def _draw(self) -> None:
        if self._drawing:
            return
        self._drawing = True
        try:
            self._canvas.delete("all")
            if not self._columns:
                self._show_empty()
                return

            view_w = max(100, self._canvas.winfo_width())
            view_h = max(100, self._canvas.winfo_height())
            top_frac, _ = self._canvas.yview()

            # ── header row ──────────────────────────────────────────────
            x = 0
            for c, col in enumerate(self._columns):
                w = self._col_widths.get(col, CELL_WIDTH)
                r = self._canvas.create_rectangle(
                    x, 0, x + w, HEADER_HEIGHT,
                    fill=self._header_bg, outline=TOKENS["BORDER_SUBTLE"],
                )
                arrow = ""
                if self._sort_col == col:
                    arrow = " ▲" if self._sort_asc else " ▼"
                self._canvas.create_text(
                    x + 6, HEADER_HEIGHT // 2, anchor="w",
                    text=f"{col}{arrow}",
                    font=MONO_BOLD,
                    fill=self._accent if self._sort_col == col else self._text,
                )
                # Bind header click to sort.
                self._canvas.tag_bind(r, "<Button-1>",
                                      lambda _e, cc=col: self._toggle_sort(cc))
                # Resize grip at the right boundary.
                grip = self._canvas.create_rectangle(
                    x + w - 3, 2, x + w + 3, HEADER_HEIGHT - 2,
                    fill=TOKENS["BORDER_SUBTLE"], outline="",
                    tags=(f"grip:{c}",),
                )
                self._canvas.tag_bind(grip, "<Button-1>",
                                      lambda _e, ci=c, cx=x, cw=w: self._start_resize(ci, _e, cx, cw))
                x += w

            # ── visible data rows (lazy / viewport) ─────────────────────
            first, last = self._visible_rows(view_h)
            for r in range(first, last):
                row = self._rows[r]
                y = HEADER_HEIGHT + r * ROW_HEIGHT - int(top_frac * self._virtual_height)
                bg = self._row_bg(r)
                col_x = 0
                for c, col in enumerate(self._columns):
                    w = self._col_widths.get(col, CELL_WIDTH)
                    rect = self._canvas.create_rectangle(
                        col_x, y, col_x + w, y + ROW_HEIGHT,
                        fill=bg, outline=TOKENS["BORDER_SUBTLE"],
                    )
                    self._canvas.tag_bind(rect, "<Double-Button-1>",
                                          lambda _e, rr=r: self._copy_row(rr))
                    val = row.get(col)
                    is_null = val is None
                    disp = "" if is_null else str(val)
                    self._canvas.create_text(
                        col_x + 6, y + ROW_HEIGHT // 2, anchor="w",
                        text=disp,
                        font=MONO_ITALIC if is_null else MONO,
                        fill=self._muted if is_null else self._text,
                    )
                    col_x += w
        finally:
            self._drawing = False

    def _show_empty(self) -> None:
        self._canvas.create_text(
            self._canvas.winfo_width() // 2,
            self._canvas.winfo_height() // 2,
            text="Run a query to preview results",
            font=("Segoe UI", 12), fill=self._muted,
        )

    # ── sorting ─────────────────────────────────────────────────────────

    def _toggle_sort(self, column: str) -> None:
        if self._sort_col == column:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = column
            self._sort_asc = True
        self._rows = sort_rows(self._rows, column, self._sort_asc)
        self._raise_status(f"Sorting by {column}")
        self._draw()

    # ── column resize ──────────────────────────────────────────────────

    def _start_resize(self, col_index: int, event, x: int, width: int) -> None:
        self._resizing = (col_index, event.x_root, width)

    def resize_at(self, event) -> None:
        if self._resizing is None:
            return
        idx, start_x, start_w = self._resizing
        delta = event.x_root - start_x
        col = self._columns[idx] if idx < len(self._columns) else None
        if col is not None:
            self._col_widths[col] = max(40, start_w + delta)
        self._draw()

    # ── helpers ────────────────────────────────────────────────────────

    def _copy_row(self, row_index: int) -> None:
        """Best-effort copy of a row to the clipboard."""
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        text = "\t".join(str(row.get(c, "")) for c in self._columns)
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._raise_status(f"Copied row {row_index + 1}")
        except Exception:  # noqa: BLE001
            pass

    def _raise_status(self, message: str) -> None:
        self._status.configure(text=message)
        self.after(3000, lambda: self._status.configure(text=""))
