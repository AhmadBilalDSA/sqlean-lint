"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Visual execution-plan / DAG viewer for the sqlean-lint PRO GUI.

Parses a ``sqlglot`` AST and renders a node graph (directed acyclic graph)
on a ``CTkCanvas``:

* **Table nodes** — rectangles labelled with the table (and alias) name.
* **Join edges** — arrows between nodes labelled with the join kind
  (``INNER``, ``LEFT``, ``RIGHT``, ``CROSS``).
* **Problematic nodes** — CROSS / Cartesian joins (or any join missing an
  ``ON`` predicate) are flagged red, matching the dashboard's warning
  palette, to spotlight Cartesian-explosion risk.

The layout is a simple layered sweep: the ``FROM`` table sits at the left and
each subsequent``JOIN`` is laid out to the right, so the graph reads left to
right like a real execution plan.

This module only *builds* a canvas and draws; it never runs ``mainloop``, so
it is headless-safe. The pure parser :func:`build_dag` is unit-testable
without any Tk dependency.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import customtkinter as ctk

import sqlglot

# ── Design tokens ─────────────────────────────────────────────────────
_BG = "#1a1b26"
_SURFACE = "#24283b"
_ZONE = "#1f2335"
_ACCENT = "#7aa2f7"
_TEXT = "#c0caf5"
_MUTED = "#565f89"
_OK = "#9ece6a"
_WARN = "#e0af68"
_ERR = "#f7768e"
_NODE_BORDER = "#3b4261"

_BOX_W = 120
_BOX_H = 46
_H_GAP = 46
_V_GAP = 26


def _zoom_dim(value: float, zoom: float) -> int:
    """Return *value* scaled by *zoom*, rounded to a whole pixel."""
    return int(value * zoom)


class DagNode:
    """A table (or source) node in the execution plan graph."""

    __slots__ = ("name", "alias", "join_kind", "problem", "note", "_pos")

    def __init__(
        self,
        name: str,
        alias: str = "",
        join_kind: str = "FROM",
        problem: bool = False,
        note: str = "",
    ) -> None:
        self.name = name
        self.alias = alias
        self.join_kind = join_kind
        self.problem = problem
        self.note = note
        self._pos = None

    def label(self) -> str:
        return f"{self.name} AS {self.alias}" if self.alias else self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "alias": self.alias,
            "join_kind": self.join_kind,
            "problem": self.problem,
            "note": self.note,
        }


def _table_name(node) -> Optional[str]:
    """Extract the underlying table name from a sqlglot Table/expression."""
    try:
        return node.name
    except Exception:  # noqa: BLE001
        return None


def _alias_of(node) -> str:
    try:
        alias = node.args.get("alias")
        if alias is not None:
            return getattr(alias, "name", "") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _join_kind(node) -> str:
    """Return the human join kind for a sqlglot Join node.

    A plain ``JOIN`` (no ``kind`` arg) defaults to ``INNER``; ``CROSS`` comes
    from a ``CROSS JOIN``; a ``side`` arg (``LEFT``/``RIGHT``/``FULL``) is
    combined with the base kind.
    """
    try:
        base = str(node.args.get("kind") or "").upper()
        side = str(node.args.get("side") or "").upper()
        if base == "CROSS":
            return "CROSS JOIN"
        if side:
            return f"{side} JOIN"
        return "INNER JOIN"
    except Exception:  # noqa: BLE001
        return "JOIN"


def _has_on_condition(node) -> bool:
    try:
        return node.args.get("on") is not None
    except Exception:  # noqa: BLE001
        return False


def build_dag(ast) -> List[DagNode]:
    """Convert a sqlglot AST into an ordered list of ``DagNode`` objects.

    The first node is the ``FROM`` source; subsequent nodes are joins in
    query order. A node is flagged ``problem`` when its join lacks an ``ON``
    predicate (e.g. a naked ``CROSS JOIN`` or ``JOIN`` with no condition) --
    a classic Cartesian-explosion trigger.

    Args:
        ast: A sqlglot expression (e.g. from ``sqlglot.parse_one``).

    Returns:
        A list of :class:`DagNode` in left-to-right plan order.
    """
    nodes: List[DagNode] = []

    def add_table(table_node, kind: str) -> Optional[DagNode]:
        name = _table_name(table_node)
        if not name:
            return None
        node = DagNode(
            name=name,
            alias=_alias_of(table_node),
            join_kind=kind,
            problem=False,
        )
        nodes.append(node)
        return node

    for item in ast.walk():
        tname = type(item).__name__
        if tname == "From":
            for child in item.walk():
                if type(child).__name__ == "Table":
                    add_table(child, "FROM")
                    break
        elif tname == "Join":
            tables = child_tables(item)
            kind = _join_kind(item)
            has_on = _has_on_condition(item)
            for t in tables:
                problem = (kind in ("CROSS", "CROSS JOIN")) or not has_on
                node = DagNode(
                    name=t.name,
                    alias=_alias_of(t),
                    join_kind=kind,
                    problem=problem,
                    note=(
                        "Cartesian risk: no ON predicate"
                        if problem
                        else ""
                    ),
                )
                nodes.append(node)

    # De-duplicate: later joins on the same table (e.g. self-join) are kept.
    return nodes


