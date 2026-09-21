"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Code minimap for the sqlean-lint PRO GUI.

A zoomed-out, read-only ``CTkCanvas`` mounted on the far-right edge of the
editor. It renders each source line as a narrow, scaled rectangular bar:

* lines are coloured by their leading keyword family (``SELECT``/``FROM``
  clauses get a cue colour; comments/blank lines are dimmed), mirroring how
  editors like VS Code render their minimap stripes,
* a highlight viewport rectangle tracks the visible range of the main editor,
* clicking or dragging the minimap scrolls the main editor to that position,
* the minimap is kept in sync by watching the editor's ``<Configure>`` and
  ``<MouseWheel>`` events plus an ``after`` heartbeat.

It builds widgets but never runs ``mainloop``, so it is headless-safe.
"""
from __future__ import annotations

import re
from typing import List, Optional

import customtkinter as ctk

# ── Design tokens ─────────────────────────────────────────────────────
_BG = "#1a1b26"
_ZONE = "#1f2335"
_TEXT = "#c0caf5"
_MUTED = "#565f89"
_ACCENT = "#7aa2f7"
_VIEWPORT = "#3b4261"

# (regex, canvas fill) for line classification.
_LINE_RULES = (
    (re.compile(r"^\s*--|^\s*/\*", re.I), _MUTED),
    (re.compile(r"^\s*SELECT|^\s*WITH", re.I), _ACCENT),
    (re.compile(r"^\s*FROM|^\s*JOIN|^\s*ON", re.I), "#9ece6a"),
    (re.compile(r"^\s*WHERE|^\s*HAVING", re.I), "#e0af68"),
    (re.compile(r"^\s*GROUP|^\s*ORDER", re.I), "#f7768e"),
    (re.compile(r"^\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)", re.I),
     "#bb9af7"),
    (re.compile(r"\S"), _TEXT),
)


def _underlying_text(widget) -> Optional:
    """Return the real ``tk.Text`` wrapped by a ``CTkTextbox`` (or None)."""
    for attr in ("_textbox", "_textbox_inner", "_textbox_widget"):
        obj = getattr(widget, attr, None)
        if obj is not None and hasattr(obj, "yview") and hasattr(obj, "get"):
            return obj
    return None


class CodeMinimap(ctk.CTkFrame):
    """A compact visual overview of the editor's source lines."""

    BAR_W = 3
    BAR_H = 2
    BAR_GAP = 1

    def __init__(
        self,
        master,
        editor: ctk.CTkTextbox,
        *,
        width: int = 90,
        bg_color: str = _ZONE,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        accent_color: str = _ACCENT,
        viewport_color: str = _VIEWPORT,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=6, width=width)
        self.grid_propagate(False)
        self._editor = editor
        self._text_color = text_color
        self._muted_color = muted_color
        self._accent_color = accent_color
        self._viewport_color = viewport_color

        self._canvas = ctk.CTkCanvas(
            self, width=width, height=120, bg=bg_color, highlightthickness=0
        )
        self._canvas.pack(fill="both", expand=True)

        self._lines_cache: List[tuple] = []  # (color, char_count)
        self._dirty = True

        # Track viewport resolution for click-to-scroll.
        self._text_widget = _underlying_text(editor)

        self._canvas.bind("<Configure>", lambda e: self._redraw())
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<B1-Motion>", self._on_drag)

        if self._text_widget is not None:
            self._text_widget.bind("<Configure>", lambda e: self._mark_dirty(), add="+")
            self._text_widget.bind("<MouseWheel>", self._on_wheel, add="+")

        # Heartbeat keeps the viewport in sync even without structural edits.
        self.after(150, self._tick)

    # ── public API ─────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Force a full re-read of the editor content."""
        self._dirty = True
        self._redraw()

    # ── internals ──────────────────────────────────────────────────────

    def _mark_dirty(self, *_args) -> None:
        self._dirty = True

    def _tick(self) -> None:
        try:
            if self._dirty:
                self._redraw()
            else:
                self._draw_viewport()
        except Exception:  # noqa: BLE001 - minimap must never crash the app
            pass
        try:
            self.after(150, self._tick)
        except Exception:  # noqa: BLE001
            pass

    def _read_lines(self) -> List[tuple]:
        widget = self._text_widget
        if widget is None:
            content = self._editor.get("1.0", "end")
            lines = content.splitlines() or [""]
            return [
                (self._classify(line), len(line)) for line in lines
            ]
        content = widget.get("1.0", "end")
        lines = content.splitlines() or [""]
        return [(self._classify(line), len(line)) for line in lines]

    def _classify(self, line: str) -> str:
        for pattern, colour in _LINE_RULES:
            if pattern.search(line):
                return colour
        return self._muted_color

    def _redraw(self) -> None:
        self._canvas.delete("all")
        self._lines_cache = self._read_lines()
        self._dirty = False
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        bar_w = self.BAR_W
        bar_h = self.BAR_H
        step = bar_h + self.BAR_GAP
        x = 8
        for i, (colour, _cc) in enumerate(self._lines_cache):
            y = 4 + i * step
            if y > h:
                break
            self._canvas.create_rectangle(
                x, y, x + bar_w, y + bar_h, fill=colour, outline=""
            )
        self._draw_viewport()

    def _visible_range(self) -> tuple:
        """Return (first_visible_line, last_visible_line)."""
        widget = self._text_widget
        if widget is None:
            return (0, 0)
        try:
            top = widget.yview()[0]
            bot = widget.yview()[1]
            total = len(self._lines_cache) or 1
            first = int(top * total)
            last = int(bot * total)
            return (first, last)
        except Exception:  # noqa: BLE001
            return (0, 0)

    def _draw_viewport(self) -> None:
        h = self._canvas.winfo_height()
        if h <= 1 or not self._lines_cache:
            return
        total = len(self._lines_cache)
        step = self.BAR_H + self.BAR_GAP
        first, last = self._visible_range()
        y0 = 4 + first * step
        y1 = 4 + max(first + 1, last) * step
        self._canvas.delete("viewport")
        self._canvas.create_rectangle(
            4, y0, self._canvas.winfo_width() - 4, y1,
            fill="", outline=self._viewport_color, width=1, tags=("viewport",),
        )

    def _line_at_y(self, y: int) -> int:
        step = self.BAR_H + self.BAR_GAP
        idx = int((y - 4) / step) if step else 0
        return max(0, min(idx, len(self._lines_cache) - 1))

    def _scroll_to_line(self, line: int) -> None:
        widget = self._text_widget
        if widget is None:
            return
        total = len(self._lines_cache) or 1
        frac = max(0.0, min(1.0, line / max(1, total - 1)))
        widget.yview_moveto(frac)

    def _on_click(self, event) -> None:
        line = self._line_at_y(event.y)
        self._scroll_to_line(line)
        self.mark_and_focus_line()

    def _on_drag(self, event) -> None:
        line = self._line_at_y(event.y)
        self._scroll_to_line(line)

    def _on_wheel(self, event) -> None:
        self._mark_dirty()

    def mark_and_focus_line(self) -> None:
        """Expose a friendly alias used by the host for click navigation."""
        widget = self._text_widget
        if widget is None:
            return
        try:
            first = int(widget.yview()[0] * (len(self._lines_cache) or 1))
            widget.mark_set("insert", f"{max(1, first + 1)}.0")
            widget.see(f"{max(1, first + 1)}.0")
        except Exception:  # noqa: BLE001
            pass
