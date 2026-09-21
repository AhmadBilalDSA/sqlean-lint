"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Responsive multi-pane docking layout for sqlean-lint PRO.

Provides:
  * ``DraggableSplitter`` -- a sash widget that resizes adjacent panes
    by tracking ``<B1-Motion>`` between two child frames.
  * ``CollapsibleRail``  -- a side panel that smoothly collapses to 0 px
    while preserving its state and restore button.
  * ``WorkspaceLayout``   -- the full docking surface with a left rail,
    center split-view, bottom drawer, and a tabbed document interface
    (TDI) header.

All colour tokens are read from ``sqlean_lint.ui.design_system.TOKENS``
so the layout participates in runtime theme switching.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from .design_system import TOKENS

# ── Draggable Splitter (Sash) ─────────────────────────────────────────

class DraggableSplitter(ctk.CTkFrame):
    """A thin draggable bar that resizes two adjacent panes.

    Orientation:
        ``"horizontal"`` -- left/right panes (sash is a vertical bar).
        ``"vertical"``   -- top/bottom panes (sash is a horizontal bar).

    Attributes:
        left_frame / top_frame:   The pane before the sash.
        right_frame / bottom_frame: The pane after the sash.
    """

    SASH_THICKNESS = 6
    MIN_PANE = 80

    def __init__(
        self,
        master,
        left_or_top: ctk.CTkFrame,
        right_or_bottom: ctk.CTkFrame,
        *,
        orientation: str = "horizontal",
        **kwargs,
    ) -> None:
        self._orientation = orientation
        super().__init__(master, fg_color=TOKENS["BORDER_SUBTLE"], **kwargs)
        self._left = left_or_top
        self._right = right_or_bottom
        self._dragging = False
        self._start_pos = 0
        self._start_left_size = 0

        cursor = "sb_h_double_arrow" if orientation == "horizontal" else "sb_v_double_arrow"
        self.configure(cursor=cursor, width=self.SASH_THICKNESS if orientation == "horizontal" else 0,
                       height=self.SASH_THICKNESS if orientation == "vertical" else 0)

        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # ── drag lifecycle ─────────────────────────────────────────────────

    def _on_press(self, event) -> None:
        self._dragging = True
        if self._orientation == "horizontal":
            self._start_pos = event.x_root
            self._start_left_size = self._left.winfo_width()
        else:
            self._start_pos = event.y_root
            self._start_left_size = self._left.winfo_height()
        self.configure(fg_color=TOKENS["BORDER_FOCUS"])

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        if self._orientation == "horizontal":
            delta = event.x_root - self._start_pos
            new_size = max(self.MIN_PANE, self._start_left_size + delta)
        else:
            delta = event.y_root - self._start_pos
            new_size = max(self.MIN_PANE, self._start_left_size + delta)

        parent = self.master
        if self._orientation == "horizontal":
            avail = parent.winfo_width()
        else:
            avail = parent.winfo_height()
        new_size = min(new_size, avail - self.MIN_PANE - self.SASH_THICKNESS)
        if new_size < self.MIN_PANE:
            new_size = self.MIN_PANE

        self._apply_size(new_size)

    def _on_release(self, _event) -> None:
        self._dragging = False
        self.configure(fg_color=TOKENS["BORDER_SUBTLE"])

    def _on_enter(self, _event) -> None:
        if not self._dragging:
            self.configure(fg_color=TOKENS["BORDER_STRONG"])

    def _on_leave(self, _event) -> None:
        if not self._dragging:
            self.configure(fg_color=TOKENS["BORDER_SUBTLE"])

    def _apply_size(self, size: int) -> None:
        if self._orientation == "horizontal":
            self._left.configure(width=size)
        else:
            self._left.configure(height=size)


# ── Collapsible Rail ──────────────────────────────────────────────────

