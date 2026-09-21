"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Animation engine for the sqlean-lint PRO CustomTkinter GUI.

Implements a lightweight, dependency-free animation framework built on the
Tk ``after`` scheduler so transitions run at roughly 60 FPS without blocking
the main event loop:

* :class:`CTkAnimationEngine` -- a reusable tweening loop that drives numeric
  transitions (position, width, alpha-like values) every frame.
* :class:`SlidePanel` -- a collapsible side panel that slides open/closed with
  an eased horizontal transition (KNIME/Excel-style reveal).
* :class:`FadeWidget` -- a wrapper that fades a widget's foreground/background
  colour toward a target over time, used for hover and loading cues.

Everything degrades gracefully: if a widget is destroyed mid-animation the
loop stops without raising.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import customtkinter as ctk

# Nominal refresh interval matching ~60 FPS (in milliseconds).
FRAME_MS = 16

# Easing presets.
_EASE_CURVES = {
    "linear": lambda t: t,
    "ease_out": lambda t: 1 - (1 - t) ** 3,
    "ease_in": lambda t: t ** 3,
    "ease_in_out": lambda t: t * t * (3 - 2 * t),
}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _parse_hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return (
        int(color[0:2], 16),
        int(color[2:4], 16),
        int(color[4:6], 16),
    )


def _to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(rgb[0]))),
        max(0, min(255, round(rgb[1]))),
        max(0, min(255, round(rgb[2]))),
    )


class CTkAnimationEngine:
    """Manages numeric tweens for widgets via the Tk ``after`` loop.

    Each animation is identified by a ``handle`` so it can be cancelled and
    superseded by a newer animation on the same property (prevents conflicting
    tweens on a single widget).
    """

    def __init__(self, master: Any, fps: int = 60) -> None:
        self._master = master
        self._interval = max(1, round(1000 / max(1, fps)))
        self._tweens: Dict[str, Dict[str, Any]] = {}
        self._running: bool = False
        self._timer_id: Optional[str] = None

    def animate(
        self,
        handle: str,
        duration_ms: int,
        setter: Callable[[float], None],
        *,
        start: float = 0.0,
        end: float = 1.0,
        easing: str = "ease_out",
        on_complete: Optional[Callable[[], None]] = None,
    ) -> None:
        """Tween ``start -> end`` over ``duration_ms`` frames.

        ``setter`` receives the current eased progress value in ``[start, end]``
        and is called every frame. ``easing`` selects a curve from the presets.
        """
        try:
            self._tweens[handle] = {
                "start": start,
                "end": end,
                "duration": max(1, int(duration_ms)),
                "setter": setter,
                "on_complete": on_complete,
                "elapsed": 0.0,
                "curve": _EASE_CURVES.get(easing, _EASE_CURVES["linear"]),
            }
        except Exception:  # noqa: BLE001 - widget may be gone
            return
        if not self._running:
            self._running = True
            self._tick()

    def stop(self, handle: str) -> None:
        """Cancel any animation registered under *handle*."""
        self._tweens.pop(handle, None)

    def stop_all(self) -> None:
        self._tweens.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    def _tick(self) -> None:
        """Advance all active tweens by one frame and reschedule."""
        if not self._running:
            return
        finished: list[str] = []
        for handle, tween in list(self._tweens.items()):
            tween["elapsed"] += self._interval
            progress = _clamp(tween["elapsed"] / tween["duration"])
            eased = tween["curve"](progress)
            value = _lerp(tween["start"], tween["end"], eased)
            try:
                tween["setter"](value)
            except Exception:  # noqa: BLE001 - widget destroyed mid-animation
                finished.append(handle)
                continue
            if progress >= 1.0:
                finished.append(handle)
        for handle in finished:
            on_complete = self._tweens.pop(handle, {}).get("on_complete")
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:  # noqa: BLE001
                    pass
        if self._tweens:
            self._timer_id = self._master.after(self._interval, self._tick)
        else:
            self._running = False
            self._timer_id = None

    def cancel(self) -> None:
        """Halt the engine and clear all pending tweens."""
        self._running = False
        if self._timer_id is not None and self._master.winfo_exists():
            try:
                self._master.after_cancel(self._timer_id)
            except Exception:  # noqa: BLE001
                pass
        self._timer_id = None
        self._tweens.clear()


