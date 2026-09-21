"""File Explorer page — tree view with batch-lint actions.

Wraps InteractiveFileExplorer with a dedicated toolbar and status strip.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

import customtkinter as ctk

from ..design_system import TOKENS, FONTS, SPACING


class ExplorerPage(ctk.CTkFrame):
    """Dedicated file-explorer page with browse / scan / clear actions."""

    def __init__(
        self,
        master,
        *,
        on_browse: Optional[Callable] = None,
        on_scan: Optional[Callable] = None,
        on_clear: Optional[Callable] = None,
        bg_color: str = "transparent",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, **kwargs)
        self._on_browse = on_browse
        self._on_scan = on_scan
        self._on_clear = on_clear

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_toolbar()
        self._build_file_tree()

    # ── toolbar ────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = ctk.CTkFrame(self, fg_color=TOKENS["CANVAS_TOOLBAR"], height=SPACING["TOOLBAR_HEIGHT"],
                           corner_radius=0)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tb, text="File Explorer", font=FONTS["TITLE"],
                     text_color=TOKENS["ACCENT_EMERALD"]).grid(row=0, column=0, padx=16, sticky="w")

        btn_kw = dict(font=FONTS["BODY"], height=SPACING["BTN_HEIGHT"],
                      corner_radius=SPACING["RADIUS_SM"])

        ctk.CTkButton(tb, text="Browse", fg_color=TOKENS["ACCENT_CYAN"],
                      text_color=TOKENS["TEXT_ON_ACCENT"],
                      command=self._on_browse, **btn_kw).grid(row=0, column=1, padx=4)
        ctk.CTkButton(tb, text="Scan Dir", fg_color=TOKENS["ACCENT_EMERALD"],
                      text_color=TOKENS["TEXT_ON_ACCENT"],
                      command=self._on_scan, **btn_kw).grid(row=0, column=2, padx=4)
        ctk.CTkButton(tb, text="Clear", fg_color=TOKENS["SURFACE_CARD"],
                      text_color=TOKENS["TEXT_MUTED"],
                      command=self._on_clear, **btn_kw).grid(row=0, column=3, padx=4)

    # ── file tree ──────────────────────────────────────────────────────

    def _build_file_tree(self) -> None:
        self._tree_frame = ctk.CTkScrollableFrame(
            self, fg_color=TOKENS["CANVAS_SIDEBAR"],
            corner_radius=0,
        )
        self._tree_frame.grid(row=2, column=0, sticky="nsew", padx=0)

        self._file_labels: List[ctk.CTkLabel] = []

    # ── public API ─────────────────────────────────────────────────────

    def set_files(self, paths: List[Path]) -> None:
        for lbl in self._file_labels:
            lbl.destroy()
        self._file_labels.clear()

        for idx, path in enumerate(paths):
            lbl = ctk.CTkLabel(
                self._tree_frame,
                text=f"  {path.name}",
                font=FONTS["CODE"],
                text_color=TOKENS["TEXT_PRIMARY"],
                anchor="w",
            )
            lbl.grid(row=idx, column=0, sticky="ew", padx=8, pady=2)
            self._file_labels.append(lbl)

    def clear(self) -> None:
        for lbl in self._file_labels:
            lbl.destroy()
        self._file_labels.clear()
