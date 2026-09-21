# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""Persistent query-history ledger for the sqlean-lint PRO GUI.

* :class:`QueryHistoryLedger` is a pure, thread-safe, JSON-persisted ledger of
  every executed query (SQL text, timestamp, elapsed ms, row count and success
  status). It is fully offline — no network calls.
* :class:`QueryHistoryDrawer` is a CustomTkinter sidebar screen that renders
  the ledger and lets the user click any historical entry to reload it into
  the active editor tab.

The widget builds ``CTk`` children lazily and never runs ``mainloop``, so it
is headless-safe (matching the other ``ui`` components).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

# Default persistence location: alongside the user data dir, gitignored.
DEFAULT_HISTORY_PATH = Path(
    os.environ.get("SQLEAN_LINT_HISTORY", str(Path.home() / ".sqlean_lint"))
) / "query_history.json"

MAX_ENTRIES = 500


@dataclass
class HistoryEntry:
    """One recorded query execution."""

    sql: str
    timestamp: str = ""
    elapsed_ms: int = 0
    row_count: int = 0
    success: bool = True
    dialect: str = "duckdb"

    @property
    def preview(self) -> str:
        first = " ".join(self.sql.strip().splitlines())[:80]
        return first or "(empty query)"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryEntry":
        try:
            return cls(
                sql=str(data.get("sql", "")),
                timestamp=str(data.get("timestamp", "")),
                elapsed_ms=int(data.get("elapsed_ms", 0)),
                row_count=int(data.get("row_count", 0)),
                success=bool(data.get("success", True)),
                dialect=str(data.get("dialect", "duckdb")),
            )
        except Exception:  # noqa: BLE001 - degrade on corrupt entries
            return cls(sql="")


