"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Split-pane code visualizer for the sqlean-lint PRO GUI.

Provides:

* :func:`apply_syntax_highlighting` -- a tokenizer that applies Tk text tags to
  colourise SQL in a ``CTkTextbox`` using the dark-mode design tokens
  (keywords, strings, numbers, functions, operators, comments, identifiers).
* :class:`SplitCodeDiffViewer` -- two side-by-side ``CTkTextbox`` widgets
  (Original vs. Optimized) with a shared syntax-highlighting routine and a
  lightweight line-diff summary.

The modules only *build* widgets; they never start ``mainloop`` so they are
safe to import in headless QA.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from .design_system import TOKENS

# ── Design tokens (dark mode) ─────────────────────────────────────────
TOKEN_COLORS: Dict[str, str] = {
    "bg": "#1f2335",
    "text": "#c0caf5",
    "keyword": "#bb9af7",
    "string": "#9ece6a",
    "number": "#ff9e64",
    "function": "#7dcfff",
    "operator": "#89ddff",
    "comment": "#565f89",
    "column": "#7aa2f7",
    "annotation": "#e0af68",
}

# Ordered tokenization rules: (tag, combined regex). The regexes are joined
# with alternation and applied as one scan; each capture group maps to a tag.
_TOKEN_RULES: List[Tuple[str, str]] = [
    ("COMMENT", r"(--[^\n]*|/\*.*?\*/)"),
    ("STRING", r"'(?:[^']|'')*'|(?<!:)\"[^\"\n]*\""),
    ("NUMBER", r"\b\d+(?:\.\d+)?\b"),
    ("KEYWORD", r"\b(?:SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|"
                r"ON|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|AS|AND|OR|NOT|IN|"
                r"IS|NULL|DISTINCT|CREATE|TABLE|INSERT|INTO|VALUES|UPDATE|"
                r"SET|DELETE|DROP|ALTER|ADD|CASE|WHEN|THEN|ELSE|END|"
                r"WITH|UNION|ALL|EXISTS|BETWEEN|LIKE|ILIKE|DESC|ASC)\b"),
    ("FUNCTION", r"\b(?:COUNT|SUM|AVG|MIN|MAX|COALESCE|CAST|CONVERT|ROUND|"
                 r"ABS|LOWER|UPPER|TRIM|LTRIM|RTRIM|LENGTH|SUBSTR|REPLACE|"
                 r"DATE|DATETIME|TIMESTAMP|YEAR|MONTH|DAY|NOW|CURRENT_DATE|"
                 r"CURRENT_TIMESTAMP|IFNULL|NULLIF|GREATEST|LEAST|CEIL|FLOOR)\s*\("),
    ("COLUMN", r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"),
    ("OPERATOR", r"(?:<=|>=|<>|!=|==|=|<|>|\+|-|\*|/|%|\(|\)|,|;|\.)"),
]

# Tag names exposed to callers.
TAG_KEYWORD = "kw"
TAG_STRING = "str"
TAG_NUMBER = "num"
TAG_FUNCTION = "fn"
TAG_OPERATOR = "op"
TAG_COMMENT = "cmt"
TAG_COLUMN = "col"

_TAG_NAMES = {
    "KEYWORD": TAG_KEYWORD,
    "STRING": TAG_STRING,
    "NUMBER": TAG_NUMBER,
    "FUNCTION": TAG_FUNCTION,
    "OPERATOR": TAG_OPERATOR,
    "COMMENT": TAG_COMMENT,
    "COLUMN": TAG_COLUMN,
}

_MASTER_PATTERN = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_RULES),
    re.IGNORECASE,
)