def child_tables(node) -> List[Any]:
    """Return the Table expressions under a Join node."""
    out = []
    for child in node.walk():
        if type(child).__name__ == "Table":
            out.append(child)
    return out


def parse_sql(sql: str, dialect: str = "duckdb") -> List[DagNode]:
    """Parse SQL text and return its DAG nodes (best-effort)."""
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
        return build_dag(ast)
    except Exception:  # noqa: BLE001 - never raise on arbitrary input
        return []


class DagViewer(ctk.CTkFrame):
    """Scrollable canvas rendering a SQL execution-plan DAG."""

    def __init__(
        self,
        master,
        *,
        bg_color: str = _ZONE,
        surface_color: str = _SURFACE,
        accent_color: str = _ACCENT,
        text_color: str = _TEXT,
        muted_color: str = _MUTED,
        ok_color: str = _OK,
        warn_color: str = _WARN,
        err_color: str = _ERR,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8)
        self._surface = surface_color
        self._accent = accent_color
        self._text = text_color
        self._muted = muted_color
        self._ok = ok_color
        self._warn = warn_color
        self._err = err_color

        self._nodes: List[DagNode] = []
        self._canvas = ctk.CTkCanvas(
            self, bg=bg_color, highlightthickness=0, scrollregion=(0, 0, 800, 400)
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        self._vsb = ctk.CTkScrollbar(self, orientation="vertical", command=self._canvas.yview)
        self._vsb.pack(side="right", fill="y")
        self._hsb = ctk.CTkScrollbar(self, orientation="horizontal", command=self._canvas.xview)
        self._hsb.pack(side="bottom", fill="x")
        self._canvas.configure(yscrollcommand=self._vsb.set, xscrollcommand=self._hsb.set)

        # Interactive pan (drag) + zoom (mouse wheel).
        self._zoom = 1.0
        self._canvas.bind("<Configure>", self._auto_scrollregion)
        # Middle-button drag pans the canvas via Tk's built-in scan affines.
        self._canvas.bind("<Button-2>", self._on_pan_start)
        self._canvas.bind("<B2-Motion>", self._on_pan_move)
        # Mouse wheel zooms about the pointer position.
        self._canvas.bind("<MouseWheel>", self._on_zoom)

        self._placeholder = None
        self._show_placeholder()

    # ── pan + zoom ─────────────────────────────────────────────────────

    def _on_pan_start(self, event) -> None:
        self._canvas.scan_mark(event.x, event.y)
        return None

    def _on_pan_move(self, event) -> None:
        self._canvas.scan_dragto(event.x, event.y, gain=1)
        return None

    def _on_zoom(self, event) -> None:
        # Windows sends delta in multiples of 120; positive = zoom in.
        step = 0.1 if event.delta > 0 else -0.1
        self._zoom = max(0.4, min(2.5, self._zoom + step))
        self._draw_graph()
        return None

    # ── public API ─────────────────────────────────────────────────────

    def render_nodes(self, nodes: List[DagNode]) -> None:
        """Store and draw *nodes*."""
        self._nodes = list(nodes)
        self._clear_placeholder()
        self._draw_graph()

    def render_sql(self, sql: str, dialect: str = "duckdb") -> List[DagNode]:
        """Parse *sql* and render its DAG; returns the nodes drawn."""
        nodes = parse_sql(sql, dialect)
        self.render_nodes(nodes)
        return nodes

    def nodes(self) -> List[DagNode]:
        return list(self._nodes)

    # ── rendering helpers ──────────────────────────────────────────────

    def _show_placeholder(self) -> None:
        if self._placeholder is None:
            self._placeholder = self._canvas.create_text(
                self._zoom_x(400), self._zoom_y(200),
                text="No query parsed. Run a scan or type a query.",
                fill=self._muted, font=self._zoom_font(13),
            )

    def _clear_placeholder(self) -> None:
        if self._placeholder is not None:
            self._canvas.delete(self._placeholder)
            self._placeholder = None

    def _auto_scrollregion(self, event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _zoom_x(self, x: float) -> int:
        return int(x * self._zoom)

    def _zoom_y(self, y: float) -> int:
        return int(y * self._zoom)

    def _zoom_font(self, size: float) -> tuple:
        if self._zoom < 0.75:
            return ("Segoe UI", max(6, int(size * self._zoom)))
        return ("Segoe UI", int(size * self._zoom))

    def _draw_graph(self) -> None:
        self._canvas.delete("all")
        self._placeholder = None
        if not self._nodes:
            self._show_placeholder()
            self._auto_scrollregion()
            return

        # Layered left-to-right layout: each node in sequence advances x.
        x = 40
        y = 60
        max_y = 0
        max_x = 0
        for node in self._nodes:
            node._pos = (x, y)
            self._draw_node(node, x, y)
            max_y = max(max_y, y + _BOX_H)
            max_x = max(max_x, x + _BOX_W)
            x += _BOX_W + _H_GAP

        # Draw join edges between consecutive nodes.
        for i in range(len(self._nodes) - 1):
            self._draw_edge(self._nodes[i], self._nodes[i + 1])

        # Problem annotations under flagged nodes.
        for node in self._nodes:
            if node.problem and node._pos:
                bx, by = node._pos
                self._canvas.create_text(
                    self._zoom_x(bx) + _zoom_dim(_BOX_W, self._zoom) // 2,
                    self._zoom_y(by) + _zoom_dim(_BOX_H, self._zoom) // 2,
                    text="!", fill=self._err,
                    font=self._zoom_font(16) + ("bold",),
                )
                self._canvas.create_text(
                    self._zoom_x(bx) + _zoom_dim(_BOX_W, self._zoom) // 2,
                    self._zoom_y(by) + _zoom_dim(_BOX_H, self._zoom) + 12,
                    text=node.note or "Cartesian risk",
                    fill=self._err, font=self._zoom_font(8),
                )

        self._canvas.configure(
            scrollregion=(0, 0, _zoom_dim(max_x + 40, self._zoom),
                          _zoom_dim(max_y + 60, self._zoom))
        )
        self._auto_scrollregion()

    def _draw_node(self, node: DagNode, x: int, y: int) -> None:
        border = self._err if node.problem else self._accent
        fill = self._surface if not node.problem else self._err
        w = _zoom_dim(_BOX_W, self._zoom)
        h = _zoom_dim(_BOX_H, self._zoom)
        self._canvas.create_rectangle(
            self._zoom_x(x), self._zoom_y(y),
            self._zoom_x(x) + w, self._zoom_y(y) + h,
            fill=fill, outline=border, width=2,
        )
        self._canvas.create_text(
            self._zoom_x(x) + w // 2, self._zoom_y(y) + h // 2 - 6,
            text=node.label(), fill=self._text,
            font=self._zoom_font(10) + ("bold",),
        )
        kind_colour = self._err if node.problem else self._ok
        self._canvas.create_text(
            self._zoom_x(x) + w // 2, self._zoom_y(y) + h // 2 + 12,
            text=node.join_kind, fill=kind_colour,
            font=("Consolas", self._zoom_font(8)[1]),
        )

    def _draw_edge(self, src: DagNode, dst: DagNode) -> None:
        if not src._pos or not dst._pos:
            return
        w = _zoom_dim(_BOX_W, self._zoom)
        h = _zoom_dim(_BOX_H, self._zoom)
        x1 = self._zoom_x(src._pos[0]) + w
        y1 = self._zoom_y(src._pos[1]) + h // 2
        x2 = self._zoom_x(dst._pos[0])
        y2 = self._zoom_y(dst._pos[1]) + h // 2
        colour = self._err if dst.problem else self._muted
        self._canvas.create_line(x1, y1, x2, y2, fill=colour, width=2,
                                 arrow="last")
        midx = (x1 + x2) // 2
        self._canvas.create_text(
            midx, y1 - 10, text=dst.join_kind, fill=self._muted,
            font=("Consolas", self._zoom_font(8)[1]),
        )