class QueryHistoryLedger:
    """Session-managed, JSON-persisted history ledger.

    Thread-safe via a mutex so the GUI worker thread can record executions
    while the main Tk thread renders them.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or DEFAULT_HISTORY_PATH
        self._entries: List[HistoryEntry] = []
        self._lock = threading.Lock()
        self._load()

    # ── persistence ───────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries = raw.get("entries", raw) if isinstance(raw, dict) else raw
            self._entries = [
                HistoryEntry.from_dict(e) for e in entries if isinstance(e, dict)
            ][-MAX_ENTRIES:]
        except Exception:  # noqa: BLE001 - corrupt history degrades gracefully
            self._entries = []

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"entries": [e.to_dict() for e in self._entries]}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:  # noqa: BLE001 - persistence is best-effort
            pass

    # ── mutation ──────────────────────────────────────────────────────

    def record(
        self,
        sql: str,
        *,
        elapsed_ms: int = 0,
        row_count: int = 0,
        success: bool = True,
        dialect: str = "duckdb",
    ) -> Optional[HistoryEntry]:
        """Append a query execution to the ledger. Never raises."""
        if not sql or not str(sql).strip():
            return None
        try:
            entry = HistoryEntry(
                sql=str(sql),
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                elapsed_ms=int(elapsed_ms),
                row_count=int(row_count),
                success=bool(success),
                dialect=str(dialect),
            )
            with self._lock:
                self._entries.append(entry)
                if len(self._entries) > MAX_ENTRIES:
                    self._entries = self._entries[-MAX_ENTRIES:]
            self._persist()
            return entry
        except Exception:  # noqa: BLE001 - recording must never crash the GUI
            return None

    # ── queries ───────────────────────────────────────────────────────

    def all(self) -> List[HistoryEntry]:
        with self._lock:
            return list(reversed(self._entries))

    def search(self, query: str) -> List[HistoryEntry]:
        q = (query or "").strip().lower()
        entries = self.all()
        if not q:
            return entries
        return [
            e for e in entries
            if q in e.sql.lower() or q in e.preview.lower() or q in e.dialect.lower()
        ]

    def clear(self) -> None:
        with self._lock:
            self._entries = []
        self._persist()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class QueryHistoryDrawer(ctk.CTkFrame):
    """Searchable sidebar that lists the ledger and reloads entries on click."""

    _SURFACE = "#24283b"
    _ZONE = "#1f2335"
    _ACCENT = "#7aa2f7"
    _TEXT = "#c0caf5"
    _MUTED = "#565f89"
    _OK = "#9ece6a"
    _ERR = "#f7768e"

    def __init__(
        self,
        master,
        *,
        ledger: Optional[QueryHistoryLedger] = None,
        on_select: Optional[Callable[[HistoryEntry], None]] = None,
        bg_color: str = _SURFACE,
        zone_color: str = _ZONE,
        accent_color: str = _ACCENT,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        ok_color: str = _OK,
        err_color: str = _ERR,
        width: int = 280,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=0, width=width)
        self._ledger = ledger or QueryHistoryLedger()
        self._on_select = on_select
        self._surface = bg_color
        self._accent = accent_color
        self._zone = zone_color
        self._text = text_color
        self._muted = muted_color
        self._ok = ok_color
        self._err = err_color

        self.grid_propagate(False)

        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(
            heading, text="Query History", font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=self._accent,
        ).pack(side="left")
        self._count = ctk.CTkLabel(
            heading, text="0", font=ctk.CTkFont("Consolas", 10),
            text_color=self._muted,
        )
        self._count.pack(side="right")

        # Search filter box.
        self._search_entry = ctk.CTkEntry(
            self,
            placeholder_text="Search query history...",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=self._zone,
        )
        self._search_entry.pack(fill="x", padx=10, pady=(0, 6))
        self._search_entry.bind("<KeyRelease>", lambda _e: self.refresh())

        # Scrollable list of entries.
        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkButton(
            footer, text="Clear History", height=26,
            font=ctk.CTkFont("Segoe UI", 11), fg_color=self._zone,
            text_color=self._err, command=self._clear,
        ).pack(side="right")

        self.refresh()

    # ── rendering ─────────────────────────────────────────────────────

    def refresh(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        self._ledger and None
        query = self._search_entry.get()
        entries = self._ledger.search(query)
        self._count.configure(text=str(len(entries)))
        if not entries:
            empty = ctk.CTkLabel(
                self._list, text="No queries yet.\nRun a query to record it.",
                font=ctk.CTkFont("Segoe UI", 11), text_color=self._muted,
                justify="center",
            )
            empty.pack(pady=12)
            return
        for entry in entries:
            card = self._build_card(entry)
            card.pack(fill="x", pady=2)

    def _build_card(self, entry: HistoryEntry) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self._list, fg_color=self._zone, corner_radius=8)

        status = self._ok if entry.success else self._err
        line1 = ctk.CTkFrame(card, fg_color="transparent")
        line1.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(
            line1, text=entry.preview, font=ctk.CTkFont("Consolas", 10),
            text_color=self._text, anchor="w", justify="left",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            line1, text="●", font=ctk.CTkFont("Segoe UI", 12), text_color=status,
        ).pack(side="right", padx=(4, 0))

        meta = (
            f"{entry.timestamp}  ·  {entry.elapsed_ms} ms  ·  "
            f"{entry.row_count} rows"
        )
        ctk.CTkLabel(
            card, text=meta, font=ctk.CTkFont("Consolas", 9),
            text_color=self._muted, anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))

        button = ctk.CTkButton(
            card, text="Open in Editor", height=22,
            font=ctk.CTkFont("Segoe UI", 10), fg_color=self._surface,
            text_color=self._accent, command=lambda: self._select(entry),
        )
        button.pack(anchor="e", padx=8, pady=(0, 6))
        return card

    def _select(self, entry: HistoryEntry) -> None:
        if self._on_select is not None:
            try:
                self._on_select(entry)
            except Exception:  # noqa: BLE001 - selection is best-effort
                pass

    def _clear(self) -> None:
        self._ledger.clear()
        self.refresh()
