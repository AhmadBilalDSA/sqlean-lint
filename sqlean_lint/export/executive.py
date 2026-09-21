"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

One-click executive export for the sqlean-lint PRO GUI.

Combines the Code Health Score, DAG visualization metadata, and a Data Grid
preview into a polished management report written as both:

* ``.xlsx`` (via ``openpyxl`` -- a rich workbook with a summary sheet, a
  DAG/plan sheet and a data-preview sheet), and
* ``.pdf`` (via a small pure-Python PDF renderer so no third-party reporting
  dependency is required; it produces a valid single-page report with text
  and simple rectangles).

Every exporter accepts ordinary Python values (score, nodes, rows), so the
functions are headless and unit-testable without a Tk root.

Copyright/identity is stamped into both artifacts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from pathlib import Path

_OWNER = "Ahmad Bilal (AhmadBilalDSA)"
_EMAIL = "kierninja@gmail.com"
_TITLE = "SQLean Lint PRO - Executive Report"


# ── Pure-Python minimal PDF writer ─────────────────────────────────────

class _Pdf:
    """A tiny PDF writer producing a valid, text + rect single-page file.

    Lays out a fixed 6-object document (catalog, pages, page, content stream,
    two fonts) and builds a correct xref table. Only ASCII-safe (latin-1)
    text is emitted; non-ASCII glyphs are replaced so the file stays valid
    even in a cp1252 console environment.
    """

    def __init__(self) -> None:
        self._stream: List[bytes] = []

    def begin_text(self, x: float, y: float, size: float = 11,
                   bold: bool = False) -> None:
        font = "/F2" if bold else "/F1"
        self._stream.append(
            f"BT /F2 {size} Tf {x} {y} Td".encode("latin-1", "replace")
            if bold else
            f"BT /F1 {size} Tf {x} {y} Td".encode("latin-1", "replace")
        )

    def line(self, text: str) -> None:
        safe = text.encode("latin-1", "replace").decode("latin-1")
        self._stream.append(f"({self._escape(safe)}) Tj T*".encode("latin-1"))

    def end_text(self) -> None:
        self._stream.append(b"ET")

    def rect(self, x: float, y: float, w: float, h: float,
             fill: str = "0.93") -> None:
        self._stream.append(
            f"{fill} rg {x} {y} {w} {h} re f".encode("latin-1")
        )

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def build(self) -> bytes:
        content = b"".join(self._stream)
        stream_obj = (
            f"4 0 obj << /Length {len(content)} >> stream\n".encode()
            + content + b"\nendstream\nendobj\n"
        )
        objects = [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            (b"3 0 obj << /Type /Page /Parent 2 0 R "
             b"/MediaBox [0 0 612 792] /Contents 4 0 R "
             b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >> endobj\n"),
            stream_obj,
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> "
            b"endobj\n",
            b"6 0 obj << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica-Bold >> endobj\n",
        ]
        pdf = b"%PDF-1.4\n"
        offsets = []
        for o in objects:
            offsets.append(len(pdf))
            pdf += o
        xref_pos = len(pdf)
        pdf += f"xref\n0 {len(objects) + 1}\n".encode()
        pdf += b"0000000000 65535 f \n"
        for off in offsets:
            pdf += f"{off:010d} 00000 n \n".encode()
        pdf += (f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n").encode()
        return pdf


def write_pdf(
    output: Path,
    *,
    score: int,
    letter: str,
    nodes: Sequence[dict],
    columns: Sequence[str],
    rows: Sequence[dict],
) -> Path:
    """Write a one-page executive PDF report.

    Args:
        output: Destination file path (``.pdf``).
        score: SQL health score (0-100).
        letter: Letter grade (A-F).
        nodes: DAG node dictionaries (see ``ui.dag_viewer``).
        columns: Data grid column names.
        rows: Up to N data rows for the preview table.

    Returns:
        The written ``output`` path.
    """
    pdf = _Pdf()
    pdf.begin_text(40, 760, 20, bold=True)
    pdf.line(_TITLE)
    pdf.end_text()

    pdf.begin_text(40, 730, 10)
    pdf.line(f"Prepared by: {_OWNER}  |  {_EMAIL}")
    pdf.line("License: MIT  |  Engine: SQLGlot AST  |  Data: Zero-Cloud Local")
    pdf.end_text()

    # Score block
    pdf.rect(40, 700, 180, 40, "0.92")
    pdf.begin_text(52, 728, 14, bold=True)
    pdf.line(f"Code Health Score: {score}/100 ({letter})")
    pdf.end_text()

    # DAG summary
    pdf.begin_text(40, 680, 12, bold=True)
    pdf.line("Execution Plan (DAG)")
    pdf.end_text()
    pdf.begin_text(40, 660, 10)
    for n in nodes[:8]:
        flag = "  [CARTESIAN RISK]" if n.get("problem") else ""
        pdf.line(f"  {n.get('name','')}  ({n.get('join_kind','')})  {flag}")
    pdf.end_text()

    # Data preview
    pdf.begin_text(40, 480, 12, bold=True)
    pdf.line("Query Result Preview")
    pdf.end_text()
    pdf.begin_text(40, 460, 9)
    header = "  ".join(str(c)[:14] for c in columns[:6])
    pdf.line(header)
    for row in rows[:14]:
        vals = "  ".join(str(row.get(c, ""))[:14] for c in columns[:6])
        pdf.line(vals)
    pdf.end_text()

    pdf.begin_text(40, 100, 9)
    pdf.line(f"Generated by {_TITLE}. Confidential - internal use only.")
    pdf.end_text()

    output.write_bytes(pdf.build())
    return output


# ── XLSX export (openpyxl) ─────────────────────────────────────────────

def write_xlsx(
    output: Path,
    *,
    score: int,
    letter: str,
    dialect: str,
    nodes: Sequence[dict],
    columns: Sequence[str],
    rows: Sequence[dict],
    health_details: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write a multi-sheet executive Excel workbook.

    Returns:
        The written ``output`` path.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # Sheet 1: Summary
    ws = wb.active
    ws.title = "Summary"
    accent = PatternFill("solid", fgColor="7AA2F7")
    ws["A1"] = _TITLE
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Prepared by {_OWNER} ({_EMAIL}) - MIT License"
    ws["A4"] = "Code Health Score"
    ws["A5"] = "Letter Grade"
    ws["A6"] = "Dialect"
    rows_meta = [
        ("Code Health Score", score),
        ("Letter Grade", letter),
        ("Dialect", dialect),
        ("Prepared by", f"{_OWNER} <{_EMAIL}>"),
        ("License", "MIT"),
    ]
    for i, (k, v) in enumerate(rows_meta, start=4):
        ws[f"A{i}"] = k
        ws[f"A{i}"].font = Font(bold=True)
        ws[f"A{i}"].fill = accent
        ws[f"B{i}"] = v
    if health_details:
        r0 = len(rows_meta) + 6
        ws[f"A{r0}"] = "Health Breakdown"
        for j, (k, v) in enumerate(health_details.items()):
            ws[f"A{r0 + 1 + j}"] = k
            ws[f"B{r0 + 1 + j}"] = v

    # Sheet 2: DAG
    ws2 = wb.create_sheet("Execution Plan")
    ws2["A1"] = "Execution Plan (DAG)"
    ws2["A1"].font = Font(bold=True)
    for i, h in enumerate(["Table", "Alias", "Join", "Problem", "Note"],
                          start=1):
        ws2.cell(row=2, column=i, value=h).font = Font(bold=True)
    for r, n in enumerate(nodes, start=3):
        ws2.cell(row=r, column=1, value=n.get("name", ""))
        ws2.cell(row=r, column=2, value=n.get("alias", ""))
        ws2.cell(row=r, column=3, value=n.get("join_kind", ""))
        ws2.cell(row=r, column=4, value=n.get("problem", False))
        ws2.cell(row=r, column=5, value=n.get("note", ""))
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["E"].width = 40

    # Sheet 3: Data preview
    ws3 = wb.create_sheet("Data Preview")
    ws3["A1"] = "Query Result Preview"
    ws3["A1"].font = Font(bold=True)
    if columns:
        for c, col in enumerate(columns, start=1):
            cell = ws3.cell(row=3, column=c, value=col)
            cell.font = Font(bold=True)
            cell.fill = accent
            ws3.column_dimensions[get_column_letter(c)].width = 16
        for r, row in enumerate(rows, start=4):
            for c, col in enumerate(columns, start=1):
                val = row.get(col)
                ws3.cell(row=r, column=c, value=("" if val is None else str(val)))

    wb.save(output)
    return output


def export_executive_report(
    output: Path,
    *,
    score: int = 0,
    letter: str = "F",
    dialect: str = "duckdb",
    nodes: Optional[Sequence[dict]] = None,
    columns: Optional[Sequence[str]] = None,
    rows: Optional[Sequence[dict]] = None,
    health_details: Optional[Dict[str, Any]] = None,
    formats=("pdf", "xlsx"),
) -> List[Path]:
    """Write the executive report in the requested *formats*.

    Args:
        output: Destination base path (extension is appended per format;
            ``.pdf`` and ``.xlsx`` are produced by default).
        score, letter, dialect, nodes, columns, rows: report payload.
        health_details: optional key/value breakdown for the summary sheet.
        formats: iterable of ``"pdf"`` and/or ``"xlsx"``.

    Returns:
        The list of written file paths.

    Raises:
        ValueError: If an unsupported format is requested.
    """
    nodes = list(nodes or [])
    columns = list(columns or [])
    rows = list(rows or [])
    written: List[Path] = []
    for fmt in formats:
        if fmt == "pdf":
            target = output.with_suffix(".pdf")
            write_pdf(
                target, score=score, letter=letter,
                nodes=nodes, columns=columns, rows=rows,
            )
            written.append(target)
        elif fmt == "xlsx":
            target = output.with_suffix(".xlsx")
            write_xlsx(
                target, score=score, letter=letter, dialect=dialect,
                nodes=nodes, columns=columns, rows=rows,
                health_details=health_details,
            )
            written.append(target)
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
    return written
