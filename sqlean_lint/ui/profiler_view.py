"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Live data-profiling side panel for the sqlean-lint PRO GUI.

Analyzes the columns referenced in the active SQL query and displays
instant summary statistics (total rows, null counts, uniqueness, type,
and a mini text histogram) in a KNIME-style scrollable panel.

* :func:`extract_columns` -- parses a SQL statement via sqlglot and returns
  the distinct column identifiers it references (with table qualifiers).
* :func:`profile_rows` -- pure function that computes per-column summary
  stats from a list of row dicts (usable headlessly and in tests).
* :class:`ProfilerView` -- the ``CTk`` widget that renders the stats for
  each column, with a small bar showing relative cardinality/uniqueness.

The widget builds ``CTkFrame``/``CTkLabel`` children on demand and never
runs ``mainloop``, so it is headless-safe.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

import customtkinter as ctk

import sqlglot

# ── Design tokens ─────────────────────────────────────────────────────
_SURFACE = "#24283b"
_ZONE = "#1f2335"
_ACCENT = "#7aa2f7"
_TEXT = "#c0caf5"
_MUTED = "#565f89"
_OK = "#9ece6a"
_WARN = "#e0af68"


def extract_columns(sql: str, dialect: str = "duckdb") -> List[str]:
    """Return distinct column names referenced by *sql* (best-effort).

    Uses sqlglot to walk the AST for :class:`~sqlglot.expressions.Column`
    nodes and returns their ``sql()`` rendering (e.g. ``o.cust_id``).

    Args:
        sql: The SQL statement text.
        dialect: sqlglot dialect name used for parsing.

    Returns:
        An ordered list of distinct column references.
    """
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - never raise on arbitrary input
        return []
    seen: List[str] = []
    seen_set = set()
    for node in ast.walk():
        if type(node).__name__ == "Column":
            ref = node.sql()
            if ref not in seen_set:
                seen_set.add(ref)
                seen.append(ref)
    return seen


def profile_rows(rows: List[Dict[str, Any]], limit: int = 100) -> Dict[str, dict]:
    """Compute summary statistics for each column across *rows*.

    Pure and deterministic; safe to call in tests without a Tk root.

    Args:
        rows: A list of row dictionaries (``{col: value}``).
        limit: Cap on the number of rows considered for stats.

    Returns:
        ``{column: {"count", "nulls", "unique", "type", "hist"} }`` where
        ``hist`` is a list of ``(bucket_label, value)`` pairs for display.
    """
    sample = rows[:limit]
    total = len(sample)
    if not total:
        return {}

    cols: List[str] = []
    for row in sample:
        for k in row:
            if k not in cols:
                cols.append(k)

    result: Dict[str, dict] = {}
    for col in cols:
        values = [r[col] for r in sample]
        nulls = sum(1 for v in values if v is None)
        present = [v for v in values if v is not None]
        unique = len(set(str(v) for v in present)) if present else 0

        # Infer a type from the first non-null value.
        kind = "text"
        first = next((v for v in present if v is not None), None)
        if isinstance(first, bool):
            kind = "boolean"
        elif isinstance(first, (int, float)):
            kind = "number"
        elif isinstance(first, (dict, list)):
            kind = "struct/list"

        # Lightweight histogram: 1-5 buckets for numbers/strings.
        hist = _build_hist(present, kind, 4)

        result[col] = {
            "count": total,
            "nulls": nulls,
            "unique": unique,
            "type": kind,
            "hist": hist,
        }
    return result