class CollapsibleRail(ctk.CTkFrame):
    """A side panel with a collapse/expand toggle button.

    The rail remembers its last pixel width so it can restore exactly.
    """

    def __init__(
        self,
        master,
        *,
        side: str = "left",
        default_width: int = 280,
        min_width: int = 0,
        header_text: str = "",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=TOKENS["PANEL_BG"], corner_radius=0, **kwargs)
        self._side = side
        self._expanded_width = default_width
        self._min_width = min_width
        self._expanded = True

        # Header row with collapse toggle.
        self._header = ctk.CTkFrame(self, fg_color="transparent")
        self._header.pack(fill="x", padx=4, pady=(4, 0))
        self._toggle_btn = ctk.CTkButton(
            self._header,
            text="\u25C0" if side == "left" else "\u25B6",
            width=24, height=24,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="transparent",
            hover_color=TOKENS["HOVER_OVERLAY"],
            text_color=TOKENS["TEXT_SECONDARY"],
            command=self._toggle,
        )
        self._toggle_btn.pack(side="left", padx=2)

        if header_text:
            ctk.CTkLabel(
                self._header,
                text=header_text,
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                text_color=TOKENS["TEXT_SECONDARY"],
            ).pack(side="left", padx=4)

        # Content frame that holds user widgets.
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=4, pady=4)

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _toggle(self) -> None:
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def collapse(self) -> None:
        self._expanded = False
        self._toggle_btn.configure(text="\u25B6" if self._side == "left" else "\u25C0")
        self.configure(width=self._min_width)
        self.pack_propagate(False)
        self.content.pack_forget()

    def expand(self) -> None:
        self._expanded = True
        self._toggle_btn.configure(text="\u25C0" if self._side == "left" else "\u25B6")
        self.configure(width=self._expanded_width)
        self.pack_propagate(True)
        self.content.pack(fill="both", expand=True, padx=4, pady=4)


# ── Tabbed Document Interface ─────────────────────────────────────────

