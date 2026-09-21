# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""UI/UX components for the sqlean-lint PRO desktop application.

Re-exports the interactive workspace widgets built on CustomTkinter across
the three enterprise "experiences":

Phase 1 - VS Code IDE experience
    Command palette, tabbed file manager, IntelliSense autocomplete and a
    code minimap.
Phase 2 - KNIME visual data-science experience
    Visual execution-plan DAG viewer and a live data-profiling panel.
Phase 3 - Excel business/analyst experience
    Interactive sortable data grid preview.
Base widgets
    Animation engine, split-pane code visualizer, health-score gauge and the
    interactive file explorer.
"""
from __future__ import annotations

from .animations import CTkAnimationEngine, FadeWidget, SlidePanel
from .chart_view import ChartView
from .code_view import (
    SplitCodeDiffViewer,
    apply_syntax_highlighting,
    configure_textbox_tags,
)
from .command_palette import Command, CommandPalette
from .dag_viewer import DagNode, DagViewer, build_dag, parse_sql
from .data_grid import DataGrid, QueryRunner, normalize_rows, sort_rows
from .explorer import InteractiveFileExplorer
from .history_tracker import HistoryTracker
from .intellisense import IntelliSense
from .minimap import CodeMinimap
from .profiler_view import ProfilerView, extract_columns, profile_rows
from .query_history import (
    HistoryEntry,
    QueryHistoryDrawer,
    QueryHistoryLedger,
)
from .rating_widget import SQLHealthScore, letter_grade_for
from .tab_manager import CTkTabBar
from .virtual_grid import VirtualGrid
from .dag_canvas import AstNode as DagCanvasNode, DagCanvas, parse_operators
from .toasts import SkeletonPulse, ToastStack

__all__ = [
    "CTkAnimationEngine",
    "FadeWidget",
    "SlidePanel",
    "ChartView",
    "SplitCodeDiffViewer",
    "apply_syntax_highlighting",
    "configure_textbox_tags",
    "Command",
    "CommandPalette",
    "DagNode",
    "DagViewer",
    "build_dag",
    "parse_sql",
    "DataGrid",
    "QueryRunner",
    "normalize_rows",
    "sort_rows",
    "InteractiveFileExplorer",
    "HistoryTracker",
    "IntelliSense",
    "CodeMinimap",
    "ProfilerView",
    "extract_columns",
    "profile_rows",
    "HistoryEntry",
    "QueryHistoryDrawer",
    "QueryHistoryLedger",
    "SQLHealthScore",
    "letter_grade_for",
    "CTkTabBar",
    "VirtualGrid",
    "DagCanvasNode",
    "DagCanvas",
    "parse_operators",
    "SkeletonPulse",
    "ToastStack",
]
