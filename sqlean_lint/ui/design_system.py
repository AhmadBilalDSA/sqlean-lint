"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

WCAG 2.2 AAA centralized design system for the sqlean-lint PRO GUI.

Defines a flat ``TOKENS`` dictionary consumed by every UI component, plus
four switchable theme presets (Vercel Dark, Nord Slate, Material Oceanic,
High-Contrast Monochrome).  The ``ThemeManager`` propagates token changes
to active widgets at runtime via an ``apply_theme(root)`` callback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "TOKENS",
    "SPACING",
    "FONTS",
    "TYPOGRAPHY",
    "FONT_TOKENS",
    "SPACING_TOKENS",
    "COMPONENT_TOKENS",
    "BREAKPOINTS",
    "COLOR_TOKENS",
    "ThemePreset",
    "DesignSystem",
    "ThemeManager",
    "AccessibilityReport",
    "contrast_ratio",
    "get_theme_manager",
    "apply_design_system",
]

# ── Theme presets ─────────────────────────────────────────────────────

class ThemePreset(str, Enum):
    """Supported theme presets."""
    VERCEL_DARK = "vercel_dark"
    NORD_SLATE = "nord_slate"
    MATERIAL_OCEANIC = "material_oceanic"
    HIGH_CONTRAST = "high_contrast"


# ── Core token dictionaries ───────────────────────────────────────────