class _TabHeader(ctk.CTkFrame):
    """A strip of closable document tabs at the top of the editor pane.

    Each tab displays the file basename, an unsaved indicator (``*``),
    and a close button (``x``).  Double-clicking empty space creates a
    new tab via the ``on_new_tab`` callback.
    """

    def __init__(
        self,
        master,
        *,
        on_select: Optional[Callable[[str], None]] = None,
        on_close: Optional[Callable[[str], None]] = None,
        on_new_tab: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=TOKENS["PANEL_BG"], corner_radius=0, **kwargs)
        self._on_select = on_select
        self._on_close = on_close
        self._on_new_tab = on_new_tab
        self._tabs: List[str] = []
        self._active: Optional[str] = None
        self._tab_widgets: Dict[str, ctk.CTkFrame] = {}
        self._tab_labels: Dict[str, ctk.CTkLabel] = {}
        self._dirty: Dict[str, bool] = {}

        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", orientation="horizontal", height=34,
        )
        self._scroll.pack(fill="x", padx=4, pady=4)
        self._scroll.pack_propagate(False)

        # Double-click on empty area to open new tab.
        self._scroll.bind("<Double-Button-1>", self._on_double_click)

    # ── public API ─────────────────────────────────────────────────────

    def open(self, path: str) -> None:
        if path in self._tabs:
            self.select(path)
            return
        self._tabs.append(path)
        self._render_tab(path)
        self.select(path)

    def close(self, path: str) -> None:
        if path not in self._tabs:
            return
        idx = self._tabs.index(path)
        self._tabs.pop(idx)
        w = self._tab_widgets.pop(path, None)
        if w is not None and w.winfo_exists():
            w.destroy()
        self._tab_labels.pop(path, None)
        self._dirty.pop(path, None)
        if self._active == path:
            self._active = None
            if self._tabs:
                self.select(self._tabs[min(idx, len(self._tabs) - 1)])
        if self._on_close is not None:
            try:
                self._on_close(path)
            except Exception:  # noqa: BLE001
                pass

    def select(self, path: str) -> None:
        if path not in self._tabs:
            return
        self._active = path
        self._paint()
        if self._on_select is not None:
            try:
                self._on_select(path)
            except Exception:  # noqa: BLE001
                pass

    def active(self) -> Optional[str]:
        return self._active

    def set_dirty(self, path: str, dirty: bool = True) -> None:
        self._dirty[path] = bool(dirty)
        lbl = self._tab_labels.get(path)
        if lbl is not None:
            try:
                lbl.configure(text=self._label_text(path))
            except Exception:  # noqa: BLE001
                pass

    def is_dirty(self, path: str) -> bool:
        return bool(self._dirty.get(path))

    def close_all(self) -> None:
        for p in list(self._tabs):
            self.close(p)

    def open_paths(self) -> List[str]:
        return list(self._tabs)

    # ── internal ───────────────────────────────────────────────────────

    def _on_double_click(self, _event) -> None:
        if self._on_new_tab is not None:
            try:
                self._on_new_tab()
            except Exception:  # noqa: BLE001
                pass

    def _label_text(self, path: str) -> str:
        name = os.path.basename(path)
        return f"* {name}" if self._dirty.get(path) else name

    def _render_tab(self, path: str) -> None:
        frame = ctk.CTkFrame(self._scroll, fg_color="transparent", corner_radius=6)
        label = ctk.CTkLabel(
            frame,
            text=self._label_text(path),
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=TOKENS["TEXT_PRIMARY"],
        )
        label.pack(side="left", padx=(8, 2), pady=4)
        close_btn = ctk.CTkButton(
            frame,
            text="\u00D7",
            width=20, height=18,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="transparent",
            hover_color=TOKENS["HOVER_OVERLAY"],
            text_color=TOKENS["TEXT_MUTED"],
            corner_radius=4,
            command=lambda p=path: self.close(p),
        )
        close_btn.pack(side="left", padx=(0, 4))
        frame.pack(side="left", padx=2, pady=2)
        frame.bind("<Button-1>", lambda _e, p=path: self.select(p))
        label.bind("<Button-1>", lambda _e, p=path: self.select(p))
        self._tab_widgets[path] = frame
        self._tab_labels[path] = label

    def _paint(self) -> None:
        for path, frame in self._tab_widgets.items():
            active = path == self._active
            lbl = self._tab_labels.get(path)
            frame.configure(
                fg_color=TOKENS["SURFACE_ELEVATED"] if active else "transparent"
            )
            if lbl is not None:
                lbl.configure(
                    text_color=TOKENS["ACCENT_PRIMARY"] if active else TOKENS["TEXT_PRIMARY"],
                    font=ctk.CTkFont(
                        "Segoe UI", 11, "bold" if active else "normal"
                    ),
                )


# ── Full Workspace Layout ─────────────────────────────────────────────

