"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Multi-tab file management for the sqlean-lint PRO GUI.

A dynamic tabbed navigation bar (VS Code style) that manages and switches
between multiple open SQL files simultaneously. Each tab shows the file
name plus a close button; clicking a tab selects it and invokes an
``on_select`` callback. Switching tabs updates an ``on_change`` callback so
the dashboard can reload the editor/preview for the active document.

The widget only builds ``CTkFrame``/``CTkButton`` children; it never starts
``mainloop`` so it is safe to import in headless QA.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import customtkinter as ctk

# ── Design tokens (mirror the dashboard palette) ──────────────────────
_SURFACE = "#24283b"
_ZONE = "#1f2335"
_ACCENT = "#7aa2f7"
_TEXT = "#c0caf5"
_MUTED = "#565f89"
_ACTIVE_TAB = "#3b4261"


class CTkTabBar(ctk.CTkFrame):
    """A horizontally scrollable tab bar of open files.

    Attributes:
        on_select: Called with the selected ``path`` when a tab is clicked.
        on_close:  Called with the closed ``path`` when a tab's X is hit.
        on_change: Called with the newly active ``path`` after switch.
    """

    def __init__(
        self,
        master,
        *,
        bg_color: str = _SURFACE,
        zone_color: str = _ZONE,
        accent_color: str = _ACCENT,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        on_select: Optional[Callable[[str], None]] = None,
        on_close: Optional[Callable[[str], None]] = None,
        on_change: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8, **kwargs)
        self._zone = zone_color
        self._accent = accent_color
        self._text = text_color
        self._muted = muted_color
        self._on_select = on_select
        self._on_close = on_close
        self._on_change = on_change

        self._tabs: List[str] = []
        self._active: Optional[str] = None
        self._tab_widgets: dict = {}
        self._tab_labels: dict = {}
        # Per-path dirty flag; modified tabs render a leading "*".
        self._dirty: dict = {}

        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", orientation="horizontal", height=38
        )
        self._scroll.pack(fill="x", padx=4, pady=4)
        self._scroll.pack_propagate(False)

    # ── public API ─────────────────────────────────────────────────────

    def open(self, path: str) -> None:
        """Open (or focus) the tab for *path*; makes it the active tab."""
        if path in self._tabs:
            self.select(path)
            return
        self._tabs.append(path)
        self._render_tab(path)
        self.select(path)

    def close(self, path: str) -> None:
        """Close the tab for *path*; focuses a sensible neighbour."""
        if path not in self._tabs:
            return
        idx = self._tabs.index(path)
        self._tabs.pop(idx)
        widget = self._tab_widgets.pop(path, None)
        if widget is not None and widget.winfo_exists():
            widget.destroy()
        self._tab_labels.pop(path, None)

        if self._active == path:
            self._active = None
            if self._tabs:
                next_idx = min(idx, len(self._tabs) - 1)
                self.select(self._tabs[next_idx])

        if self._on_close is not None:
            try:
                self._on_close(path)
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()

        if not self._tabs:
            if self._on_change is not None:
                try:
                    self._on_change("")
                except Exception:  # noqa: BLE001
                    import traceback

                    traceback.print_exc()

    def select(self, path: str) -> None:
        """Make *path* the active tab (no-op if not open)."""
        if path not in self._tabs or path == self._active:
            if path not in self._tabs:
                return
            self._paint()
        self._active = path
        self._paint()
        if self._on_select is not None:
            try:
                self._on_select(path)
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()
        if self._on_change is not None:
            try:
                self._on_change(path)
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()

    def active(self) -> Optional[str]:
        """Return the currently active tab path (or ``None``)."""
        return self._active

    def open_paths(self) -> List[str]:
        """Return the ordered list of open tab paths."""
        return list(self._tabs)

    def close_all(self) -> None:
        """Close every tab."""
        for path in list(self._tabs):
            self.close(path)

    # ── rendering ──────────────────────────────────────────────────────

    def _display_name(self, path: str) -> str:
        import os

        return os.path.basename(path)

    def _render_tab(self, path: str) -> None:
        frame = ctk.CTkFrame(self._scroll, fg_color="transparent", corner_radius=6)
        label = ctk.CTkLabel(
            frame,
            text=self._label_text(path),
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=self._text,
        )
        label.pack(side="left", padx=(10, 2), pady=4)

        close_btn = ctk.CTkButton(
            frame,
            text=" x ",
            width=22,
            height=20,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="transparent",
            hover_color=self._zone,
            text_color=self._muted,
            corner_radius=4,
            command=lambda p=path: self.close(p),
        )
        close_btn.pack(side="left", padx=(0, 4))

        frame.pack(side="left", padx=3, pady=2)
        frame.bind("<Button-1>", lambda _e, p=path: self.select(p))
        label.bind("<Button-1>", lambda _e, p=path: self.select(p))
        frame.grid_propagate(False)

        self._tab_widgets[path] = frame
        self._tab_labels[path] = label

    def _label_text(self, path: str) -> str:
        name = self._display_name(path)
        if self._dirty.get(path):
            name = f"* {name}"
        return name

    def set_dirty(self, path: str, dirty: bool = True) -> None:
        """Mark (or clear) *path* as modified; renders a "*" prefix."""
        self._dirty[path] = bool(dirty)
        label = self._tab_labels.get(path)
        if label is not None:
            try:
                label.configure(text=self._label_text(path))
            except Exception:  # noqa: BLE001
                pass

    def is_dirty(self, path: str) -> bool:
        return bool(self._dirty.get(path))

    def dirty_paths(self) -> List[str]:
        return [p for p, d in self._dirty.items() if d]

    def _paint(self) -> None:
        for path, frame in self._tab_widgets.items():
            active = path == self._active
            label = self._tab_labels.get(path)
            frame.configure(fg_color=_ACTIVE_TAB if active else "transparent")
            if label is not None:
                label.configure(
                    text_color=self._accent if active else self._text,
                    font=ctk.CTkFont(
                        "Segoe UI", 12, "bold" if active else "normal"
                    ),
                )