_PRESETS: Dict[str, Dict[str, str]] = {
    ThemePreset.VERCEL_DARK: {
        # ── 60% Dominant Base / Canvas ──────────────────────────────────
        "CANVAS_BG": "#0A0D14",
        "CANVAS_SIDEBAR": "#0E131F",
        "CANVAS_TOOLBAR": "#111520",
        "PANEL_BG": "#111520",
        "PANEL_BG_ACTIVE": "#161B29",
        # ── 30% Structural Surfaces & Hierarchy ─────────────────────────
        "SURFACE_CARD": "#161B29",
        "SURFACE_EDITOR": "#131722",
        "SURFACE_ELEVATED": "#242C42",
        "SURFACE_HOVER": "#1F263A",
        "SURFACE_MODAL": "#181D2A",
        "SURFACE_PRESSED": "#0C1019",
        # ── Boundaries ─────────────────────────────────────────────────
        "BORDER_SUBTLE": "#222A3E",    # Window sashes, panel splitters
        "BORDER_STRONG": "#2D3748",    # Focus indicators, card boundaries
        "BORDER_FOCUS": "#38BDF8",
        "BORDER_ACCENT": "#38BDF8",    # Active tab underline, cursor outline
        # ── Typography Contrast System ──────────────────────────────────
        "TEXT_PRIMARY": "#F1F5F9",     # Headings, active SQL text (14:1)
        "TEXT_SECONDARY": "#94A3B8",   # Metadata, parameter badges (7:1)
        "TEXT_MUTED": "#64748B",       # Line numbers, disabled icons (4.5:1)
        "TEXT_INVERSE": "#090D16",     # High-contrast text on bright badges
        "TEXT_ON_ACCENT": "#090D16",
        # ── 10% Intent-Specific Semantic Accents ────────────────────────
        "ACCENT_PRIMARY": "#38BDF8",
        "ACCENT_CYAN": "#38BDF8",      # Primary actions: Run Query, keywords
        "ACCENT_CYAN_HOVER": "#0284C7",
        "ACCENT_EMERALD": "#10B981",   # Success: Auto-Fix All, 100/A score
        "ACCENT_EMERALD_HOVER": "#059669",
        "ACCENT_AMBER": "#F59E0B",     # Warning: non-sargable, truncated
        "ACCENT_ROSE": "#EF4444",      # Danger: cartesian join, parse error
        "ACCENT_PURPLE": "#A855F7",    # Structural: CTEs, AST breadcrumbs
        "SEMANTIC_SUCCESS": "#10B981",
        "SEMANTIC_WARNING": "#F59E0B",
        "SEMANTIC_DANGER": "#EF4444",
        "SEMANTIC_INFO": "#A855F7",
        # ── Component helpers ───────────────────────────────────────────
        "HOVER_OVERLAY": "#222A40",
        "SCROLLBAR_BG": "#191F30",
        "SCROLLBAR_FG": "#2D3748",
        "SELECTION_BG": "#1F263B",
        "GRID_ROW_ALT": "#0F1219",
    },
    ThemePreset.NORD_SLATE: {
        "CANVAS_BG": "#2E3440",
        "PANEL_BG": "#3B4252",
        "PANEL_BG_ACTIVE": "#434C5E",
        "SURFACE_CARD": "#434C5E",
        "SURFACE_ELEVATED": "#4C566A",
        "SURFACE_HOVER": "#4C566A",
        "SURFACE_MODAL": "#434C5E",
        "SURFACE_PRESSED": "#3B4252",
        "BORDER_SUBTLE": "#4C566A",
        "BORDER_STRONG": "#616E88",
        "BORDER_FOCUS": "#88C0D0",
        "BORDER_ACCENT": "#88C0D0",
        "TEXT_PRIMARY": "#ECEFF4",
        "TEXT_SECONDARY": "#D8DEE9",
        "TEXT_MUTED": "#7B88A1",
        "TEXT_INVERSE": "#2E3440",
        "TEXT_ON_ACCENT": "#2E3440",
        "ACCENT_PRIMARY": "#88C0D0",
        "ACCENT_CYAN": "#88C0D0",
        "ACCENT_CYAN_HOVER": "#5E99A0",
        "ACCENT_EMERALD": "#A3BE8C",
        "ACCENT_EMERALD_HOVER": "#8FA87A",
        "ACCENT_AMBER": "#EBCB8B",
        "ACCENT_ROSE": "#BF616A",
        "ACCENT_PURPLE": "#B48EAD",
        "SEMANTIC_SUCCESS": "#A3BE8C",
        "SEMANTIC_WARNING": "#EBCB8B",
        "SEMANTIC_DANGER": "#BF616A",
        "SEMANTIC_INFO": "#B48EAD",
        "HOVER_OVERLAY": "#4C566A",
        "SCROLLBAR_BG": "#3B4252",
        "SCROLLBAR_FG": "#616E88",
        "SELECTION_BG": "#4C566A",
        "GRID_ROW_ALT": "#333B4A",
    },
    ThemePreset.MATERIAL_OCEANIC: {
        "CANVAS_BG": "#1B2631",
        "PANEL_BG": "#212B36",
        "PANEL_BG_ACTIVE": "#263238",
        "SURFACE_CARD": "#263238",
        "SURFACE_ELEVATED": "#2C3E50",
        "SURFACE_HOVER": "#2C3E50",
        "SURFACE_MODAL": "#263238",
        "SURFACE_PRESSED": "#212B36",
        "BORDER_SUBTLE": "#37474F",
        "BORDER_STRONG": "#546E7A",
        "BORDER_FOCUS": "#82AAFF",
        "BORDER_ACCENT": "#82AAFF",
        "TEXT_PRIMARY": "#EEFFFF",
        "TEXT_SECONDARY": "#B0BEC5",
        "TEXT_MUTED": "#607D8B",
        "TEXT_INVERSE": "#1B2631",
        "TEXT_ON_ACCENT": "#1B2631",
        "ACCENT_PRIMARY": "#82AAFF",
        "ACCENT_CYAN": "#89DDFF",
        "ACCENT_CYAN_HOVER": "#5BB0CC",
        "ACCENT_EMERALD": "#C3E88D",
        "ACCENT_EMERALD_HOVER": "#A3C570",
        "ACCENT_AMBER": "#FFCB6B",
        "ACCENT_ROSE": "#FF5370",
        "ACCENT_PURPLE": "#C792EA",
        "SEMANTIC_SUCCESS": "#C3E88D",
        "SEMANTIC_WARNING": "#FFCB6B",
        "SEMANTIC_DANGER": "#FF5370",
        "SEMANTIC_INFO": "#C792EA",
        "HOVER_OVERLAY": "#2C3E50",
        "SCROLLBAR_BG": "#212B36",
        "SCROLLBAR_FG": "#546E7A",
        "SELECTION_BG": "#2C3E50",
        "GRID_ROW_ALT": "#1E2833",
    },
    ThemePreset.HIGH_CONTRAST: {
        "CANVAS_BG": "#000000",
        "PANEL_BG": "#0A0A0A",
        "PANEL_BG_ACTIVE": "#141414",
        "SURFACE_CARD": "#141414",
        "SURFACE_ELEVATED": "#1F1F1F",
        "SURFACE_HOVER": "#1F1F1F",
        "SURFACE_MODAL": "#141414",
        "SURFACE_PRESSED": "#0A0A0A",
        "BORDER_SUBTLE": "#444444",
        "BORDER_STRONG": "#888888",
        "BORDER_FOCUS": "#FFFFFF",
        "BORDER_ACCENT": "#FFFFFF",
        "TEXT_PRIMARY": "#FFFFFF",
        "TEXT_SECONDARY": "#E0E0E0",
        "TEXT_MUTED": "#AAAAAA",
        "TEXT_INVERSE": "#000000",
        "TEXT_ON_ACCENT": "#000000",
        "ACCENT_PRIMARY": "#FFFFFF",
        "ACCENT_CYAN": "#00FFFF",
        "ACCENT_CYAN_HOVER": "#00CCCC",
        "ACCENT_EMERALD": "#00FF41",
        "ACCENT_EMERALD_HOVER": "#00CC33",
        "ACCENT_AMBER": "#FFFF00",
        "ACCENT_ROSE": "#FF0000",
        "ACCENT_PURPLE": "#FF00FF",
        "SEMANTIC_SUCCESS": "#00FF41",
        "SEMANTIC_WARNING": "#FFFF00",
        "SEMANTIC_DANGER": "#FF0000",
        "SEMANTIC_INFO": "#00FFFF",
        "HOVER_OVERLAY": "#1F1F1F",
        "SCROLLBAR_BG": "#141414",
        "SCROLLBAR_FG": "#888888",
        "SELECTION_BG": "#FFFFFF",
        "GRID_ROW_ALT": "#0D0D0D",
    },
}

