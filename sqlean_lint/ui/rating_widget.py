"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Visual SQL health-scoring widget for the sqlean-lint PRO GUI.

Renders a 0-100 score as:

* a circular progress arc (drawn on a ``CTkCanvas``),
* a letter grade badge (A, B, C, D, F) whose colour transitions from green
  (A) to red (F),
* a human-readable status line.

The score can be animated via the shared animation engine so it "counts up"
to its final value for a polished enterprise feel.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

import customtkinter as ctk

# Grade thresholds: score -> (letter, status phrase).
_GRADE_TABLE = [
    (90, "A", "Excellent", "#9ece6a"),
    (80, "B", "Good", "#a7c080"),
    (65, "C", "Fair", "#e0af68"),
    (50, "D", "Poor", "#e07a68"),
    (0, "F", "Critical", "#f7768e"),
]


def letter_grade_for(score: float) -> tuple[str, str, str]:
    """Return ``(letter, phrase, hex_color)`` for a 0-100 *score*."""
    score = max(0.0, min(100.0, score))
    for threshold, letter, phrase, color in _GRADE_TABLE:
        if score >= threshold:
            return letter, phrase, color
    return "F", "Critical", "#f7768e"


class SQLHealthScore(ctk.CTkFrame):
    """Circular score + letter badge component.

    Args:
        master: Parent widget.
        score: Initial 0-100 score.
        radius: Radius of the circular arc (pixels).
    """

    def __init__(
        self,
        master: Any,
        *,
        score: float = 0.0,
        radius: int = 34,
        bg_color: str = "#24283b",
        zone_color: str = "#1f2335",
        accent_color: str = "#7aa2f7",
        text_color: str = "#c0caf5",
        muted_color: str = "#565f89",
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=10)
        self._radius = max(20, int(radius))
        self._zone = zone_color
        self._accent = accent_color
        self._text_color = text_color
        self._muted = muted_color
        self._anim: Optional[Callable] = None
        self._target = max(0.0, min(100.0, float(score)))

        diameter = self._radius * 2 + 16
        self.grid_columnconfigure(0, weight=1)

        canvas_holder = ctk.CTkFrame(self, fg_color="transparent")
        canvas_holder.grid(row=0, column=0, pady=(6, 2))
        self._canvas = ctk.CTkCanvas(
            canvas_holder,
            width=diameter,
            height=diameter,
            bg=bg_color,
            highlightthickness=0,
        )
        self._canvas.pack()

        self._badge = ctk.CTkLabel(
            self,
            text="-",
            font=ctk.CTkFont("Segoe UI", self._radius // 2 + 4, "bold"),
            text_color=self._muted,
            corner_radius=999,
            fg_color=self._zone,
            width=self._radius * 2 // 3,
        )
        self._badge.grid(row=1, column=0, pady=(0, 2))

        self._phrase = ctk.CTkLabel(
            self,
            text="No data",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=self._muted,
        )
        self._phrase.grid(row=2, column=0, pady=(0, 2))

        self._score_label = ctk.CTkLabel(
            self,
            text="0/100",
            font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._score_label.grid(row=3, column=0, pady=(0, 6))

        self._render_score(0.0)

    # ── public API ─────────────────────────────────────────────────────

    def set_score(self, score: float, *, animate: bool = True) -> None:
        """Update the displayed score, optionally animating the arc."""
        score = max(0.0, min(100.0, float(score)))
        self._target = score
        if animate:
            from .animations import CTkAnimationEngine

            def _step(value: float) -> None:
                self._render_score(value)

            engine = CTkAnimationEngine(master=self, fps=60)
            self._anim = engine
            engine.animate(
                "score",
                620,
                _step,
                start=0.0,
                end=score,
                easing="ease_out",
            )
        else:
            self._render_score(score)

    def score(self) -> float:
        """Return the current (target) 0-100 score."""
        return self._target

    def letter(self) -> str:
        """Return the current letter grade (A-F)."""
        letter, _phrase, _color = letter_grade_for(self._target)
        return letter

    # ── rendering ──────────────────────────────────────────────────────

    def _render_score(self, value: float) -> None:
        """Redraw the circular gauge for a 0-100 *value*."""
        letter, phrase, color = letter_grade_for(value)
        self._badge.configure(text=letter, text_color=color)
        self._phrase.configure(text=phrase, text_color=color)
        self._score_label.configure(
            text=f"{round(value)}/100", text_color=self._text_color
        )

        self._canvas.delete("all")
        cx = cy = self._radius + 8
        r = self._radius - 2
        start_deg = 135
        sweep_deg = -270 * (value / 100.0)

        # Background track.
        self._canvas.create_arc(
            cx - r,
            cy - r,
            cx + r,
            cy + r,
            start=start_deg,
            extent=-270,
            style="arc",
            outline=self._zone,
            width=6,
        )
        if value > 0:
            self._canvas.create_arc(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                start=start_deg,
                extent=sweep_deg,
                style="arc",
                outline=color,
                width=6,
            )
        # Center number.
        self._canvas.create_text(
            cx,
            cy,
            text=str(round(value)),
            fill=color,
            font=("Segoe UI", self._radius // 2, "bold"),
        )
