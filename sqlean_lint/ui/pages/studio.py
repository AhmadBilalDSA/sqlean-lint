"""SQL Studio page — dual-editor split diff with run-query, auto-fix and minimap.

Composes SplitCodeDiffViewer, CodeMinimap, and the toolbar action strip.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from ..design_system import TOKENS, FONTS, SPACING
from ..code_view import SplitCodeDiffViewer
from ..minimap import CodeMinimap

_CB = TOKENS["BORDER_SUBTLE"]


class StudioPage(ctk.CTkFrame):
    """Primary SQL authoring surface with side-by-side diff viewer."""

    def __init__(
        self,
        master,
        *,
        on_run_query: Optional[Callable] = None,
        on_autofix: Optional[Callable] = None,
        on_format: Optional[Callable] = None,
        on_open_settings: Optional[Callable] = None,
        bg_color: str = "transparent",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, **kwargs)
        self._on_run = on_run_query
        self._on_autofix = on_autofix
        self._on_format = on_format

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_toolbar()
        self._build_editor_area()

    # ── toolbar ────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = ctk.CTkFrame(self, fg_color=TOKENS["CANVAS_TOOLBAR"], height=SPACING["TOOLBAR_HEIGHT"],
                           corner_radius=0)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tb, text="SQL Studio", font=FONTS["TITLE"],
                     text_color=TOKENS["ACCENT_CYAN"]).grid(row=0, column=0, padx=16, sticky="w")

        btn_kw = dict(
            font=FONTS["BODY"], height=SPACING["BTN_HEIGHT"],
            corner_radius=SPACING["RADIUS_SM"],
        )

        ctk.CTkButton(tb, text="Run (F5)", fg_color=TOKENS["ACCENT_CYAN"],
                      text_color=TOKENS["TEXT_ON_ACCENT"],
                      command=self._on_run, **btn_kw).grid(row=0, column=1, padx=4)
        ctk.CTkButton(tb, text="Auto-Fix", fg_color=TOKENS["ACCENT_EMERALD"],
                      text_color=TOKENS["TEXT_ON_ACCENT"],
                      command=self._on_autofix, **btn_kw).grid(row=0, column=2, padx=4)
        ctk.CTkButton(tb, text="Format", fg_color=TOKENS["SURFACE_CARD"],
                      text_color=TOKENS["TEXT_PRIMARY"],
                      command=self._on_format, **btn_kw).grid(row=0, column=3, padx=4)

    # ── editor area ────────────────────────────────────────────────────

    def _build_editor_area(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        self._diff = SplitCodeDiffViewer(
            container,
            left_title="Original",
            right_title="Optimized",
            bg_color=TOKENS["SURFACE_CARD"],
            zone_color=TOKENS["SURFACE_EDITOR"],
            text_color=TOKENS["TEXT_PRIMARY"],
            accent_color=TOKENS["ACCENT_CYAN"],
            ok_color=TOKENS["ACCENT_EMERALD"],
            muted_color=TOKENS["TEXT_MUTED"],
        )
        self._diff.grid(row=0, column=0, sticky="nsew")

        self._minimap = CodeMinimap(
            container,
            editor=self._diff.get_original_pane(),
            width=80,
            bg_color=TOKENS["SURFACE_EDITOR"],
            text_color=TOKENS["TEXT_PRIMARY"],
            muted_color=TOKENS["TEXT_MUTED"],
            accent_color=TOKENS["ACCENT_CYAN"],
        )
        self._minimap.grid(row=0, column=1, sticky="ns")

    # ── public API ─────────────────────────────────────────────────────

    def render(self, original: str, optimized: str) -> None:
        self._diff.render(original, optimized)
        self._minimap.refresh()

    def get_editor(self) -> ctk.CTkTextbox:
        return self._diff.get_original_pane()

    def get_optimized(self) -> ctk.CTkTextbox:
        return self._diff.get_optimized_pane()