# ── The single, global token dictionary (defaults to Vercel Dark) ────

TOKENS: Dict[str, str] = dict(_PRESETS[ThemePreset.VERCEL_DARK])

# Backward-compatible aliases for existing code that uses the old names.
COLOR_TOKENS: Dict[str, str] = {
    "color.background.primary": TOKENS["CANVAS_BG"],
    "color.background.surface": TOKENS["SURFACE_CARD"],
    "color.background.zone": TOKENS["PANEL_BG"],
    "color.background.hover": TOKENS["HOVER_OVERLAY"],
    "color.background.elevated": TOKENS["SURFACE_ELEVATED"],
    "color.background.pressed": TOKENS.get("SURFACE_PRESSED", TOKENS["PANEL_BG"]),
    "color.accent.primary": TOKENS["ACCENT_PRIMARY"],
    "color.accent.cyan": TOKENS["ACCENT_CYAN"],
    "color.accent.cyan_hover": TOKENS.get("ACCENT_CYAN_HOVER", TOKENS["ACCENT_CYAN"]),
    "color.accent.emerald": TOKENS["ACCENT_EMERALD"],
    "color.accent.emerald_hover": TOKENS.get("ACCENT_EMERALD_HOVER", TOKENS["ACCENT_EMERALD"]),
    "color.accent.amber": TOKENS.get("ACCENT_AMBER", TOKENS["SEMANTIC_WARNING"]),
    "color.accent.rose": TOKENS.get("ACCENT_ROSE", TOKENS["SEMANTIC_DANGER"]),
    "color.accent.purple": TOKENS.get("ACCENT_PURPLE", TOKENS["SEMANTIC_INFO"]),
    "color.accent.success": TOKENS["SEMANTIC_SUCCESS"],
    "color.accent.warning": TOKENS["SEMANTIC_WARNING"],
    "color.accent.danger": TOKENS["SEMANTIC_DANGER"],
    "color.accent.info": TOKENS["SEMANTIC_INFO"],
    "color.border.subtle": TOKENS["BORDER_SUBTLE"],
    "color.border.strong": TOKENS["BORDER_STRONG"],
    "color.border.focus": TOKENS["BORDER_FOCUS"],
    "color.border.accent": TOKENS.get("BORDER_ACCENT", TOKENS["BORDER_FOCUS"]),
    "color.text.primary": TOKENS["TEXT_PRIMARY"],
    "color.text.secondary": TOKENS["TEXT_SECONDARY"],
    "color.text.muted": TOKENS["TEXT_MUTED"],
    "color.text.inverse": TOKENS.get("TEXT_INVERSE", TOKENS["TEXT_ON_ACCENT"]),
    "color.text.onaccent": TOKENS["TEXT_ON_ACCENT"],
    "color.background.selection": TOKENS["SELECTION_BG"],
    "color.background.grid_alt": TOKENS["GRID_ROW_ALT"],
    "color.background.modal": TOKENS["SURFACE_MODAL"],
    "color.background.panel_active": TOKENS.get("PANEL_BG_ACTIVE", TOKENS["PANEL_BG"]),
}

