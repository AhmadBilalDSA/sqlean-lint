"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Interactive, zoomable AST lineage canvas for the sqlean-lint PRO GUI.

Parses the active query into relational-operator nodes (``TableScan``,
``Filter``, ``Join``, ``Aggregate``, ``Projection``) and renders them as
elevated cards on a Tk canvas with coloured connector links showing data
flow direction.

Interactions:
* Drag to pan (``<B2-Motion>`` or ``<Shift-B1-Motion>``).
* Mouse-wheel zoom between 0.5x and 2.0x, anchored at the pointer.
* Clicking a node invokes ``on_node_clicked(node, sql_range)`` so the caller
  can highlight the matching SQL token range in the editor.

A pure parser (:func:`parse_operators`) is unit-testable without Tk.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import customtkinter as ctk
import sqlglot

from .design_system import TOKENS

MIN_ZOOM = 0.5
MAX_ZOOM = 2.0

NODE_W = 150
NODE_H = 52
H_GAP = 40
V_GAP = 40


class AstNode:
    """A single relational-operator node with its source SQL range."""

    __slots__ = ("kind", "label", "sql_range", "detail", "_pos")

    def __init__(
        self,
        kind: str,
        label: str,
        sql_range: Optional[Tuple[int, int]] = None,
        detail: str = "",
    ) -> None:
        self.kind = kind
        self.label = label
        self.sql_range = sql_range
        self.detail = detail
        self._pos: Optional[Tuple[int, int]] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "sql_range": list(self.sql_range) if self.sql_range else None,
        }


# ── node fill colours per relational operator kind ────────────────────

_KIND_FILL = {
    "TableScan": TOKENS["SURFACE_CARD"],
    "Filter": "#1E2A40",
    "Join": "#242C42",
    "Aggregate": "#2A344D",
    "Projection": "#28324C",
}

_KIND_BORDER = {
    "TableScan": TOKENS["SEMANTIC_INFO"],
    "Filter": TOKENS["SEMANTIC_WARNING"],
    "Join": TOKENS["SEMANTIC_SUCCESS"],
    "Aggregate": TOKENS["ACCENT_PRIMARY"],
    "Projection": TOKENS["SEMANTIC_DANGER"],
}


def _range_of(e: Any, src: str) -> Tuple[int, int]:
    """Best-effort (start, end) char range of an expression within *src*."""
    try:
        text = e.sql()
    except Exception:  # noqa: BLE001
        return (0, len(src))
    start = src.find(text)
    if start == -1:
        # Normalise whitespace and search again.
        compact = re.sub(r"\s+", " ", text)
        norm = re.sub(r"\s+", " ", src)
        start = norm.find(compact)
        if start != -1:
            return (start, start + len(compact))
        return (0, len(src))
    return (start, start + len(text))


def parse_operators(sql: str, dialect: str = "duckdb") -> List[AstNode]:
    """Parse *sql* into ordered relational-operator nodes (best-effort).

    Returns an empty list if the query cannot be parsed; never raises.
    """
    if not sql or not sql.strip():
        return []
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        try:
            ast = sqlglot.parse_one(sql)
        except Exception:  # noqa: BLE001
            return []

    nodes: List[AstNode] = []
    src = sql

    for item in ast.walk():
        tname = type(item).__name__
        if tname == "Table":
            nodes.append(AstNode(
                kind="TableScan",
                label=f"TableScan: {item.name}",
                sql_range=_range_of(item, src),
                detail=f"Scan {item.name}",
            ))
        elif tname == "From":
            # Just marks the source; table children already added.
            pass
        elif tname == "Join":
            kind = _join_kind(item)
            nodes.append(AstNode(
                kind="Join",
                label=f"Join ({kind})",
                sql_range=_range_of(item, src),
                detail=kind,
            ))
        elif tname == "Where":
            nodes.append(AstNode(
                kind="Filter",
                label="Filter (WHERE)",
                sql_range=_range_of(item, src),
                detail="Predicate",
            ))
        elif tname == "Aggregate":
            nodes.append(AstNode(
                kind="Aggregate",
                label="Aggregate",
                sql_range=_range_of(item, src),
                detail="GROUP BY",
            ))
        elif tname in ("Select", "Projection"):
            nodes.append(AstNode(
                kind="Projection",
                label="Projection",
                sql_range=(0, len(src)),
                detail="SELECT columns",
            ))
    if not nodes:
        nodes.append(AstNode(
            kind="Projection", label="Projection (Unknown)", sql_range=(0, len(src)),
        ))
    return nodes


