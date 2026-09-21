# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA) | kierninja@gmail.com
# All Rights Reserved.

"""Interactive data grid for the sqlean-lint PRO GUI.

A rich, sortable, scrollable Excel-style grid that renders a live preview of
query results (first ``MAX_ROWS`` rows) below the code editor.

* :class:`DataGrid` renders column headers (click to sort) and data cells in
  a ``CTkScrollableFrame``.
* :class:`QueryRunner` executes SQL through the existing
  :class:`~sqlean_lint.connectors.duckdb_local.DuckDBLocalConnector` on the
  caller's thread and normalises the result into row dicts (a pure, testable
  helper). The GUI smoke-test path feeds rows directly to :meth:`DataGrid.set_rows`.

The widget builds ``CTk`` children lazily and never runs ``mainloop``, so it
is headless-safe.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import customtkinter as ctk

# ── Design tokens (bind to the WCAG 2.2 AAA design system) ────────────
from .design_system import TOKENS  # noqa: E402

_SURFACE = TOKENS["SURFACE_ELEVATED"]
_ZONE = TOKENS["PANEL_BG"]
_ACCENT = TOKENS["ACCENT_PRIMARY"]
_TEXT = TOKENS["TEXT_PRIMARY"]
_MUTED = TOKENS["TEXT_MUTED"]
_HEADER_BG = TOKENS["SURFACE_ELEVATED"]
_ALT_ROW = TOKENS["GRID_ROW_ALT"]
_SECONDARY = TOKENS["TEXT_SECONDARY"]
_MODAL = TOKENS["SURFACE_MODAL"]
_PANEL = TOKENS["PANEL_BG"]
_BORDER = TOKENS["BORDER_SUBTLE"]

# The grid keeps the FULL dataset in memory but only materialises the rows
# that fit the visible viewport (see DataGrid._visible_count).
MAX_ROWS = 10**9  # effectively unbounded; viewport-limited rendering
ROW_HEIGHT = 28
HEADER_HEIGHT = 26


def normalize_rows(result: Sequence[Dict[str, Any]], limit: int = MAX_ROWS) -> tuple:
    """Turn connector output into ``(columns, rows)`` for the grid.

    Args:
        result: Rows as returned by a connector's ``execute_query`` (list of
            dicts). If it is a list of tuples, column names are synthesised.
        limit: Cap the number of rows rendered.

    Returns:
        ``(columns, capped_rows)`` where ``columns`` is the ordered column
        name list and ``rows`` the first ``limit`` row dicts.
    """
    if not result:
        return ([], [])
    if isinstance(result[0], dict):
        columns = list(result[0].keys())
        rows = list(result[:limit])
        return (columns, rows)
    # Tuple/sequence rows: synthesise column names.
    width = len(result[0])
    columns = [f"col{i + 1}" for i in range(width)]
    rows = [
        {columns[i]: row[i] for i in range(width)}
        for row in result[:limit]
    ]
    return (columns, rows)


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


class QueryRunner:
    """Runs SQL via ``DuckDBLocalConnector`` and returns grid-ready rows.

    The connector is injected so tests can substitute a fake; the pure
    function behaves identically with any object exposing ``execute_query``.
    """

    def __init__(self, connector) -> None:
        self._connector = connector
        self.last_error: Optional[str] = None
        self.last_types: List[str] = []
        self.last_elapsed_ms: int = 0

    def run(self, sql: str) -> List[Dict[str, Any]]:
        """Execute *sql* and return row dicts (or an empty list on error).

        Errors are swallowed and returned as an empty list so the GUI never
        crashes on bad SQL; callers may inspect :attr:`last_error`.
        """
        import time as _time

        self.last_error = None
        self.last_types = []
        started = _time.perf_counter()
        try:
            # Prefer the typed variant so the grid can show DuckDB type badges.
            typed = getattr(self._connector, "execute_query_typed", None)
            if callable(typed):
                columns, types, rows = typed(sql)
                self.last_types = list(types)
                return list(rows)
            result = self._connector.execute_query(sql)
            if result is None:
                return []
            _, rows = normalize_rows(result)
            return rows
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return []
        finally:
            self.last_elapsed_ms = int((_time.perf_counter() - started) * 1000)


class DataGrid(ctk.CTkFrame):
    """Virtualized, sortable, scrollable grid for 100,000+ row result sets.

    The full dataset is retained in memory (:attr:`_raw_rows`), but only the
    rows that fit the visible viewport are ever materialised as ``CTkLabel``
    widgets.  Scrolling re-slices the raw rows and repaints just those cells,
    so a million-row result never blocks the GUI or exhausts memory.
    """

    def __init__(
        self,
        master,
        *,
        bg_color: str = _ZONE,
        surface_color: str = _SURFACE,
        accent_color: str = _ACCENT,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        header_bg: str = _HEADER_BG,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8)
        self._surface = surface_color
        self._accent = accent_color
        self._text = text_color
        self._muted = muted_color
        self._header_bg = header_bg

        self._columns: List[str] = []
        self._types: List[str] = []
        self._raw_rows: List[Dict[str, Any]] = []
        self._execution_time_ms: int = 0

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(6, 2))

        self._count = ctk.CTkLabel(
            toolbar, text="0 rows", font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._count.pack(side="left")

        self._clear_btn = ctk.CTkButton(
            toolbar, text="Clear", width=60, height=22,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=self._surface, text_color=self._text,
            command=self.clear,
        )
        self._clear_btn.pack(side="right")

        # Viewport canvas: sticky header row + recycled body cells.
        self._canvas = ctk.CTkCanvas(
            self, bg=bg_color, highlightthickness=0, bd=0
        )
        self._canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._vscroll = ctk.CTkScrollbar(
            self, command=self._on_vscroll, fg_color=self._surface,
            button_color=self._surface, button_hover_color=self._header_bg,
        )
        self._vscroll.pack(side="right", fill="y", padx=(0, 4), pady=(0, 8))
        self._canvas.configure(yscrollcommand=self._vscroll.set)
        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self._canvas.bind("<Button-4>", lambda e: self._step_wheel(-1, e))
        self._canvas.bind("<Button-5>", lambda e: self._step_wheel(1, e))

        self._header_row = 0
        self._start_index = 0
        self._visible_count = 0
        self._col_widths: List[int] = []
        self._sort_col: Optional[str] = None
        self._sort_asc = True
        self._resize_col: Optional[int] = None
        self._row_gap = 2
        self._body: Dict[int, Dict[int, int]] = {}  # canvas_id -> (row,col)

        self._maybe_show_empty()

    # ?? public API ?????????????????????????????????????????????????????

    def set_dataset(
        self,
        columns: Sequence[str],
        rows: List[Dict[str, Any]],
        *,
        types: Optional[Sequence[str]] = None,
        execution_time_ms: int = 0,
    ) -> None:
        """Bulk-load a (potentially huge) result set into the grid.

        The full ``rows`` list is retained; only the visible window is drawn.
        """
        self._columns = list(columns)
        self._types = list(types or [])
        self._raw_rows = list(rows)
        self._execution_time_ms = int(execution_time_ms)
        self._sort_col = None
        self._sort_asc = True
        self._start_index = 0
        self._refresh_status()
        self._render_viewport(reset_scroll=True)

    def set_rows(
        self,
        columns: Sequence[str],
        rows: List[Dict[str, Any]],
        types: Optional[Sequence[str]] = None,
    ) -> None:
        """Backwards-compatible alias for :meth:`set_dataset`."""
        self.set_dataset(columns, rows, types=types)

    def load(
        self,
        columns: Sequence[str],
        rows: List[Dict[str, Any]],
        types: Optional[Sequence[str]] = None,
    ) -> None:
        """Alias used by callers that prefer ``load`` naming."""
        self.set_dataset(columns, rows, types=types)

    def clear(self) -> None:
        self._columns = []
        self._types = []
        self._raw_rows = []
        self._execution_time_ms = 0
        self._start_index = 0
        self._canvas.delete("all")
        self._body.clear()
        self._refresh_status()
        self._maybe_show_empty()

    def current_rows(self) -> List[Dict[str, Any]]:
        return list(self._raw_rows)

    def row_count(self) -> int:
        return len(self._raw_rows)

    def max_rows(self) -> int:
        """The grid is viewport-bounded, so it accepts the full dataset."""
        return MAX_ROWS

    # ?? sorting ????????????????????????????????????????????????????????

    def _toggle_sort(self, column: str) -> None:
        if self._sort_col == column:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = column
            self._sort_asc = True
        self._raw_rows = sort_rows(self._raw_rows, column, self._sort_asc)
        self._render_viewport(reset_scroll=True)

    # ?? viewport computation ???????????????????????????????????????????

    def _visible_count(self) -> int:
        canvas_h = self._canvas.winfo_height()
        if canvas_h <= HEADER_HEIGHT:
            return 20
        body_h = canvas_h - HEADER_HEIGHT - self._row_gap
        return max(1, int(body_h // (ROW_HEIGHT + self._row_gap)))

    def _maybe_show_empty(self) -> None:
        if not self._columns:
            self._canvas.delete("all")
            self._canvas.create_text(
                12, HEADER_HEIGHT + 24, anchor="nw",
                text="Run a query to preview results",
                fill=self._muted, font=("Segoe UI", 12),
            )

    # ?? rendering ??????????????????????????????????????????????????????

    def _refresh_status(self) -> None:
        if not self._columns:
            self._count.configure(text="0 rows")
            return
        n = len(self._raw_rows)
        if self._execution_time_ms:
            self._count.configure(
                text=f"{n:,} rows loaded in {self._execution_time_ms} ms"
            )
        else:
            self._count.configure(text=f"{n:,} rows")

    def _draw_header(self) -> None:
        x = 0
        self._canvas.delete("header")
        for c, col in enumerate(self._columns):
            w = self._col_widths[c] if c < len(self._col_widths) else 140
            arrow = " ?" if self._sort_col == col and self._sort_asc else (
                " ?" if self._sort_col == col else ""
            )
            self._canvas.create_rectangle(
                x, 0, x + w, HEADER_HEIGHT, fill=_HEADER_BG,
                outline=_BORDER if globals().get("_BORDER") else _HEADER_BG,
                tags=("header", "head",
                      "sort:" + str(c), "col:" + str(c)),
            )
            self._canvas.create_text(
                x + 6, HEADER_HEIGHT // 2, anchor="w",
                text=(col + arrow).upper(), font=("Segoe UI", 11, "bold"),
                fill=_SECONDARY, tags=("header",),
            )
            # Resize grip at the right edge of each header cell.
            self._canvas.create_line(
                x + w - 2, 0, x + w - 2, HEADER_HEIGHT,
                fill=_BORDER if globals().get("_BORDER") else _HEADER_BG,
                tags=("header", "grip", "col:" + str(c), "sort:" + str(c)),
            )
            x += w

        self._canvas.tag_bind(
            "head", "<Button-1>", lambda e: self._header_click(e)
        )
        self._canvas.tag_bind("grip", "<Button-1>", self._grab_grip)
        self._canvas.tag_bind("grip", "<B1-Motion>", self._drag_grip)
        self._canvas.tag_bind("grip", "<ButtonRelease-1>", self._release_grip)

    def _view_columns(self) -> int:
        total_w = sum(self._col_widths) if self._col_widths else 0
        canvas_w = self._canvas.winfo_width()
        if not self._col_widths or total_w == 0:
            return max(1, len(self._columns))
        idx = 0
        acc = 0
        for w in self._col_widths:
            if acc >= canvas_w:
                break
            idx += 1
            acc += w
        return max(1, idx)

    def _render_viewport(self, reset_scroll: bool = False) -> None:
        self._canvas.delete("all")
        self._body.clear()
        if not self._columns:
            self._maybe_show_empty()
            return
        if not self._col_widths:
            self._col_widths = [
                max(60, self._canvas.winfo_width() // max(1, len(self._columns)))
                for _ in self._columns
            ]

        self._draw_header()

        n = len(self._raw_rows)
        self._visible_count = self._visible_count()
        if n == 0:
            self._refresh_status()
            return

        if reset_scroll:
            self._start_index = 0
        max_start = max(0, n - self._visible_count)
        self._start_index = min(self._start_index, max_start)

        # Configure the virtual scroll region (1 unit == 1 row).
        self._canvas.configure(scrollregion=(0, 0, 0, n))
        self._canvas.yview_moveto(self._start_index / max(1, n))

        self._draw_body()
        self._refresh_status()

    def _draw_body(self) -> None:
        self._canvas.delete("bodyrow")
        start = self._start_index
        end = min(start + self._visible_count, len(self._raw_rows))
        for vr, r in enumerate(range(start, end)):
            y = HEADER_HEIGHT + vr * (ROW_HEIGHT + self._row_gap)
            x = 0
            bg = _PANEL if r % 2 == 0 else _ALT_ROW
            for c, col in enumerate(self._columns):
                w = self._col_widths[c] if c < len(self._col_widths) else 140
                val = self._raw_rows[r].get(col)
                if val is None:
                    text = "NULL"
                    italic = True
                    color = self._muted
                else:
                    text = str(val)
                    italic = False
                    color = self._text
                rect = self._canvas.create_rectangle(
                    x, y, x + w, y + ROW_HEIGHT, fill=bg, outline=bg,
                    tags=("bodyrow",),
                )
                self._canvas.create_text(
                    x + 6, y + ROW_HEIGHT // 2, anchor="w",
                    text=text, font=("Consolas", 11, "italic" if italic else "normal"),
                    fill=color, tags=("bodyrow",),
                )
                self._body[rect] = (r, c)
                x += w

    def _on_resize(self, _event) -> None:
        if self._canvas.winfo_width() <= 1:
            return
        if not self._col_widths and self._columns:
            self._col_widths = [
                max(60, self._canvas.winfo_width() // len(self._columns))
                for _ in self._columns
            ]
        self._render_viewport()

    def _on_wheel(self, event) -> None:
        if not self._columns:
            return
        delta = -1 if getattr(event, "delta", 0) > 0 else 1
        self._step_wheel(delta)

    def _step_wheel(self, delta: int, _event=None) -> None:
        if not self._columns:
            return
        n = len(self._raw_rows)
        if n <= self._visible_count:
            return
        max_start = n - self._visible_count
        self._start_index = max(0, min(max_start, self._start_index + delta))
        self._canvas.yview_moveto(self._start_index / max(1, n))
        self._draw_body()

    def _on_vscroll(self, *args) -> None:
        """Map a scrollbar command back to the row-based start index."""
        if not self._columns:
            return
        n = len(self._raw_rows)
        if n <= 0:
            return
        try:
            if args and args[0] == "moveto":
                frac = float(args[1])
            elif args and args[0] == "scroll":
                units = int(args[1])
                self._start_index = max(
                    0, min(n - 1, self._start_index + units)
                )
                self._draw_body()
                return
            else:
                frac = self._canvas.yview()[0]
        except (ValueError, IndexError):
            return
        self._start_index = max(
            0, min(n - self._visible_count, int(frac * n))
        )
        self._draw_body()

    def _header_click(self, event) -> None:
        closest = self._canvas.find_closest(event.x, event.y)
        tags = self._canvas.gettags(closest[0]) if closest else ()
        col_tag = next((t for t in tags if t.startswith("col:")), None)
        if col_tag is None:
            return
        col_idx = int(col_tag.split(":")[1])
        if 0 <= col_idx < len(self._columns):
            self._toggle_sort(self._columns[col_idx])

    def _grab_grip(self, event) -> None:
        closest = self._canvas.find_closest(event.x, event.y)
        tags = self._canvas.gettags(closest[0]) if closest else ()
        col_tag = next((t for t in tags if t.startswith("col:")), None)
        self._resize_col = int(col_tag.split(":")[1]) if col_tag else None

    def _drag_grip(self, event) -> None:
        if self._resize_col is None:
            return
        if not self._col_widths or self._resize_col >= len(self._col_widths):
            return
        left = sum(self._col_widths[: self._resize_col])
        new_width = max(40, event.x - left)
        self._col_widths[self._resize_col] = new_width
        self._render_viewport()

    def _release_grip(self, _event) -> None:
        self._resize_col = None