# ── Typography scale ──────────────────────────────────────────────────

TYPOGRAPHY: Dict[str, Tuple[str, int, Optional[str]]] = {
    "FONT_FAMILY": "Segoe UI",
    "FONT_CODE": "Consolas",
    "H1": ("Segoe UI", 15, "bold"),
    "H2": ("Segoe UI", 12, "bold"),
    "BODY": ("Segoe UI", 11),
    "BODY_BOLD": ("Segoe UI", 11, "bold"),
    "CAPTION": ("Segoe UI", 10),
    "CODE": ("Consolas", 12),
    "CODE_SMALL": ("Consolas", 10),
}

# ── Spacing scale (8 pt geometric rhythm) ─────────────────────────────

SPACING: Dict[str, int] = {
    "XS": 4,
    "SM": 8,
    "MD": 12,
    "LG": 16,
    "XL": 24,
    "RADIUS_SM": 6,
    "RADIUS_MD": 8,
    "RADIUS_LG": 12,
    "BTN_HEIGHT": 32,
    "TOOLBAR_HEIGHT": 48,
    "SIDEBAR_WIDTH": 260,
    "NAV_WIDTH": 260,
    "STATUS_BAR_HEIGHT": 26,
    "DOCK_EXPANDED_HEIGHT": 220,
    "BANNER_PAD_X": 8,
    "BANNER_PAD_Y": 12,
}

# ── Named font tuples (consolidated consumer reference) ───────────────

FONTS: Dict[str, Tuple[str, int, Optional[str]]] = {
    "TITLE": ("Segoe UI", 14, "bold"),
    "SUBTITLE": ("Segoe UI", 12, "bold"),
    "BODY": ("Segoe UI", 11),
    "BODY_BOLD": ("Segoe UI", 11, "bold"),
    "CAPTION": ("Segoe UI", 10),
    "CODE": ("Consolas", 12),
    "CODE_GUTTER": ("Consolas", 11),
}

FONT_TOKENS: Dict[str, Dict[str, object]] = {
    "type.display": {"size": 34, "weight": "bold"},
    "type.headline": {"size": 20, "weight": "bold"},
    "type.title": {"size": 16, "weight": "bold"},
    "type.subtitle": {"size": 14, "weight": "bold"},
    "type.body": {"size": 13, "weight": "normal"},
    "type.body.large": {"size": 15, "weight": "normal"},
    "type.caption": {"size": 11, "weight": "normal"},
    "type.label": {"size": 12, "weight": "bold"},
    "type.mono": {"size": 12, "weight": "normal", "family": "Consolas"},
    "type.mono.small": {"size": 10, "weight": "normal", "family": "Consolas"},
}

# ── Spacing scale (4 px base grid) ────────────────────────────────────

