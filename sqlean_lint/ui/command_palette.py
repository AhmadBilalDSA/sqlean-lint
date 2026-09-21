# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA) | kierninja@gmail.com
# All Rights Reserved.

"""Spotlight command palette for the sqlean-lint PRO GUI.

A floating, borderless/card modal search overlay modelled after VS Code's
``Ctrl+Shift+P`` (and bound additionally to ``Ctrl+K``):

* A top text input performs fuzzy search-as-you-type filtering against every
  registered application command (titles, descriptions and action families).
* Full keyboard navigation (``Up`` / ``Down`` / ``Enter`` / ``Escape``) with
  the active row highlighted via ``TOKENS["SELECTION_BG"]``.
* ``Enter`` runs the highlighted command; ``Escape`` dismisses the overlay.
* Card dimensions: 580x360, centered horizontally in the upper third.

Headless-safe: the palette only materializes a ``CTkToplevel`` when shown.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from .animations import CTkAnimationEngine, FadeWidget
from .design_system import TOKENS


class Command:
    """A single palette entry combining a label, shortcut hint and action."""

    __slots__ = ("label", "hint", "callback", "family")

    def __init__(
        self,
        label: str,
        callback: Callable[[], None],
        hint: str = "",
        family: str = "General",
    ) -> None:
        self.label = label
        self.callback = callback
        self.hint = hint
        self.family = family


# ── lightweight fuzzy (subsequence) matcher ───────────────────────────

def _fuzzy_score(query: str, text: str) -> Optional[int]:
    """Return a match score if *query* is a subsequence of *text*, else None.

    Lower scores are better. A query that appears contiguously scores higher
    (fewer "gaps") than a scattershot match. Case-insensitive.
    """
    query = query.lower()
    text = text.lower()
    if not query:
        return 0
    ti, score, gap_penalty = 0, 0, 0
    matched = 0
    for qc in query:
        found = text.find(qc, ti)
        if found == -1:
            return None
        if found != ti:
            gap_penalty += 1
        score += found - ti
        ti = found + 1
        matched += 1
    return score + gap_penalty * 3


class CommandPalette:
    """A reusable command palette overlay attached to a *root* window."""

    def __init__(
        self,
        root: Any,
        commands: Optional[List[Command]] = None,
        animation_engine: Optional[CTkAnimationEngine] = None,
    ) -> None:
        self._root = root
        self._commands: List[Command] = list(commands or [])
        self._anim = animation_engine
        self._window: Optional[ctk.CTkToplevel] = None
        # Lazily-bound widget refs.
        self._entry = None
        self._list_frame = None
        self._row_widgets: List[Any] = []
        self._selected: int = 0
        self._filtered: List[Command] = []

    # ── registry ───────────────────────────────────────────────────────

    def add_command(self, command: Command) -> None:
        """Register *command* so it appears in the palette."""
        self._commands.append(command)

    def add_commands(self, commands: List[Command]) -> None:
        """Register many commands at once."""
        self._commands.extend(commands)

    def register_task_commands(
        self,
        *,
        lint: Optional[Callable[[], None]] = None,
        autofix: Optional[Callable[[], None]] = None,
        run_query: Optional[Callable[[], None]] = None,
        switch_dialect: Optional[Dict[str, Callable[[], None]]] = None,
        export_pdf: Optional[Callable[[], None]] = None,
        export_xlsx: Optional[Callable[[], None]] = None,
        switch_theme: Optional[Callable[[], None]] = None,
        clear_cache: Optional[Callable[[], None]] = None,
    ) -> None:
        """Register the standard task commands referenced by the spec."""
        if lint is not None:
            self.add_command(Command("Lint Active Buffer", lint, "Ctrl+Enter", "Scan"))
        if autofix is not None:
            self.add_command(Command("Auto-Fix Lint Errors", autofix, "Ctrl+R", "Edit"))
        if run_query is not None:
            self.add_command(Command("Run DuckDB Query", run_query, "F5", "Data"))
        for name, cb in (switch_dialect or {}).items():
            self.add_command(Command(f"Switch Dialect \u2192 {name}", cb, "", "Config"))
        if export_pdf is not None:
            self.add_command(Command("Export Report (PDF)", export_pdf, "", "Report"))
        if export_xlsx is not None:
            self.add_command(Command("Export Report (XLSX)", export_xlsx, "", "Report"))
        if switch_theme is not None:
            self.add_command(Command("Switch Theme...", switch_theme, "", "Config"))
        if clear_cache is not None:
            self.add_command(Command("Clear Cache", clear_cache, "", "General"))

    def register_ide_actions(
        self,
        *,
        new_query_tab: Optional[Callable[[], None]] = None,
        open_sql_file: Optional[Callable[[], None]] = None,
        save_active_query: Optional[Callable[[], None]] = None,
        clear_workspace: Optional[Callable[[], None]] = None,
        run_query: Optional[Callable[[], None]] = None,
        lint_buffer: Optional[Callable[[], None]] = None,
        auto_fix_all: Optional[Callable[[], None]] = None,
        toggle_nav_rail: Optional[Callable[[], None]] = None,
        toggle_diagnostics: Optional[Callable[[], None]] = None,
        toggle_ast_canvas: Optional[Callable[[], None]] = None,
        switch_duckdb: Optional[Callable[[], None]] = None,
        switch_tsql: Optional[Callable[[], None]] = None,
        switch_postgres: Optional[Callable[[], None]] = None,
        switch_snowflake: Optional[Callable[[], None]] = None,
        apply_vercel_dark: Optional[Callable[[], None]] = None,
        apply_nord_slate: Optional[Callable[[], None]] = None,
        apply_high_contrast: Optional[Callable[[], None]] = None,
    ) -> None:
        """Register the full IDE action set specified by the Phase 2 spec."""
        # File
        if new_query_tab is not None:
            self.add_command(
                Command("New Query Tab", new_query_tab, "Ctrl+T", "File"))
        if open_sql_file is not None:
            self.add_command(
                Command("Open SQL File", open_sql_file, "Ctrl+O", "File"))
        if save_active_query is not None:
            self.add_command(
                Command("Save Active Query", save_active_query, "Ctrl+S", "File"))
        if clear_workspace is not None:
            self.add_command(
                Command("Clear Workspace", clear_workspace, "", "File"))

        # Engine
        if run_query is not None:
            self.add_command(
                Command("Run Local DuckDB Query", run_query, "F5", "Engine"))
        if lint_buffer is not None:
            self.add_command(
                Command("Lint Current Buffer", lint_buffer, "Ctrl+Enter", "Engine"))
        if auto_fix_all is not None:
            self.add_command(
                Command("Auto-Fix All Rules", auto_fix_all, "Ctrl+R", "Engine"))

        # View
        if toggle_nav_rail is not None:
            self.add_command(
                Command("Toggle Navigation Rail", toggle_nav_rail, "", "View"))
        if toggle_diagnostics is not None:
            self.add_command(
                Command("Toggle Diagnostics Drawer", toggle_diagnostics,
                        "", "View"))
        if toggle_ast_canvas is not None:
            self.add_command(
                Command("Toggle AST Lineage Canvas", toggle_ast_canvas,
                        "", "View"))

        # Dialects
        if switch_duckdb is not None:
            self.add_command(
                Command("Switch to DuckDB", switch_duckdb, "", "Dialects"))
        if switch_tsql is not None:
            self.add_command(
                Command("Switch to T-SQL", switch_tsql, "", "Dialects"))
        if switch_postgres is not None:
            self.add_command(
                Command("Switch to Postgres", switch_postgres, "", "Dialects"))
        if switch_snowflake is not None:
            self.add_command(
                Command("Switch to Snowflake", switch_snowflake, "", "Dialects"))

        # Themes
        if apply_vercel_dark is not None:
            self.add_command(
                Command("Apply Vercel Dark", apply_vercel_dark, "", "Themes"))
        if apply_nord_slate is not None:
            self.add_command(
                Command("Apply Nord Slate", apply_nord_slate, "", "Themes"))
        if apply_high_contrast is not None:
            self.add_command(
                Command("Apply High Contrast", apply_high_contrast,
                        "", "Themes"))

    # ── public API ─────────────────────────────────────────────────────

    def show(self) -> None:
        """Materialize and focus the floating palette."""
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            self._window.focus_force()
            self._refresh(self._entry.get())
            return
        self._build_window()
        self._refresh(filter_text="")
        self._entry.focus_set()

    def hide(self) -> None:
        """Dismiss the palette immediately."""
        if self._window is not None and self._window.winfo_exists():
            self._window.destroy()
        self._window = None

    def is_visible(self) -> bool:
        return self._window is not None and self._window.winfo_exists()

    def toggle(self) -> None:
        """Toggle palette visibility (used by the ``Ctrl+K`` binding)."""
        if self.is_visible():
            self.hide()
        else:
            self.show()

    # ── construction ───────────────────────────────────────────────────

    def _build_window(self) -> None:
        win = ctk.CTkToplevel(self._root)
        win.title("Command Palette")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(
            fg_color=TOKENS["SURFACE_MODAL"],
            border_width=1,
            border_color=TOKENS["BORDER_STRONG"],
            corner_radius=10,
        )
        win.resizable(False, False)

        width = 580
        height = 360
        try:
            root_w = self._root.winfo_width()
            root_h = self._root.winfo_height()
            x = self._root.winfo_rootx() + (root_w - width) // 2
            y = self._root.winfo_rooty() + int(root_h * 0.25) - height // 2
        except Exception:  # noqa: BLE001 - headless / not-yet-mapped
            x, y = 200, 100
        win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        entry = ctk.CTkEntry(
            win,
            placeholder_text="Type a command or search actions...",
            font=ctk.CTkFont("Segoe UI", 13),
            fg_color=TOKENS["PANEL_BG"],
            border_color=TOKENS["BORDER_FOCUS"],
            text_color=TOKENS["TEXT_PRIMARY"],
            border_width=1,
            corner_radius=6,
        )
        entry.pack(fill="x", padx=12, pady=(12, 8))
        entry.bind("<KeyRelease>", self._on_key)
        entry.bind("<Return>", lambda e: self._run_selected())
        entry.bind("<Up>", lambda e: self._move(-1))
        entry.bind("<Down>", lambda e: self._move(1))
        entry.bind("<Escape>", lambda e: self.hide())

        frame = ctk.CTkFrame(
            win,
            fg_color=TOKENS["PANEL_BG"],
            corner_radius=8,
            border_width=1,
            border_color=TOKENS["BORDER_SUBTLE"],
        )
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        lists = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        lists.pack(fill="both", expand=True)
        lists.pack_propagate(False)

        self._window = win
        self._entry = entry
        self._list_frame = lists
        self._row_widgets = []

    # ── interactions ───────────────────────────────────────────────────

    def _on_key(self, _event) -> None:
        self._refresh(filter_text=self._entry.get())

    def _move(self, delta: int) -> None:
        if not self._filtered:
            return
        total = len(self._filtered)
        self._selected = (self._selected + delta) % total
        self._scroll_selected_into_view()
        self._paint_selection()

    def _run_selected(self) -> None:
        if not self._filtered:
            return
        cmd = self._filtered[min(self._selected, len(self._filtered) - 1)]
        self.hide()
        try:
            cmd.callback()
        except Exception:  # noqa: BLE001 - never let a command crash the GUI
            import traceback

            traceback.print_exc()

    # ── rendering ──────────────────────────────────────────────────────

    def _filter(self, text: str) -> List[Command]:
        q = text.strip().strip(">").strip().lower()
        if not q:
            return list(self._commands)
        scored: List[tuple] = []
        for c in self._commands:
            label_score = _fuzzy_score(q, c.label)
            hint_score = _fuzzy_score(q, c.hint)
            family_score = _fuzzy_score(q, c.family)
            candidates = [s for s in (label_score, hint_score, family_score)
                          if s is not None]
            if not candidates:
                continue
            scored.append((min(candidates), c))
        scored.sort(key=lambda t: t[0])
        return [c for _, c in scored]

    def _refresh(self, filter_text: str) -> None:
        if self._list_frame is None:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()
        self._row_widgets = []
        self._filtered = self._filter(filter_text)
        self._selected = 0

        if not self._filtered:
            empty = ctk.CTkLabel(
                self._list_frame,
                text="No matching commands",
                font=ctk.CTkFont("Segoe UI", 13),
                text_color=TOKENS["TEXT_MUTED"],
            )
            empty.pack(anchor="w", padx=12, pady=12)
            return

        for i, cmd in enumerate(self._filtered):
            row = ctk.CTkFrame(
                self._list_frame, fg_color="transparent", corner_radius=6
            )
            row.pack(fill="x", padx=4, pady=2)
            row.grid_columnconfigure(0, weight=1)

            label = ctk.CTkLabel(
                row,
                text=cmd.label,
                font=ctk.CTkFont("Segoe UI", 13),
                text_color=TOKENS["TEXT_PRIMARY"],
                anchor="w",
            )
            label.grid(row=0, column=0, padx=8, pady=4, sticky="w")
            label.bind("<Button-1>", lambda _e, idx=i: self._select_and_run(idx))
            row.bind("<Button-1>", lambda _e, idx=i: self._select_and_run(idx))
            row.bind("<Enter>", lambda _e, idx=i: self._hover(idx))

            hint = ctk.CTkLabel(
                row,
                text=cmd.hint,
                font=ctk.CTkFont("Consolas", 11),
                text_color=TOKENS["TEXT_MUTED"],
                anchor="e",
            )
            hint.grid(row=0, column=1, padx=8, pady=4, sticky="e")

            family = ctk.CTkLabel(
                row,
                text=cmd.family,
                font=ctk.CTkFont("Segoe UI", 10),
                text_color=TOKENS["TEXT_MUTED"],
                anchor="e",
            )
            family.grid(row=0, column=2, padx=(0, 4), pady=4, sticky="e")

            if self._anim is not None and hasattr(FadeWidget, "bind_hover"):
                FadeWidget.bind_hover(row, TOKENS["SELECTION_BG"], self._anim)

            self._row_widgets.append((row, label))

        self._paint_selection()

    def _hover(self, idx: int) -> None:
        if 0 <= idx < len(self._row_widgets):
            self._selected = idx
            self._paint_selection()

    def _select_and_run(self, idx: int) -> None:
        self._selected = idx % len(self._filtered) if self._filtered else 0
        self._paint_selection()
        self._run_selected()

    def _scroll_selected_into_view(self) -> None:
        if self._list_frame is None:
            return
        try:
            frame = getattr(self._list_frame, "_parent_frame", None) or self._list_frame
            if hasattr(frame, "see"):
                frame.see(self._selected)
        except Exception:  # noqa: BLE001
            pass

    def _paint_selection(self) -> None:
        for i, (row, label) in enumerate(self._row_widgets):
            active = i == self._selected
            row.configure(fg_color=TOKENS["SELECTION_BG"] if active else "transparent")
            label.configure(
                text_color=TOKENS["ACCENT_PRIMARY"] if active else TOKENS["TEXT_PRIMARY"],
                font=ctk.CTkFont("Segoe UI", 13, "bold" if active else "normal"),
            )
