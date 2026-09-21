"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Context-aware IntelliSense completions for the sqlean-lint PRO GUI.

Polls the main ``CTkTextbox`` for the token immediately preceding the
cursor and offers a lightweight dropdown of:

* SQL keywords (``SELECT``, ``WHERE``, ``JOIN`` ...) ranked by position in a
  statement (start-of-statement, after-``SELECT``, after-``WHERE`` ...),
* table names registered by the host (typically the active file explorer
  or the DuckDB catalog),
* column names registered by the dashboard (from profiling).

The dropdown is a topmost, borderless overlay positioned at the caret that
updates on ``KeyRelease`` (debounced) and commits the chosen suggestion back
into the editor. It builds ``CTk`` widgets but never starts ``mainloop`` so
it is headless-safe.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import customtkinter as ctk

# ── Design tokens ─────────────────────────────────────────────────────
_SURFACE = "#24283b"
_ZONE = "#1f2335"
_ACCENT = "#7aa2f7"
_TEXT = "#c0caf5"
_MUTED = "#565f89"
_HIGHLIGHT = "#3b4261"

_KEYWORDS: List[str] = [
    "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT",
    "OFFSET", "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN",
    "FULL JOIN", "CROSS JOIN", "ON", "AS", "AND", "OR", "NOT", "IN",
    "IS NULL", "IS NOT NULL", "DISTINCT", "UNION", "UNION ALL", "WITH",
    "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM", "CREATE TABLE",
    "ALTER TABLE", "DROP TABLE", "CASE", "WHEN", "THEN", "ELSE", "END",
    "EXISTS", "BETWEEN", "LIKE", "ILIKE", "DESC", "ASC",
]

_FUNCTIONS: List[str] = [
    "COUNT(", "SUM(", "AVG(", "MIN(", "MAX(", "COALESCE(", "CAST(",
    "ROUND(", "ABS(", "LOWER(", "UPPER(", "TRIM(", "LENGTH(", "SUBSTR(",
    "REPLACE(", "DATE(", "NOW()", "IFNULL(", "NULLIF(", "GREATEST(", "LEAST(",
]