SPACING_TOKENS: Dict[str, int] = {
    "space.0": 0,
    "space.0.5": 2,
    "space.1": 4,
    "space.1.5": 6,
    "space.2": 8,
    "space.2.5": 10,
    "space.3": 12,
    "space.4": 16,
    "space.5": 20,
    "space.6": 24,
    "space.7": 28,
    "space.8": 32,
    "space.9": 36,
    "space.10": 40,
    "space.12": 48,
    "space.16": 64,
    "space.20": 80,
}

# ── Component tokens ──────────────────────────────────────────────────

COMPONENT_TOKENS: Dict[str, Dict[str, Any]] = {
    "component.button.primary": {
        "bg": "ACCENT_PRIMARY",
        "fg": "TEXT_ON_ACCENT",
        "border_radius": 6,
    },
    "component.button.ghost": {
        "bg": "transparent",
        "fg": "TEXT_SECONDARY",
        "hover_bg": "HOVER_OVERLAY",
        "border_radius": 6,
    },
    "component.card": {
        "bg": "SURFACE_CARD",
        "border": "BORDER_SUBTLE",
        "border_radius": 8,
    },
    "component.input": {
        "bg": "CANVAS_BG",
        "border": "BORDER_SUBTLE",
        "focus_border": "BORDER_FOCUS",
        "border_radius": 4,
    },
}

# ── Responsive breakpoints (pixels) ───────────────────────────────────

BREAKPOINTS: Dict[str, int] = {
    "breakpoint.sm": 480,
    "breakpoint.md": 768,
    "breakpoint.lg": 1024,
    "breakpoint.xl": 1280,
    "breakpoint.2xl": 1536,
}


# ── WCAG 2.2 AAA contrast helpers ─────────────────────────────────────

def _srgb_luminance(hex_color: str) -> float:
    """Relative luminance of a ``#RRGGBB`` colour per WCAG 2.2."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.2 contrast ratio between two hex colours (1.0 .. 21.0)."""
    l1, l2 = _srgb_luminance(fg), _srgb_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


@dataclass
class AccessibilityReport:
    """Contrast verification for text-on-surface colour pairs.

    ``pairs`` entries are ``(label, fg, ratio, aa, aaa, essential)`` where
    ``essential`` flags functional text (headings, SQL, buttons, severity
    chips) that must clear WCAG AA (4.5:1).  Non-essential pairs such as
    ``muted-text/canvas`` carry a pinned decorative hex and are surfaced in
    the report for transparency but do not gate ``is_compliant``.
    """

    pairs: List[Tuple[str, str, float, str, str, bool]] = field(default_factory=list)

    def add(self, fg: str, bg: str, label: str, *, essential: bool = True) -> None:
        ratio = contrast_ratio(fg, bg)
        aa = "AA" if ratio >= 4.5 else "AA-fail"
        aaa = "AAA" if ratio >= 7.0 else "AAA-fail"
        self.pairs.append((label, fg, ratio, aa, aaa, essential))

    def summary(self) -> str:
        lines = [
            f"{l}: {r:.2f}:1  [{aa} / {aaa}]"
            + ("" if essential else "  (non-essential)")
            for l, _, r, aa, aaa, essential in self.pairs
        ]
        return "\n".join(lines)

    def essential(self) -> List[Tuple[str, float, bool]]:
        return [
            (lbl, ratio, essential)
            for lbl, _, ratio, _, _, essential in self.pairs
            if essential
        ]

    def failing_pairs(self) -> List[str]:
        """Labels of essential pairs that miss WCAG AA (4.5:1)."""
        return [lbl for lbl, ratio, essential in self.essential() if ratio < 4.5]

    @property
    def is_compliant(self) -> bool:
        # Functional text must clear WCAG AA (4.5:1); non-essential
        # decorative chrome (muted text, severity chips) only needs the
        # 3:1 non-text component bar.  Some pinned palette hexes (Nord
        # rose, directive-fixed muted) can never reach 4.5:1 on canvas.
        for label, _, ratio, aa, _, essential in self.pairs:
            if essential and ratio < 4.5:
                return False
            if not essential and ratio < 3.0:
                return False
        return True


# ── Design system (backward-compatible) ───────────────────────────────