def apply_syntax_highlighting(
    textbox: ctk.CTkTextbox,
    sql: str,
    *,
    start: str = "1.0",
    clear_prev: bool = True,
) -> None:
    """Colourise *sql* into *textbox* using pygments with batched tag_add.

    Token ranges are first bucketed by tag name, then applied with multi-index
    ``tag_add(*chunk)`` calls (≤ 400 indices per call) instead of one call per
    token.  This avoids the C↔Tcl bridge lockups that a naive per-token loop
    triggers on large buffers.

    Args:
        textbox: A ``CTkTextbox`` or raw Tk ``Text`` widget.
        sql: The SQL source to tokenize and tag.
        start: Tk index at which the SQL text begins.
        clear_prev: Remove previously applied syntax tags before re-tagging.
    """
    try:
        from pygments.lexers.sql import SqlLexer
        from pygments.token import Token
    except Exception:  # noqa: BLE001 - pygments is optional for highlighting
        return
    configure_textbox_tags(textbox)
    text_widget = _underlying_text(textbox)

    if not sql or len(sql) > 100_000:
        for tag in _TAG_NAMES.values():
            try:
                text_widget.tag_remove(tag, "1.0", "end")
            except Exception:  # noqa: BLE001
                pass
        return

    # Bucket character index pairs by tag name (drop positional formatting).
    buckets = {
        TAG_KEYWORD: [],
        TAG_STRING: [],
        TAG_COMMENT: [],
        TAG_FUNCTION: [],
        TAG_NUMBER: [],
    }
    try:
        pos = 0
        lexer = SqlLexer()
        for ttype, value in lexer.get_tokens(sql):
            if not value:
                continue
            end = pos + len(value)
            if ttype in Token.Keyword:
                bucket = TAG_KEYWORD
            elif ttype in Token.String:
                bucket = TAG_STRING
            elif ttype in Token.Comment:
                bucket = TAG_COMMENT
            elif ttype in Token.Name.Function or ttype in Token.Name.Builtin:
                bucket = TAG_FUNCTION
            elif ttype in Token.Number:
                bucket = TAG_NUMBER
            else:
                pos = end
                continue
            buckets[bucket].extend((f"{start}+{pos}c", f"{start}+{end}c"))
            pos = end
    except Exception:  # noqa: BLE001
        return

    if clear_prev:
        for tag in _TAG_NAMES.values():
            try:
                text_widget.tag_remove(tag, "1.0", "end")
            except Exception:  # noqa: BLE001
                pass

    # Apply ranges in batches of 400 indices to prevent Tcl argument overflow.
    BATCH = 400
    for tag, ranges in buckets.items():
        if not ranges:
            continue
        for i in range(0, len(ranges), BATCH):
            chunk = ranges[i:i + BATCH]
            try:
                text_widget.tag_add(tag, *chunk)
            except Exception:  # noqa: BLE001
                continue


def configure_textbox_tags(textbox: ctk.CTkTextbox) -> None:
    """Configure the syntax colour tags once on the underlying Tk text widget.

    This is idempotent; configuring a tag twice is a harmless no-op in Tk.
    """
    text_widget = _underlying_text(textbox)
    tag_configs = {
        TAG_KEYWORD: (TOKEN_COLORS["keyword"], "bold"),
        TAG_STRING: (TOKEN_COLORS["string"], "normal"),
        TAG_NUMBER: (TOKEN_COLORS["number"], "normal"),
        TAG_FUNCTION: (TOKEN_COLORS["function"], "bold"),
        TAG_OPERATOR: (TOKEN_COLORS["operator"], "normal"),
        TAG_COMMENT: (TOKEN_COLORS["comment"], "italic"),
        TAG_COLUMN: (TOKEN_COLORS["column"], "normal"),
    }
    for tag, (color, weight) in tag_configs.items():
        try:
            text_widget.tag_config(tag, foreground=color, font=(tag, weight))
        except Exception:  # noqa: BLE001
            pass


def _underlying_text(textbox: ctk.CTkTextbox):
    """Return the inner Tk Text widget exposed by a ``CTkTextbox``."""
    if hasattr(textbox, "_textbox"):
        return textbox._textbox
    return textbox


def _row_count(sql: str) -> int:
    return len(sql.splitlines()) if sql else 0



