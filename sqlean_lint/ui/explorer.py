"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

KNIME/Excel-style interactive left navigation for the sqlean-lint PRO GUI.

:class:`InteractiveFileExplorer` renders collapsible accordion sections
("Active Directory", "Recent Files", "Violations Found") where each row is a
clickable element. Clicking a file row triggers a registered ``on_select``
callback so the main panel can update its view — mirroring the KNIME left-nav
interaction model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from .animations import CTkAnimationEngine, SlidePanel


class _AccordionSection(ctk.CTkFrame):
    """A single collapsible section with a header toggle and a body container."""

    def __init__(
        self,
        master: Any,
        title: str,
        *,
        on_toggle: Optional[Callable[[str, bool], None]] = None,
        bg_color: str = "#24283b",
        zone_color: str = "#1f2335",
        accent_color: str = "#7aa2f7",
        text_color: str = "#c0caf5",
        muted_color: str = "#565f89",
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._title = title
        self._on_toggle = on_toggle
        self._zone = zone_color
        self._accent = accent_color
        self._text_color = text_color
        self._muted = muted_color
        self._expanded = True

        self.grid_columnconfigure(0, weight=1)

        # Header (clickable).
        self._header = ctk.CTkFrame(self, fg_color=zone_color, corner_radius=8)
        self._header.grid(row=0, column=0, sticky="ew", pady=(2, 4))
        self._header.grid_columnconfigure(0, weight=1)
        self._header.bind("<Button-1>", lambda _e: self.toggle())
        self._header.bind("<Return>", lambda _e: self.toggle())

        self._carat = ctk.CTkLabel(
            self._header,
            text="▾",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=accent_color,
        )
        self._carat.grid(row=0, column=0, padx=(8, 4), pady=6, sticky="w")

        self._title_label = ctk.CTkLabel(
            self._header,
            text=title,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=text_color,
            anchor="w",
        )
        self._title_label.grid(row=0, column=1, sticky="ew", pady=6)
        self._title_label.bind("<Button-1>", lambda _e: self.toggle())

        self._count_label = ctk.CTkLabel(
            self._header,
            text="",
            font=ctk.CTkFont("Consolas", 10),
            text_color=muted_color,
        )
        self._count_label.grid(row=0, column=2, padx=(4, 8), pady=6)

        # Body (rows scroll inside a fixed-height frame).
        self._body = SlidePanel(
            self,
            fg_color="transparent",
            open_width=1,
            closed_width=1,
            duration_ms=220,
        )
        self._body.grid(row=1, column=0, sticky="ew")
        self._body_canvas = ctk.CTkCanvas(
            self._body, height=0, bg=zone_color, highlightthickness=0
        )
        self._rows_host = ctk.CTkFrame(self, fg_color="transparent")

    # ── public API ─────────────────────────────────────────────────────

    def set_count(self, count: int) -> None:
        self._count_label.configure(text=str(count))

    @property
    def expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._carat.configure(text="▸" if not self._expanded else "▾")
        if self._on_toggle is not None:
            self._on_toggle(self._title, self._expanded)

    def add_row(
        self,
        label: str,
        path: Optional[str] = None,
        *,
        on_click: Optional[Callable[[str], None]] = None,
        style: str = "file",
        extra: str = "",
    ) -> "ctk.CTkButton":
        """Append a clickable row to the section body.

        Args:
            label: Display text (usually the file name).
            path: Absolute path to pass to ``on_click``.
            on_click: Callback invoked with *path* on click.
            style: One of ``"file"``, ``"badge"``, ``"dir"``, ``"error"``.
            extra: Optional right-hand metadata (e.g. violation count).
        """
        host = self._create_host()
        host.grid_columnconfigure(0, weight=1)

        text_color = {
            "file": self._text_color,
            "badge": self._accent,
            "dir": self._text_color,
            "error": "#f7768e",
        }.get(style, self._text_color)

        prefix = {"dir": "▸ ", "badge": "● ", "error": "✖ ", "file": ""}.get(
            style, ""
        )
        display = f"{prefix}{label}"

        def _clicked(_e=None) -> None:
            if on_click is not None:
                on_click(path or label)

        btn = ctk.CTkButton(
            host,
            text=display,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color="transparent",
            hover_color="#2a2f45",
            text_color=text_color,
            anchor="w",
            corner_radius=6,
            command=_clicked,
        )
        btn.grid(row=0, column=0, sticky="ew", padx=2, pady=1)

        if extra:
            ctk.CTkLabel(
                host,
                text=extra,
                font=ctk.CTkFont("Consolas", 9),
                text_color=self._muted,
            ).grid(row=0, column=1, padx=(4, 6))

        return btn

    def clear_rows(self) -> None:
        for child in self._rows_host.winfo_children():
            child.destroy()

    def _create_host(self) -> ctk.CTkFrame:
        host = ctk.CTkFrame(self._rows_host, fg_color="transparent")
        host.pack(fill="x", padx=4, pady=1)
        return host


class InteractiveFileExplorer(ctk.CTkFrame):
    """Accordion-based left navigation for scanning and file selection.

    Args:
        master: Parent widget.
        on_select: Callback invoked with ``(section_name, path)`` when a file
            row is clicked.
        on_toggle: Callback invoked when an accordion section is expanded or
            collapsed ``(section_name, expanded)``.
    """

    def __init__(
        self,
        master: Any,
        *,
        on_select: Optional[Callable[[str, str], None]] = None,
        on_toggle: Optional[Callable[[str, bool], None]] = None,
        bg_color: str = "#24283b",
        zone_color: str = "#1f2335",
        accent_color: str = "#7aa2f7",
        text_color: str = "#c0caf5",
        muted_color: str = "#565f89",
        width: int = 300,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=12, width=width)
        self._bg = bg_color
        self._zone = zone_color
        self._accent = accent_color
        self._text_color = text_color
        self._muted = muted_color
        self._on_select = on_select
        self._on_toggle = on_toggle
        self._width = max(180, int(width))

        self._rows: Dict[str, List[Any]] = {}
        self._selected_path: Optional[str] = None

        self.grid_propagate(False)
        self.grid_rowconfigure(3, weight=1)

        self._build_logo()
        self._sections: Dict[str, _AccordionSection] = {}
        self._build_sections()

    # ── layout ─────────────────────────────────────────────────────────

    def _build_logo(self) -> None:
        logo = ctk.CTkLabel(
            self,
            text="SQLean Lint Pro",
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=self._accent,
        )
        logo.grid(row=0, column=0, padx=12, pady=(14, 8), sticky="w")

        sub = ctk.CTkLabel(
            self,
            text="Enterprise Workspace",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=self._muted,
        )
        sub.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")

    def _build_sections(self) -> None:
        section_host = ctk.CTkScrollableFrame(
            self, fg_color=self._bg, corner_radius=0
        )
        section_host.grid(row=3, column=0, sticky="nsew", padx=2, pady=(4, 2))

        for title in ("Active Directory", "Recent Files", "Violations Found"):
            section = _AccordionSection(
                section_host,
                title,
                on_toggle=self._on_toggle,
                bg_color=self._bg,
                zone_color=self._zone,
                accent_color=self._accent,
                text_color=self._text_color,
                muted_color=self._muted,
            )
            section.pack(fill="x", padx=2, pady=2)
            self._sections[title] = section
            self._rows[title] = []

    # ── public API ─────────────────────────────────────────────────────

    def add_file(
        self,
        section: str,
        name: str,
        path: str,
        *,
        style: str = "file",
        extra: str = "",
    ) -> None:
        """Add a clickable file (or directory) row under *section*."""
        section_widget = self._sections.get(section)
        if section_widget is None:
            return

        def _click(clicked_path: str) -> None:
            self._selected_path = clicked_path
            if self._on_select is not None:
                self._on_select(section, clicked_path)

        section_widget.add_row(
            name,
            path,
            on_click=_click,
            style=style,
            extra=extra,
        )
        self._rows[section].append(path)
        section_widget.set_count(len(self._rows[section]))

    def set_directory(self, directory: Path, file_names: List[str]) -> None:
        """Render the "Active Directory" section with *file_names*."""
        section = "Active Directory"
        self.clear_section(section)
        for fname in file_names:
            self.add_file(section, fname, str(directory / fname))

    def add_recent(self, path: Path) -> None:
        self.add_file("Recent Files", path.name, str(path))

    def add_violation(
        self, path: str, rule_id: str, severity: str, count: int = 1
    ) -> None:
        style = "error" if severity.upper() in ("CRITICAL", "HIGH") else "badge"
        self.add_file(
            "Violations Found",
            f"{Path(path).name} · {rule_id}",
            path,
            style=style,
            extra=str(count),
        )

    def clear_section(self, section: str) -> None:
        widget = self._sections.get(section)
        if widget is not None:
            widget.clear_rows()
            self._rows[section] = []
            widget.set_count(0)

    def clear_all(self) -> None:
        for section in self._sections:
            self.clear_section(section)

    @property
    def selected_path(self) -> Optional[str]:
        return self._selected_path