def _join_kind(node) -> str:
    try:
        base = str(node.args.get("kind") or "").upper()
        side = str(node.args.get("side") or "").upper()
        if base == "CROSS":
            return "CROSS"
        if side:
            return side
        return "INNER"
    except Exception:  # noqa: BLE001
        return "JOIN"


class DagCanvas(ctk.CTkFrame):
    """Zoomable, pannable lineage canvas for relational operators."""

    def __init__(
        self,
        master,
        *,
        bg_color: str = TOKENS["PANEL_BG"],
        on_node_clicked: Optional[Callable[[AstNode], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, corner_radius=8, **kwargs)
        self._bg = bg_color
        self._on_node_clicked = on_node_clicked
        self._nodes: List[AstNode] = []
        self._zoom = 1.0
        self._node_ids: Dict[int, AstNode] = {}
        self._pan_mode = False

        self._canvas = ctk.CTkCanvas(
            self, bg=bg_color, highlightthickness=0, borderwidth=0,
            scrollregion=(0, 0, 1200, 600),
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        # Pan: middle drag or Shift+B1 drag.
        self._canvas.bind("<Button-2>", self._pan_start)
        self._canvas.bind("<B2-Motion>", self._pan_move)
        self._canvas.bind("<ButtonRelease-2>", self._pan_end)
        self._canvas.bind("<Shift-Button-1>", self._pan_start)
        self._canvas.bind("<Shift-B1-Motion>", self._pan_move)
        self._canvas.bind("<Shift-ButtonRelease-1>", self._pan_end)

        # Zoom with mouse wheel (Windows Scroll).
        self._canvas.bind("<MouseWheel>", self._on_zoom)

        # Selection click on a bare canvas (release).
        self._canvas.bind("<Button-1>", self._on_click_release)

        self._placeholder = None
        self._show_placeholder()

    # ── public API ─────────────────────────────────────────────────────

    def render_nodes(self, nodes: List[AstNode]) -> None:
        """Store and draw *nodes*."""
        self._nodes = list(nodes)
        self._clear_placeholder()
        self._repaint()

    def render_sql(self, sql: str, dialect: str = "duckdb") -> List[AstNode]:
        nodes = parse_operators(sql, dialect)
        self.render_nodes(nodes)
        return nodes

    def nodes(self) -> List[AstNode]:
        return list(self._nodes)

    def clear(self) -> None:
        self._nodes = []
        self._canvas.delete("all")
        self._show_placeholder()

    # ── interactions ───────────────────────────────────────────────────

    def _pan_start(self, event) -> None:
        self._pan_mode = True
        self._canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event) -> None:
        if self._pan_mode:
            self._canvas.scan_dragto(event.x, event.y, gain=1)

    def _pan_end(self, _event) -> None:
        self._pan_mode = False

    def _on_zoom(self, event) -> None:
        # Honour the viewport-content gap rule: zoom anchored at pointer.
        anchor_x = event.x
        anchor_y = event.y
        anchor_world_x = self._canvas.canvasx(anchor_x)
        anchor_world_y = self._canvas.canvasy(anchor_y)
        step = 0.1 if event.delta > 0 else -0.1
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom + step))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        self._zoom = new_zoom
        self._repaint()
        # Re-centre the view so the pointer-anchored world point stays put.
        try:
            sx = self._canvas.canvasx(0)
            sy = self._canvas.canvasy(0)
            self._canvas.scan_mark(0, 0)
            self._canvas.scan_dragto(
                int(sx - (anchor_world_x - anchor_x)) * -1,
                int(sy - (anchor_world_y - anchor_y)) * -1,
                gain=1,
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_click_release(self, event) -> None:
        # Ignore clicks coming from a pan gesture.
        if self._pan_mode:
            return
        nearest = self._canvas.find_closest(event.x, event.y)
        if not nearest:
            return
        item = nearest[0]
        node = self._node_ids.get(item)
        if node is not None and self._on_node_clicked is not None:
            try:
                self._on_node_clicked(node)
            except Exception:  # noqa: BLE001
                pass

    # ── drawing ────────────────────────────────────────────────────────

    def _show_placeholder(self) -> None:
        if self._placeholder is None:
            self._placeholder = self._canvas.create_text(
                60, 60, text="No query parsed. Run a scan or type a query.",
                fill=TOKENS["TEXT_MUTED"], font=("Segoe UI", 12),
                anchor="nw",
            )

    def _clear_placeholder(self) -> None:
        if self._placeholder is not None:
            self._canvas.delete(self._placeholder)
            self._placeholder = None

    def _z(self, value: float) -> int:
        return int(value * self._zoom)

    def _font(self, size: float) -> tuple:
        return ("Segoe UI", max(6, int(size * self._zoom)))

    def _repaint(self) -> None:
        self._canvas.delete("all")
        self._placeholder = None
        self._node_ids = {}
        if not self._nodes:
            self._show_placeholder()
            return

        # Lay out operators top-down by kind ordering, left-to-right.
        w = self._z(NODE_W)
        h = self._z(NODE_H)
        max_x = 40
        max_y = 40
        for node in self._nodes:
            x = 40
            y = 40
            node._pos = (x, y)
            self._draw_node(node, x, y, w, h)
            max_x = max(max_x, x + w + H_GAP)
            max_y = max(max_y, y + h + 20)

        # Connect consecutive nodes with arrowed links.
        for i in range(len(self._nodes) - 1):
            self._draw_edge(self._nodes[i], self._nodes[i + 1])

        self._canvas.configure(
            scrollregion=(0, 0, max_x + 80, max_y + 80)
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        """Truncate *text* to *max_chars* with an ellipsis if needed."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "\u2026"

    def _draw_node(self, node: AstNode, x: int, y: int, w: int, h: int) -> None:
        fill = _KIND_FILL.get(node.kind, TOKENS["SURFACE_CARD"])
        border = _KIND_BORDER.get(node.kind, TOKENS["BORDER_STRONG"])
        rect = self._canvas.create_rectangle(
            x, y, x + w, y + h, fill=fill, outline=border, width=2,
        )
        self._node_ids[rect] = node
        # Truncate text to prevent overflow beyond the node width (150 px).
        kind_text = self._truncate(node.kind, 16)
        label_text = self._truncate(node.label, 18)
        detail_text = self._truncate(node.detail, 20) if node.detail else ""
        self._canvas.create_text(
            x + w // 2, y + 8, text=kind_text,
            font=self._font(8) + ("bold",),
            fill=TOKENS["TEXT_SECONDARY"],
        )
        self._canvas.create_text(
            x + w // 2, y + h // 2 + 6, text=label_text,
            font=self._font(9) + ("bold",),
            fill=TOKENS["TEXT_PRIMARY"],
        )
        if detail_text:
            self._canvas.create_text(
                x + w // 2, y + h - 6, text=detail_text,
                font=self._font(7), fill=TOKENS["TEXT_MUTED"],
            )

    def _draw_edge(self, src: AstNode, dst: AstNode) -> None:
        if not src._pos or not dst._pos:
            return
        src_x = src._pos[0] + self._z(NODE_W)
        src_y = src._pos[1] + self._z(NODE_H) // 2
        dst_x = dst._pos[0]
        dst_y = dst._pos[1] + self._z(NODE_H) // 2
        colour = TOKENS["SEMANTIC_INFO"]
        self._canvas.create_line(
            src_x, src_y, dst_x, dst_y, fill=colour, width=2, arrow="last",
        )