class SlidePanel(ctk.CTkFrame):
    """A width-animated collapsible panel.

    Renders its contents inside an inner frame with a fixed pixel height and
    animates the outer frame's width between *closed_width* and *open_width*
    using the :class:`CTkAnimationEngine`. All children should be packed into
    ``body`` (obtained via :meth:`get_body`).
    """

    def __init__(
        self,
        master: Any,
        *,
        open_width: int = 300,
        closed_width: int = 0,
        duration_ms: int = 260,
        easing: str = "ease_out",
        fg_color: Optional[str] = None,
        corner_radius: int = 12,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master, fg_color=fg_color, corner_radius=corner_radius, **kwargs
        )
        self._open_width = max(0, int(open_width))
        self._closed_width = max(0, int(closed_width))
        self._duration_ms = max(1, int(duration_ms))
        self._easing = easing
        self._engine = CTkAnimationEngine(master=self, fps=60)
        self._is_open = False
        self._on_toggle: list[Callable[[bool], None]] = []

        # Inner body hosts child widgets; its width is governed by this frame.
        self._body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self._body.pack(fill="both", expand=True, padx=4, pady=4)

        self.configure(width=self._closed_width)
        self.pack_propagate(False)

    def get_body(self) -> ctk.CTkFrame:
        """Return the inner frame onto which callers pack their widgets."""
        return self._body

    @property
    def is_open(self) -> bool:
        return self._is_open

    def on_toggle(self, callback: Callable[[bool], None]) -> None:
        """Register a callback invoked whenever open-state changes."""
        self._on_toggle.append(callback)

    def toggle(self) -> None:
        self.open() if not self._is_open else self.close()

    def open(self, animate: bool = True) -> None:
        target = self._open_width
        self._is_open = True
        self._animate_width(target, animate)

    def close(self, animate: bool = True) -> None:
        target = self._closed_width
        self._is_open = False
        self._animate_width(target, animate)

    def _animate_width(self, target: int, animate: bool) -> None:
        def setter(value: float) -> None:
            try:
                self.configure(width=max(0, round(value)))
            except Exception:  # noqa: BLE001
                pass

        if not animate:
            self.configure(width=target)
            self._emit_toggle()
            return
        self._engine.animate(
            "width",
            self._duration_ms,
            setter,
            start=float(self.winfo_width()),
            end=float(target),
            easing=self._easing,
            on_complete=self._emit_toggle,
        )

    def _emit_toggle(self) -> None:
        for cb in self._on_toggle:
            try:
                cb(self._is_open)
            except Exception:  # noqa: BLE001
                pass

    def destroy(self) -> None:
        try:
            self._engine.cancel()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()


class FadeWidget:
    """Colour-fading wrapper for hover/loading transitions.

    Animates a widget's ``fg_color``/``text_color`` between a resting and a
    highlighted colour over time. Useful to soften hover states on custom
    block widgets that do not implement their own hover styling.
    """

    def __init__(
        self,
        widget: ctk.CTkFrame,
        *,
        target_fg: str = "#7aa2f7",
        idle_fg: Optional[str] = None,
        duration_ms: int = 180,
        easing: str = "ease_out",
    ) -> None:
        self._widget = widget
        self._target_fg = target_fg
        self._idle_fg = idle_fg or widget.cget("fg_color") or "#24283b"
        self._duration_ms = max(1, int(duration_ms))
        self._easing = easing
        self._engine = CTkAnimationEngine(master=widget, fps=60)
        self._active = False
        try:
            widget.bind("<Enter>", lambda _e: self.enter())
            widget.bind("<Leave>", lambda _e: self.leave())
        except Exception:  # noqa: BLE001
            pass

    def enter(self) -> None:
        self._active = True
        self._fade_to(self._target_fg)

    def leave(self) -> None:
        self._active = False
        self._fade_to(self._idle_fg)

    @property
    def active(self) -> bool:
        return self._active

    def _fade_to(self, target: str) -> None:
        try:
            current = self._widget.cget("fg_color") or self._idle_fg
            if self._is_transparent(current):
                current = self._idle_fg
            if self._is_transparent(target):
                target = self._idle_fg
        except Exception:  # noqa: BLE001
            return
        start_rgb, end_rgb = _parse_hex(current), _parse_hex(target)

        def setter(value: float) -> None:
            rgb = tuple(
                _lerp(start_rgb[i], end_rgb[i], value) for i in range(3)
            )
            try:
                self._widget.configure(fg_color=_to_hex(rgb))
            except Exception:  # noqa: BLE001
                pass

        self._engine.animate("fg", self._duration_ms, setter, easing=self._easing)

    @staticmethod
    def _is_transparent(color: str) -> bool:
        return color is None or (isinstance(color, str) and color.lower() in (
            "transparent", "#00000000"
        ))