class WorkspaceLayout(ctk.CTkFrame):
    """Three-column docking surface with collapsible rails and a bottom
    drawer, topped by a tabbed document interface header.

    Typical usage::

        ws = WorkspaceLayout(root)
        ws.pack(fill="both", expand=True)
        ws.add_left_content(some_frame)
        ws.add_center_content(editor_frame)
        ws.add_bottom_content(terminal_frame)
        ws.open_tab("/path/to/file.sql")
    """

    def __init__(
        self,
        master,
        *,
        on_tab_select: Optional[Callable[[str], None]] = None,
        on_tab_close: Optional[Callable[[str], None]] = None,
        on_new_tab: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=TOKENS["CANVAS_BG"], corner_radius=0, **kwargs)

        # ── top-level vertical layout: TDI header | body | bottom sash | drawer
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # TDI header.
        self._tab_header = _TabHeader(
            self,
            on_select=on_tab_select,
            on_close=on_tab_close,
            on_new_tab=on_new_tab,
        )
        self._tab_header.grid(row=0, column=0, sticky="ew")

        # Body: left rail | h-sash | center | h-sash | right rail.
        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, sticky="nsew")
        self._body.grid_columnconfigure(0, weight=0)
        self._body.grid_columnconfigure(1, weight=0)
        self._body.grid_columnconfigure(2, weight=1)
        self._body.grid_columnconfigure(3, weight=0)
        self._body.grid_columnconfigure(4, weight=0)
        self._body.grid_rowconfigure(0, weight=1)

        # Left rail.
        self._left_rail = CollapsibleRail(
            self._body, side="left", default_width=260,
            header_text="Navigation",
        )
        self._left_rail.grid(row=0, column=0, sticky="ns")

        # Left sash.
        self._left_sash = DraggableSplitter(
            self._body, self._left_rail, self._body,
            orientation="horizontal",
        )
        self._left_sash.grid(row=0, column=1, sticky="ns")

        # Center pane.
        self._center = ctk.CTkFrame(self._body, fg_color="transparent")
        self._center.grid(row=0, column=2, sticky="nsew")

        # Right rail (collapsed by default).
        self._right_rail = CollapsibleRail(
            self._body, side="right", default_width=240,
            header_text="Details",
        )
        self._right_rail.grid(row=0, column=4, sticky="ns")
        self._right_rail.collapse()

        # Right sash.
        self._right_sash = DraggableSplitter(
            self._body, self._right_rail, self._body,
            orientation="horizontal",
        )
        self._right_sash.grid(row=0, column=3, sticky="ns")

        # Bottom drawer (collapsible) with a vertical sash above it.
        # Vertical splitter: body is the "top" pane, drawer is the "bottom".
        self._bottom_drawer = CollapsibleRail(
            self, side="left", default_width=9999,
            header_text="Terminal",
        )
        self._bottom_sash = DraggableSplitter(
            self, self._center, self._bottom_drawer,
            orientation="vertical",
        )
        # Drawer sits in its own row so it can collapse to 0 px.
        self._bottom_drawer.grid(row=2, column=0, sticky="ew")
        self._bottom_sash.grid(row=3, column=0, sticky="ew")

        # Initially collapse bottom drawer (hides sash and drawer).
        self._bottom_drawer.collapse()

    # ── public content API ─────────────────────────────────────────────

    def add_left_content(self, widget: ctk.CTkBaseClass) -> None:
        widget.pack(in_=self._left_rail.content, fill="both", expand=True)

    def add_center_content(self, widget: ctk.CTkBaseClass) -> None:
        widget.pack(in_=self._center, fill="both", expand=True)

    def add_right_content(self, widget: ctk.CTkBaseClass) -> None:
        widget.pack(in_=self._right_rail.content, fill="both", expand=True)

    def add_bottom_content(self, widget: ctk.CTkBaseClass) -> None:
        widget.pack(in_=self._bottom_drawer.content, fill="both", expand=True)

    # ── tab delegation ─────────────────────────────────────────────────

    def open_tab(self, path: str) -> None:
        self._tab_header.open(path)

    def close_tab(self, path: str) -> None:
        self._tab_header.close(path)

    def close_all_tabs(self) -> None:
        self._tab_header.close_all()

    def active_tab(self) -> Optional[str]:
        return self._tab_header.active()

    def set_tab_dirty(self, path: str, dirty: bool = True) -> None:
        self._tab_header.set_dirty(path, dirty)

    # ── rail visibility ────────────────────────────────────────────────

    def toggle_left_rail(self) -> None:
        if self._left_rail.is_expanded:
            self._left_rail.collapse()
        else:
            self._left_rail.expand()

    def toggle_right_rail(self) -> None:
        if self._right_rail.is_expanded:
            self._right_rail.collapse()
        else:
            self._right_rail.expand()

    def toggle_bottom_drawer(self) -> None:
        if self._bottom_drawer.is_expanded:
            self._bottom_drawer.collapse()
        else:
            self._bottom_drawer.expand()