def _bucket_key(s: str, n: int, kind: str) -> str:
    if kind == "number":
        try:
            v = float(s)
        except Exception:  # noqa: BLE001
            return "other"
        lo = int(v // n)
        return f"[{lo * n}, {(lo + 1) * n})"
    # Text: bucket by first character-range.
    if not s:
        return "(blank)"
    return f"{s[0].upper()}-group"


def _build_hist(values: List[Any], kind: str, buckets: int = 4) -> List[tuple]:
    if not values:
        return []
    if kind == "number":
        strs = []
        for v in values:
            strs.append(str(v))
        return _numeric_hist(strs, buckets)
    # Text histogram: group by first letter.
    counts: Counter = Counter()
    for v in values:
        key = _bucket_key(str(v), buckets, kind)
        counts[key] += 1
    top = counts.most_common(buckets)
    total = sum(c for _, c in top)
    if not total:
        return []
    return [(k, c) for k, c in top]


def _numeric_hist(strs: List[str], buckets: int) -> List[tuple]:
    nums = []
    for s in strs:
        try:
            nums.append(float(s))
        except Exception:  # noqa: BLE001
            continue
    if not nums:
        return [("N/A", 0)]
    lo = min(nums)
    hi = max(nums)
    span = (hi - lo) or 1.0
    counts = [0] * buckets
    for n in nums:
        idx = min(buckets - 1, int((n - lo) / span * buckets))
        counts[idx] += 1
    out = []
    for i, c in enumerate(counts):
        b_lo = lo + (span * i) / buckets
        b_hi = lo + (span * (i + 1)) / buckets
        out.append((f"{b_lo:.1f}-{b_hi:.1f}", c))
    return out


class ProfilerView(ctk.CTkFrame):
    """Side panel showing per-column summary statistics."""

    WIDTH = 260

    def __init__(
        self,
        master,
        *,
        bg_color: str = _SURFACE,
        zone_color: str = _ZONE,
        accent_color: str = _ACCENT,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        ok_color: str = _OK,
        warn_color: str = _WARN,
    ) -> None:
        super().__init__(
            master, fg_color=bg_color, corner_radius=8, width=self.WIDTH
        )
        self.grid_propagate(False)
        self._zone = zone_color
        self._accent = accent_color
        self._text = text_color
        self._muted = muted_color
        self._ok = ok_color
        self._warn = warn_color

        self._current_stats: Dict[str, dict] = {}

        header = ctk.CTkLabel(
            self,
            text="Data Profile",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=self._accent,
        )
        header.pack(anchor="w", padx=12, pady=(10, 4))

        self._sub = ctk.CTkLabel(
            self,
            text="No query analyzed",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=self._muted,
        )
        self._sub.pack(anchor="w", padx=12, pady=(0, 6))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ── public API ─────────────────────────────────────────────────────

    def set_sql_source(self, sql: str, dialect: str = "duckdb") -> None:
        """Refresh the listed columns from *sql* (stats become placeholders)."""
        cols = extract_columns(sql, dialect)
        self._current_stats = {c: {"count": 0, "nulls": 0, "unique": 0,
                                   "type": "?", "hist": []}
                               for c in cols}
        if cols:
            self._sub.configure(text=f"{len(cols)} referenced columns")
        else:
            self._sub.configure(text="No columns parsed")
        self._render()

    def update_rows(self, rows: List[Dict[str, Any]], limit: int = 100) -> None:
        """Compute real stats from *rows* and re-render."""
        self._current_stats = profile_rows(rows, limit)
        n = len(rows)
        self._sub.configure(
            text=f"{len(self._current_stats)} columns - {n} rows profiled"
        )
        self._render()

    def stats(self) -> Dict[str, dict]:
        return {k: dict(v) for k, v in self._current_stats.items()}

    # ── rendering ──────────────────────────────────────────────────────

    def _render(self) -> None:
        for child in self._scroll.winfo_children():
            child.destroy()
        if not self._current_stats:
            empty = ctk.CTkLabel(
                self._scroll, text="No data yet",
                font=ctk.CTkFont("Segoe UI", 11), text_color=self._muted,
            )
            empty.pack(pady=14)
            return

        for col, st in self._current_stats.items():
            card = ctk.CTkFrame(self._scroll, fg_color=self._zone, corner_radius=8)
            card.pack(fill="x", pady=3)

            name = ctk.CTkLabel(
                card, text=col, font=ctk.CTkFont("Consolas", 11, "bold"),
                text_color=self._text, anchor="w",
            )
            name.pack(fill="x", padx=8, pady=(6, 0))

            meta = ctk.CTkLabel(
                card,
                text=f"type: {st['type']} | nulls: {st['nulls']} | "
                     f"unique: {st['unique']}/{st['count']}",
                font=ctk.CTkFont("Consolas", 9),
                text_color=self._muted,
            )
            meta.pack(fill="x", padx=8, pady=(0, 2))

            # Uniqueness mini-bar (0..1 -> WARN..OK gradient).
            unique = st["unique"]
            count = st.get("count") or 0
            ratio = (unique / count) if count else 0.0
            bar_colour = self._ok if ratio >= 0.5 else (
                self._warn if ratio >= 0.15 else self._accent
            )
            self._draw_bar(card, ratio, bar_colour)

            for label, value in st.get("hist", [])[:3]:
                hv = ctk.CTkLabel(
                    card,
                    text=f"{label}: {value}",
                    font=ctk.CTkFont("Consolas", 9),
                    text_color=self._muted,
                )
                hv.pack(fill="x", padx=8, pady=(0, 1))

    def _draw_bar(self, parent, ratio: float, colour: str) -> None:
        bar_bg = ctk.CTkFrame(parent, fg_color=self._muted, corner_radius=2,
                              height=5)
        bar_bg.pack(fill="x", padx=8, pady=2)
        bar_bg.pack_propagate(False)
        fill = ctk.CTkFrame(bar_bg, fg_color=colour, corner_radius=2, width=max(
            8, int(230 * max(0.0, min(1.0, ratio)))))
        fill.pack(side="left", fill="y")