# Which keywords are desirable given the last completed token.
_CONTEXT_RULES: Dict[str, List[str]] = {
    "": ["SELECT", "WITH", "INSERT INTO", "UPDATE", "DELETE FROM", "CREATE TABLE"],
    "SELECT": ["FROM", "DISTINCT", "COUNT(", "SUM(", "MAX(", "*"],
    "FROM": ["WHERE", "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "GROUP BY"],
    "JOIN": ["ON", "WHERE", "AND"],
    "LEFT JOIN": ["ON", "WHERE"],
    "RIGHT JOIN": ["ON", "WHERE"],
    "WHERE": ["AND", "OR", "GROUP BY", "BETWEEN", "LIKE", "IN", "IS NULL"],
    "GROUP BY": ["ORDER BY", "HAVING", "LIMIT"],
    "HAVING": ["ORDER BY", "LIMIT"],
    "ORDER BY": ["LIMIT", "ASC", "DESC"],
    "ON": ["AND", "OR"],
    "AND": ["IS NULL", "IS NOT NULL", "BETWEEN", "LIKE", "IN"],
    "OR": ["IS NULL", "IS NOT NULL", "BETWEEN", "LIKE", "IN"],
    "UPDATE": ["SET"],
    "SET": ["WHERE"],
    "INSERT INTO": ["VALUES", "SELECT"],
    "DELETE FROM": ["WHERE"],
}

_IGNORE_CHARS = set(" \t\n;,()")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class IntelliSense:
    """Dropdown completion provider bound to a ``CTkTextbox`` editor.

    Args:
        editor: The ``CTkTextbox`` whose content is being completed.
        root:   The top-level window used to parent the overlay.
        keywords: Optional override keyword list (defaults to built-ins).
    """

    def __init__(
        self,
        editor: ctk.CTkTextbox,
        root,
        keywords: Optional[List[str]] = None,
    ) -> None:
        self._editor = editor
        self._root = root
        self._keywords = list(keywords or _KEYWORDS)

        # Registered catalog entries (tables + columns) supplied by host.
        self._tables: List[str] = []
        self._columns: List[str] = []

        self._overlay: Optional[ctk.CTkToplevel] = None
        self._list_frame = None
        self._rows: List = []
        self._selection: int = 0
        self._candidates: List[str] = []
        self._prefix: str = ""
        self._insert_index: int = 0  # index where replacement begins
        self._suppress: bool = False

        # Bind to the editor.
        editor.bind("<KeyRelease>", self._on_key, add="+")
        editor.bind("<Control-space>", self._force_show, add="+")

    # ── public API ─────────────────────────────────────────────────────

    def set_tables(self, tables: List[str]) -> None:
        """Register table names as completion candidates."""
        self._tables = list(tables)

    def set_columns(self, columns: List[str]) -> None:
        """Register column names as completion candidates."""
        self._columns = list(columns)

    def suggestions(self) -> List[str]:
        """Return the current candidate list (useful for tests)."""
        return list(self._candidates)

    # ── internals ──────────────────────────────────────────────────────

    def _on_key(self, event) -> None:
        if event.keysym in ("Escape", "Return", "Tab", "Up", "Down"):
            return
        if event.keysym == "space" and event.state & 0x4:  # Ctrl+space
            self._force_show(None)
            return
        self._debounced_analyze()

    def _force_show(self, _event=None) -> None:
        self._debounced_analyze(force=True)

    def _debounced_analyze(self, force: bool = False) -> None:
        # Debounce with after().  Store pending id to cancel earlier calls.
        pending = getattr(self, "_pending_after", None)
        if pending is not None:
            try:
                self._editor.after_cancel(pending)
            except Exception:  # noqa: BLE001
                pass
        after_id = self._editor.after(
            120, lambda: self._analyze(force=force)
        )
        self._pending_after = after_id

    def _current_word(self) -> str:
        """Return the partial token before the cursor."""
        try:
            idx = self._editor.index("insert")
            line_start = self._editor.index(f"{idx} linestart")
            prefix = self._editor.get(line_start, idx)
            m = _TOKEN_RE.search(prefix)
            return m.group(0) if m else ""
        except Exception:  # noqa: BLE001
            return ""

    def _context_keyword(self) -> str:
        """Return the last major keyword before the cursor (for ranking)."""
        try:
            start = self._editor.index("1.0")
            idx = self._editor.index("insert")
            before = self._editor.get(start, idx).upper()
        except Exception:  # noqa: BLE001
            return ""
        words = re.findall(r"[A-Z_]+", before)
        for w in reversed(words):
            if w in _CONTEXT_RULES:
                return w
        return ""

    def _analyze(self, force: bool = False) -> None:
        word = self._current_word()
        if not force and not word:
            self._hide()
            return

        context = self._context_keyword()
        ranked = list(_CONTEXT_RULES.get(context, []))
        pool: List[str] = []

        for kw in self._keywords:
            if kw.upper() in ranked and kw not in pool:
                pool.append(kw.upper())
        for kw in self._keywords:
            if kw.upper() not in pool:
                pool.append(kw.upper())
        for t in self._tables:
            if t not in pool:
                pool.append(t)
        for c in self._columns:
            if c not in pool:
                pool.append(c)
        pool.extend(f for f in _FUNCTIONS if f.rstrip("(") not in pool)

        if word:
            wl = word.lower()
            matches = [c for c in pool if c.lower().startswith(wl)]
        else:
            matches = pool

        if not matches:
            self._hide()
            return

        self._prefix = word
        self._insert_index = self._caret_insert_index()
        self._candidates = matches[:8]
        self._selection = 0
        self._show_overlay()
        self._paint()

    def _caret_insert_index(self) -> int:
        """Return the char index (1-based) of the caret within the textbox."""
        idx = self._editor.index("insert")
        try:
            line, col = (int(x) for x in idx.split("."))
        except Exception:  # noqa: BLE001
            return 1
        line_start = f"{line}.0"
        return len(self._editor.get("1.0", line_start)) + col + 1

    def _show_overlay(self) -> None:
        if self._overlay is None or not self._overlay.winfo_exists():
            overlay = ctk.CTkToplevel(self._root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.configure(fg_color=_SURFACE)
            frame = ctk.CTkScrollableFrame(
                overlay, fg_color="transparent", width=260, height=200
            )
            frame.pack(fill="both", expand=True)
            self._overlay = overlay
            self._list_frame = frame
        self._position_overlay()
        self._overlay.deiconify()
        self._overlay.lift()
        self._overlay.attributes("-topmost", True)

    def _position_overlay(self) -> None:
        try:
            bbox = self._editor.bbox("insert")
            if not bbox:
                return
            x, y, _w, _h = bbox
            ex = self._editor.winfo_rootx()
            ey = self._editor.winfo_rooty()
            self._overlay.geometry(f"+{ex + x}+{ey + y + 22}")
        except Exception:  # noqa: BLE001
            pass

    def _paint(self) -> None:
        if self._list_frame is None:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()
        self._rows = []
        for i, cand in enumerate(self._candidates):
            label = ctk.CTkLabel(
                self._list_frame,
                text=cand,
                font=ctk.CTkFont("Consolas", 12),
                anchor="w",
            )
            label.pack(fill="x", padx=6, pady=1)
            label.bind("<Button-1>", lambda _e, idx=i: self._accept(idx))
            self._rows.append(label)
        self._paint_selection()

    def _paint_selection(self) -> None:
        for i, label in enumerate(self._rows):
            active = i == self._selection
            label.configure(
                fg_color=_HIGHLIGHT if active else "transparent",
                text_color=_ACCENT if active else _TEXT,
                font=ctk.CTkFont("Consolas", 12, "bold" if active else "normal"),
            )

    def _accept(self, idx: Optional[int] = None) -> None:
        i = self._selection if idx is None else idx
        if not self._candidates:
            self._hide()
            return
        c = self._candidates[i % len(self._candidates)]
        self._hide()
        self._replace_current(c)

    def _replace_current(self, candidate: str) -> None:
        try:
            start = self._editor.index("insert")
            if self._prefix:
                line, col = (int(x) for x in str(start).split("."))
                back = len(self._prefix)
                new_col = max(0, col - back)
                start = f"{line}.{new_col}"
            self._editor.delete(start, "insert")
            self._editor.insert("insert", candidate)
            self._editor.focus_set()
        except Exception:  # noqa: BLE001
            pass

    def _hide(self) -> None:
        if self._overlay is not None and self._overlay.winfo_exists():
            self._overlay.withdraw()
    hide = _hide

    def key(self, keysym: str) -> bool:
        """Handle navigation keystrokes; return True if consumed."""
        if self._overlay is None or not self._overlay.winfo_exists():
            return False
        if keysym == "Return":
            self._accept()
            return True
        if keysym == "Tab":
            self._accept()
            return True
        if keysym == "Escape":
            self._hide()
            return True
        if keysym == "Up":
            if self._candidates:
                self._selection = (self._selection - 1) % len(self._candidates)
                self._paint_selection()
            return True
        if keysym == "Down":
            if self._candidates:
                self._selection = (self._selection + 1) % len(self._candidates)
                self._paint_selection()
            return True
        return False
