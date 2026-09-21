"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Non-blocking toast notifications and skeleton loaders for sqlean-lint PRO.

* :class:`ToastStack` pins a stack of ephemeral notifications to the
  bottom-right corner, each auto-dismissing after ~4 seconds.
* :class:`SkeletonPulse` animates a surface between ``#1A2030`` and
  ``#242C42`` to act as a loading placeholder during background AST scans or
  query execution.

Headless-safe: widgets are only materialized when actually used, and the
implementations never call ``mainloop``.
"""
from __future__ import annotations

from typing import List

import customtkinter as ctk

from .design_system import TOKENS

_PULSE_A = "#1A2030"
_PULSE_B = "#242C42"
DEFAULT_DURATION_MS = 4000


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _parse_hex(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _to_hex(rgb: tuple) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[int(c) for c in rgb])


class ToastStack:
    """A bottom-right pinned stack of auto-dismissing toasts."""

    def __init__(self, master, *, max_visible: int = 5) -> None:
        self._master = master
        self._max_visible = max_visible
        self._toasts: List[ctk.CTkToplevel] = []
        self._spacing = 8

    def notify(self, text: str, *,
               kind: str = "info",
               duration_ms: int = DEFAULT_DURATION_MS) -> None:
        """Push a toast onto the bottom-right stack.

        ``kind`` selects an accent colour: ``info``/``success``/``warning``/
        ``error``.
        """
        try:
            self._spawn(text, kind, duration_ms)
        except Exception:  # noqa: BLE001 - toasts never crash the GUI
            pass

    def _spawn(self, text: str, kind: str, duration_ms: int) -> None:
        toast = ctk.CTkToplevel(self._master)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)

        accent = {
            "info": TOKENS["ACCENT_PRIMARY"],
            "success": TOKENS["SEMANTIC_SUCCESS"],
            "warning": TOKENS["SEMANTIC_WARNING"],
            "error": TOKENS["SEMANTIC_DANGER"],
        }.get(kind, TOKENS["ACCENT_PRIMARY"])

        toast.configure(
            fg_color=TOKENS["SURFACE_ELEVATED"],
            border_width=1, border_color=TOKENS["BORDER_STRONG"],
        )
        self._toasts.append(toast)
        self._cleanup_dead()
        x, y = self._next_position()
        toast.geometry(f"320x56+{max(0, x)}+{max(0, y)}")

        ctk.CTkFrame(toast, fg_color=accent, width=4, corner_radius=0).pack(
            side="left", fill="y"
        )
        ctk.CTkLabel(
            toast, text=text, font=ctk.CTkFont("Segoe UI", 12),
            text_color=TOKENS["TEXT_PRIMARY"], wraplength=260, justify="left",
        ).pack(side="left", padx=10, pady=8, fill="both", expand=True)

        # Auto-dismiss after the duration.
        toast.after(max(500, duration_ms), lambda t=toast: self.dismiss(t))

    def _next_position(self) -> tuple:
        try:
            root_w = self._master.winfo_width()
            root_h = self._master.winfo_height()
            rx = self._master.winfo_rootx()
            ry = self._master.winfo_rooty()
        except Exception:  # noqa: BLE001
            root_w, root_h, rx, ry = 1180, 720, 0, 0
        tw, th = 320, 56
        x = rx + root_w - tw - 16
        y = ry + root_h - th - 16 - (len(self._toasts) * (th + self._spacing))
        return (x, y)

    def _cleanup_dead(self) -> None:
        live = [t for t in self._toasts if t is not None and t.winfo_exists()]
        while len(live) > self._max_visible:
            old = live.pop(0)
            try:
                old.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._toasts = live

    def _reflow(self) -> None:
        pass

    def dismiss(self, toast: ctk.CTkToplevel) -> None:
        try:
            if toast.winfo_exists():
                toast.destroy()
        except Exception:  # noqa: BLE001
            pass
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reflow()

    def dismiss_all(self) -> None:
        for t in list(self._toasts):
            try:
                if t.winfo_exists():
                    t.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._toasts = []


class SkeletonPulse(ctk.CTkFrame):
    """A pulsing placeholder surface for background operations.

    Animates ``fg_color`` between ``#1A2030`` (rest) and ``#242C42``
    (peak) in a cosine loop. Call :meth:`start` to begin and :meth:`stop`
    to halt and restore the resting colour.
    """

    def __init__(self, master, *, width: int = 200, height: int = 24,
                 corner_radius: int = 6, **kwargs) -> None:
        super().__init__(master, fg_color=_PULSE_A, corner_radius=corner_radius,
                         width=width, height=height, **kwargs)
        self._rest = _PULSE_A
        self._peak = _PULSE_B
        self._running = False
        self._phase = 0.0
        self._step = 0.08
        self._rest_a = _parse_hex(self._rest)
        self._peak_b = _parse_hex(self._peak)

    def start(self) -> None:
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        try:
            self.configure(fg_color=self._rest)
        except Exception:  # noqa: BLE001
            pass

    @property
    def running(self) -> bool:
        return self._running

    def _tick(self) -> None:
        if not self._running:
            return
        # Triangle wave 0..1.
        t = (1.0 - abs(1.0 - (self._phase % 2.0))) / 1.0
        rgb = tuple(
            _lerp(self._rest_a[i], self._peak_b[i], t) for i in range(3)
        )
        try:
            self.configure(fg_color=_to_hex(rgb))
        except Exception:  # noqa: BLE001
            pass
        self._phase += self._step
        self.after(40, self._tick)