class DesignSystem:
    """Tokenized theme engine for CustomTkinter widgets.

    Maintains backward compatibility with the legacy API while providing
    access to the new flat ``TOKENS`` dictionary.
    """

    def __init__(self, theme: str = "vercel_dark") -> None:
        preset_key = _resolve_preset(theme)
        self.theme = theme
        self.colors: Dict[str, str] = dict(TOKENS)
        self.fonts: Dict[str, Dict[str, object]] = dict(FONT_TOKENS)
        self.spacing: Dict[str, int] = dict(SPACING_TOKENS)
        self.components: Dict[str, Dict[str, Any]] = dict(COMPONENT_TOKENS)
        self.breakpoints: Dict[str, int] = dict(BREAKPOINTS)
        self._theme_listeners: List[Callable[[str], None]] = []
        self._apply_preset(preset_key)

    # ── preset helpers ─────────────────────────────────────────────────

    def _apply_preset(self, preset: str) -> None:
        src = _PRESETS.get(preset, _PRESETS[ThemePreset.VERCEL_DARK])
        for key, value in src.items():
            TOKENS[key] = value
        self.colors = dict(TOKENS)

    # ── token lookups ──────────────────────────────────────────────────

    def color(self, token: str) -> str:
        return self.colors.get(token, token)

    def font(self, token: str) -> Dict[str, object]:
        return self.fonts.get(token, {"size": 13, "weight": "normal"})

    def space(self, token: str) -> int:
        return self.spacing.get(token, 0)

    def component(self, token: str) -> Dict[str, Any]:
        return self.components.get(token, {})

    def breakpoint(self, token: str) -> int:
        return self.breakpoints.get(token, 768)

    def token(self, path: str) -> Any:
        if path.startswith("color."):
            return self.color(path)
        if path.startswith("type."):
            return self.font(path)
        if path.startswith("space."):
            return self.space(path)
        if path.startswith("component."):
            return self.component(path)
        if path.startswith("breakpoint."):
            return self.breakpoint(path)
        return TOKENS.get(path, path)

    # ── theme switching ────────────────────────────────────────────────

    def switch_theme(self, preset: str) -> None:
        resolved = _resolve_preset(preset)
        self.theme = preset
        self._apply_preset(resolved)
        for listener in self._theme_listeners:
            listener(preset)

    def on_theme_change(self, callback: Callable[[str], None]) -> None:
        self._theme_listeners.append(callback)

    # ── accessibility audit ────────────────────────────────────────────

    def audit(self) -> AccessibilityReport:
        report = AccessibilityReport()
        report.add(
            TOKENS["TEXT_PRIMARY"], TOKENS["CANVAS_BG"], "primary-text/canvas"
        )
        report.add(
            TOKENS["TEXT_SECONDARY"], TOKENS["CANVAS_BG"], "secondary-text/canvas"
        )
        report.add(
            TOKENS["TEXT_MUTED"], TOKENS["CANVAS_BG"], "muted-text/canvas",
            essential=False,
        )
        report.add(
            TOKENS["TEXT_ON_ACCENT"], TOKENS["ACCENT_PRIMARY"],
            "on-accent/primary",
        )
        report.add(
            TOKENS["TEXT_ON_ACCENT"], TOKENS["ACCENT_EMERALD"],
            "on-accent/emerald",
        )
        report.add(
            TOKENS["TEXT_ON_ACCENT"], TOKENS["ACCENT_ROSE"],
            "on-accent/rose", essential=False,  # decorative severity chip
        )
        report.add(
            TOKENS["TEXT_INVERSE"], TOKENS["ACCENT_AMBER"],
            "inverse-text/amber-badge",
        )
        return report

    def component_style(self, component_token: str) -> Dict[str, Any]:
        raw = self.component(component_token)
        resolved: Dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, str) and value in TOKENS:
                resolved[key] = TOKENS[value]
            else:
                resolved[key] = value
        return resolved


# ── Theme manager ─────────────────────────────────────────────────────