class SplitCodeDiffViewer(ctk.CTkFrame):
    """Two-pane Original vs. Optimized SQL viewer with syntax highlighting.

    Renders a header strip with column titles plus a summary line, and two
    scrollable ``CTkTextbox`` widgets side by side. The **Original** pane is
    wrapped with a full editor shell (Phase 1):

    * ``TkLineNumbers`` gutter (``tklinenums``) synchronised with scrolling.
    * A 12px overview ``Canvas`` ruler that draws diagnostics ticks; clicking
      a tick scrolls the editor straight to that line.
    * Debounced (300 ms) ``pygments`` lexical highlighting.
    * A right-click context menu (Run Selection / Format / Copy / Paste /
      Explain Plan).

    Wraps assets so a caller can later brand the panes.
    """

    def __init__(
        self,
        master: Any,
        *,
        left_title: str = "Original",
        right_title: str = "Optimized",
        bg_color: str = "#24283b",
        zone_color: str = "#1f2335",
        text_color: str = "#c0caf5",
        accent_color: str = "#7aa2f7",
        ok_color: str = "#9ece6a",
        muted_color: str = "#565f89",
        font_family: str = "Consolas",
        font_size: int = 12,
        on_run_selection: Optional[Callable[[], None]] = None,
        on_format_sql: Optional[Callable[[], None]] = None,
        on_explain_plan: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=10)
        self._zone = zone_color
        self._text_color = text_color
        self._accent = accent_color
        self._ok = ok_color
        self._muted = muted_color
        self._font = ctk.CTkFont(font_family, size=font_size)
        self._on_run_selection = on_run_selection
        self._on_format_sql = on_format_sql
        self._on_explain_plan = on_explain_plan

        # Phase 1 state.
        self._violations: List[Tuple[int, str]] = []
        self._debounce_after: Optional[str] = None
        self._menu = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header(left_title, right_title)
        self._build_panes()

    def _build_header(self, left_title: str, right_title: str) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=(2, 4))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text=left_title,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=self._muted,
        ).grid(row=0, column=0, sticky="w", padx=6)

        self._summary = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=self._accent,
        )
        self._summary.grid(row=0, column=0, sticky="e", padx=6)

        ctk.CTkLabel(
            header,
            text=right_title,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=self._ok,
        ).grid(row=0, column=1, sticky="w", padx=6)

    def _build_panes(self) -> None:
        # Left column hosts: line-number gutter | editor | overview ruler.
        self._left_box = ctk.CTkFrame(self, fg_color="transparent")
        self._left_box.grid(row=2, column=0, padx=(2, 1), pady=(0, 2), sticky="nsew")
        self._left_box.grid_columnconfigure(1, weight=1)
        self._left_box.grid_rowconfigure(0, weight=1)

        # Gutter host (line numbers).
        self._gutter_host = ctk.CTkFrame(self._left_box, fg_color=self._zone)
        self._gutter_host.grid(row=0, column=0, sticky="ns")

        self._left = self._make_pane(self._left_box)
        self._left.grid(row=0, column=1, sticky="nsew")

        # Overview ruler (12 px diagnostics heatmap).
        import tkinter as tk

        self._ruler = tk.Canvas(
            self._left_box, width=12, bg=self._zone,
            highlightthickness=0, bd=0, yscrollincrement=1,
        )
        self._ruler.grid(row=0, column=2, sticky="ns")
        self._ruler.bind("<Button-1>", self._on_ruler_click)
        self._ruler.bind("<Configure>", lambda e: self._draw_ruler())

        self._right = self._make_pane(self)
        self._right.grid(row=2, column=1, padx=(1, 2), pady=(0, 2), sticky="nsew")

        self._attach_gutter()
        self._bind_editor_extras()

    def _make_pane(self, master) -> ctk.CTkTextbox:
        return ctk.CTkTextbox(
            master,
            font=self._font,
            fg_color=self._zone,
            text_color=self._text_color,
            corner_radius=8,
            wrap="none",
            height=260,
        )

    # ?? Phase 1: gutter ????????????????????????????????????????????????

    def _attach_gutter(self) -> None:
        try:
            from tklinenums import TkLineNumbers

            text = _underlying_text(self._left)
            self._gutter = TkLineNumbers(
                self._gutter_host,
                text,
                justify="right",
                colors=(TOKENS["PANEL_BG"], TOKENS["TEXT_MUTED"]),
            )
            self._gutter.pack(side="left", fill="y")
        except Exception:  # noqa: BLE001 - gutter is an optional enhancement
            self._gutter = None
        try:
            text = _underlying_text(self._left)
            text.bind("<<Change>>", lambda e: self._sync_gutter(), add="+")
            text.bind("<MouseWheel>", lambda e: self._sync_gutter(), add="+")
        except Exception:  # noqa: BLE001
            pass

    def _sync_gutter(self) -> None:
        if getattr(self, "_gutter", None) is not None:
            try:
                self._gutter.redraw()
            except Exception:  # noqa: BLE001
                pass

    # ?? Phase 1: overview ruler ????????????????????????????????????????

    def set_ruler_violations(self, violations: Sequence[Tuple[int, str]]) -> None:
        """Feed ``(line, severity)`` diagnostics into the overview ruler.

        Severity ``"CRITICAL"`` (and ``SQL-CART-*`` / parse failures) render
        red ``#EF4444``; ``"HIGH"``/warning levels render amber ``#F59E0B``.
        Clicking a tick scrolls the editor to that line.
        """
        self._violations = list(violations)
        try:
            self._draw_ruler()
        except Exception:  # noqa: BLE001
            pass

    def _draw_ruler(self) -> None:
        if not hasattr(self, "_ruler"):
            return
        try:
            self._ruler.delete("all")
            text = _underlying_text(self._left)
            total = int(text.index("end-1c").split(".")[0])
            height = self._ruler.winfo_height()
            if height <= 0:
                return
            red = "#EF4444"
            amber = "#F59E0B"
            for line, severity in self._violations:
                if total <= 0:
                    continue
                frac = max(0.0, min(1.0, (line - 1) / total))
                y = int(frac * (height - 2)) + 1
                color = red if severity in ("CRITICAL", "ERROR") else amber
                self._ruler.create_line(
                    3, y, 9, y, fill=color, width=2, tags=("tick",),
                )
        except Exception:  # noqa: BLE001
            pass

    def _on_ruler_click(self, event) -> None:
        try:
            text = _underlying_text(self._left)
            total = int(text.index("end-1c").split(".")[0])
            height = self._ruler.winfo_height()
            if total <= 0 or height <= 0:
                return
            frac = max(0.0, min(1.0, event.y / height))
            line = max(1, int(frac * total + 0.5))
            text.see(f"{line}.0")
        except Exception:  # noqa: BLE001
            pass

    # ?? Phase 1: context menu ??????????????????????????????????????????

    def _bind_editor_extras(self) -> None:
        try:
            text = _underlying_text(self._left)
            text.bind("<Button-3>", self._show_context_menu, add="+")
        except Exception:  # noqa: BLE001
            pass

    def _show_context_menu(self, event) -> None:
        import tkinter as tk

        text = _underlying_text(self._left)
        menu = tk.Menu(text, tearoff=0)
        menu.add_command(
            label="Run Selection (F5)",
            command=self._on_run_selection or (lambda: None),
        )
        menu.add_command(
            label="Format SQL",
            command=self._on_format_sql or (lambda: None),
        )
        menu.add_separator()
        menu.add_command(label="Copy", command=lambda: self._editor_copy())
        menu.add_command(label="Paste", command=lambda: self._editor_paste())
        menu.add_separator()
        menu.add_command(
            label="Explain Plan",
            command=self._on_explain_plan or (lambda: None),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:  # noqa: BLE001
                pass

    def _editor_copy(self) -> None:
        try:
            text = _underlying_text(self._left)
            text.event_generate("<<Copy>>")
        except Exception:  # noqa: BLE001
            pass

    def _editor_paste(self) -> None:
        try:
            text = _underlying_text(self._left)
            text.event_generate("<<Paste>>")
        except Exception:  # noqa: BLE001
            pass

    # ?? public API ?????????????????????????????????????????????????????

    def render(self, original: str, optimized: str) -> None:
        """Populate both panes and apply syntax highlighting."""
        self._apply_text(self._left, original)
        self._apply_text(self._right, optimized)
        self._update_summary(original, optimized)
        self._draw_ruler()

    def _apply_text(self, pane: ctk.CTkTextbox, sql: str) -> None:
        pane.configure(state="normal")
        pane.delete("1.0", "end")
        pane.insert("1.0", sql)
        if pane is self._left:
            self._schedule_pygments(sql)
        else:
            apply_syntax_highlighting(pane, sql)
        try:
            text_widget = _underlying_text(pane)
            text_widget.configure(state="normal")
            text_widget.tag_config("cp", background=self._muted)
        except Exception:  # noqa: BLE001
            pass
        pane.configure(state="disabled")
        self._sync_gutter()

    def _schedule_pygments(self, sql: str) -> None:
        if self._debounce_after is not None:
            try:
                self.after_cancel(self._debounce_after)
            except Exception:  # noqa: BLE001
                pass
        self._debounce_after = self.after(300, lambda: self._pygments_highlight(sql))

    def _pygments_highlight(self, sql: str) -> None:
        from pygments import lex
        from pygments.lexers.sql import SqlLexer
        from pygments.token import Comment, Keyword, Name, Number, String

        try:
            text = _underlying_text(self._left)
            if len(sql) > 100_000:
                for tag in ("pkw", "pstr", "pcom", "pfn", "pnum"):
                    try:
                        text.tag_remove(tag, "1.0", "end")
                    except Exception:  # noqa: BLE001
                        pass
                return
            text.tag_config(
                "pkw", foreground=TOKENS["ACCENT_CYAN"],
            )
            text.tag_config(
                "pfn", foreground=TOKENS["ACCENT_EMERALD"],
            )
            text.tag_config(
                "pstr", foreground=TOKENS["TEXT_MUTED"],
            )
            text.tag_config(
                "pcom", foreground=TOKENS["TEXT_MUTED"],
            )
            text.tag_config("pnum", foreground=TOKENS["ACCENT_PRIMARY"])
            text.tag_remove("pkw", "1.0", "end")
            text.tag_remove("pstr", "1.0", "end")
            text.tag_remove("pcom", "1.0", "end")
            text.tag_remove("pfn", "1.0", "end")
            text.tag_remove("pnum", "1.0", "end")
            # Bucket index pairs by tag, then apply in batches of ≤ 400 indices
            # per tag_add call to avoid C↔Tcl bridge lockups on large buffers.
            buckets = {"pkw": [], "pstr": [], "pcom": [], "pfn": [], "pnum": []}
            lexer = SqlLexer()
            pos = 0
            for ttype, value in lex(sql, lexer):
                if not value:
                    continue
                end = pos + len(value)
                if ttype in String:
                    bucket = "pstr"
                elif ttype in Comment:
                    bucket = "pcom"
                elif ttype in Keyword:
                    bucket = "pkw"
                elif ttype in Name.Function or ttype in Name.Builtin:
                    bucket = "pfn"
                elif ttype in Number:
                    bucket = "pnum"
                else:
                    pos = end
                    continue
                buckets[bucket].extend((f"1.0+{pos}c", f"1.0+{end}c"))
                pos = end
            for tag, ranges in buckets.items():
                if not ranges:
                    continue
                for i in range(0, len(ranges), 400):
                    chunk = ranges[i:i + 400]
                    try:
                        text.tag_add(tag, *chunk)
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001 - highlighting never crashes the editor
            pass

    def _update_summary(self, original: str, optimized: str) -> None:
        o_rows, n_rows = _row_count(original), _row_count(optimized)
        delta = n_rows - o_rows
        if delta == 0:
            summary = "No line changes"
        elif delta > 0:
            summary = f"+{delta} lines"
        else:
            summary = f"{delta} lines"
        self._summary.configure(text=summary)

    def get_original_pane(self) -> ctk.CTkTextbox:
        return self._left

    def get_optimized_pane(self) -> ctk.CTkTextbox:
        return self._right

    def enable_editing(self) -> None:
        """Enable direct user input on the left-hand original code canvas."""
        self._left.configure(state="normal")
        self._left.focus_set()

    def set_content(self, text: str) -> None:
        """Replace the editor buffer and re-apply syntax highlighting."""
        self._left.configure(state="normal")
        self._left.delete("1.0", "end")
        self._left.insert("1.0", text)
        self._schedule_pygments(text)
        self._sync_gutter()
