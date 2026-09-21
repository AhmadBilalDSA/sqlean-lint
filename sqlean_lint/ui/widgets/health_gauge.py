"""Vectorized high-resolution circular health score gauge.

Renders a percentage arc (0-100) with colour-coded tiers:
  * >= 90 : Emerald / Grade A
  * >= 70 : Amber / Needs Audit
  * < 70  : Rose / Critical Risk
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

import customtkinter as ctk

from ..design_system import TOKENS, FONTS, SPACING


class HealthScoreMeter(ctk.CTkFrame):
    """High-resolution interactive circular gauge for the SQL health score."""

    def __init__(
        self,
        parent,
        size: int = 180,
        *,
        bg_color: str = "transparent",
        **kwargs,
    ) -> None:
        super().__init__(parent, fg_color=bg_color, **kwargs)
        self._size = size
        self._score = 0
        self._letter = ""

        self._canvas = tk.Canvas(
            self,
            width=size,
            height=size,
            bg=TOKENS["CANVAS_SIDEBAR"],
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(padx=SPACING["PAD_SM"], pady=SPACING["PAD_SM"])

        self.set_score(0)

    # ── public API ─────────────────────────────────────────────────────

    def set_score(self, score: int) -> None:
        """Redraw the gauge arc and centre label for *score* (0-100)."""
        self._score = max(0, min(100, score))
        self._letter = self._grade_letter(self._score)
        self._repaint()

    def score(self) -> int:
        return self._score

    def letter(self) -> str:
        return self._letter

    # ── internal ───────────────────────────────────────────────────────

    @staticmethod
    def _grade_letter(score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    @staticmethod
    def _tier(score: int) -> tuple:
        if score >= 90:
            return TOKENS["ACCENT_EMERALD"], "GRADE A"
        if score >= 70:
            return TOKENS["ACCENT_AMBER"], "NEEDS AUDIT"
        return TOKENS["ACCENT_ROSE"], "CRITICAL RISK"

    def _repaint(self) -> None:
        c = self._canvas
        c.delete("all")
        sz = self._size
        pad = 18

        accent, label = self._tier(self._score)

        # Background ring
        c.create_oval(
            pad, pad, sz - pad, sz - pad,
            outline=TOKENS["BORDER_SUBTLE"], width=12,
        )

        # Foreground arc (clockwise from 12 o'clock)
        extent = -max(1, (self._score / 100.0) * 359.9)
        c.create_arc(
            pad, pad, sz - pad, sz - pad,
            start=90, extent=extent,
            outline=accent, width=12, style="arc",
        )

        # Centre percentage
        c.create_text(
            sz // 2, sz // 2 - 10,
            text=f"{self._score}%",
            fill=TOKENS["TEXT_PRIMARY"],
            font=FONTS["TITLE"],
        )

        # Grade letter
        c.create_text(
            sz // 2, sz // 2 + 12,
            text=self._letter,
            fill=accent,
            font=FONTS["SUBTITLE"],
        )

        # Tier label
        c.create_text(
            sz // 2, sz // 2 + 30,
            text=label,
            fill=accent,
            font=FONTS["CAPTION"],
        )