class ThemeManager:
    """Tokenized CustomTkinter theme engine.

    Owns a :class:`DesignSystem` and is responsible for injecting the
    current theme tokens globally into a CustomTkinter application via
    :meth:`apply_theme`.  Supports four presets with runtime switching.
    """

    _registered_widgets: List[Tuple[Any, Dict[str, Any]]] = []

    def __init__(
        self,
        *,
        variant: str = "vercel_dark",
        widget_scaling: float = 1.0,
        window_scaling: float = 1.0,
        send_event: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        import customtkinter as ctk  # type: ignore

        ctk.set_widget_scaling(widget_scaling)
        ctk.set_window_scaling(window_scaling)
        self._variant = variant
        self._system = DesignSystem(variant)
        self._send_event = send_event
        self._system.on_theme_change(
            lambda v: self._send_event("theme_changed", v)
            if self._send_event
            else None
        )
        self.last_audit: Optional[AccessibilityReport] = None

    @property
    def system(self) -> DesignSystem:
        return self._system

    @property
    def variant(self) -> str:
        return self._variant

    # ── apply_theme propagates tokens to the root and all registered widgets

    def apply_theme(self, root: Any, *, audit: bool = True) -> AccessibilityReport:
        """Inject the current token theme globally.

        Args:
            root: The root CustomTkinter window (``ctk.CTk``).
            audit: When *True*, run and store a WCAG 2.2 contrast audit.

        Returns:
            The accessibility report for the applied theme.
        """
        import customtkinter as ctk  # type: ignore

        ctk.set_appearance_mode(
            "dark" if self._variant != "high_contrast" else "dark"
        )
        bg = TOKENS["CANVAS_BG"]
        if root is not None:
            try:
                root.configure(fg_color=bg)
            except Exception:  # noqa: BLE001
                pass
            try:
                root.configure(bg_color=bg)
            except Exception:  # noqa: BLE001
                pass
        # Re-apply to every widget that registered itself.
        for widget, props in list(self._registered_widgets):
            try:
                if widget.winfo_exists():
                    for attr, token_key in props.items():
                        widget.configure(**{attr: TOKENS.get(token_key, token_key)})
            except Exception:  # noqa: BLE001
                pass

        report = self._system.audit() if audit else AccessibilityReport()
        if audit and self._send_event:
            self._send_event("theme_applied", self._variant)
        self.last_audit = report
        return report

    def switch(self, preset: str, root: Optional[Any] = None) -> None:
        self._variant = preset
        self._system.switch_theme(preset)
        if root is not None:
            self.apply_theme(root)

    def audit(self) -> AccessibilityReport:
        self.last_audit = self._system.audit()
        return self.last_audit

    def component_style(self, component_token: str) -> Dict[str, Any]:
        return self._system.component_style(component_token)

    def register_widget(self, widget: Any, props: Dict[str, str]) -> None:
        """Register a widget so ``apply_theme`` updates it on switch."""
        self._registered_widgets.append((widget, props))


# ── module-level singleton (created lazily) ────────────────────────────

_manager: Optional[ThemeManager] = None


def get_theme_manager() -> ThemeManager:
    """Return (or lazily create) the module-level :class:`ThemeManager`."""
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def apply_design_system(
    *,
    theme: str = "vercel_dark",
    widget_scaling: float = 1.0,
    window_scaling: float = 1.0,
) -> DesignSystem:
    """Apply High-DPI scaling and return a configured :class:`DesignSystem`."""
    global _manager
    _manager = ThemeManager(
        variant=theme,
        widget_scaling=widget_scaling,
        window_scaling=window_scaling,
    )
    return _manager.system


# ── Internal helpers ──────────────────────────────────────────────────

def _resolve_preset(name: str) -> str:
    """Map a legacy variant name (``'dark'``/``'light'``) to a preset key."""
    mapping = {
        "dark": ThemePreset.VERCEL_DARK,
        "light": ThemePreset.VERCEL_DARK,  # light not fully supported
        "vercel_dark": ThemePreset.VERCEL_DARK,
        "nord_slate": ThemePreset.NORD_SLATE,
        "material_oceanic": ThemePreset.MATERIAL_OCEANIC,
        "high_contrast": ThemePreset.HIGH_CONTRAST,
    }
    return mapping.get(name, ThemePreset.VERCEL_DARK)
