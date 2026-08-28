"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

SQL cross-dialect transpiler and data-exchange utilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

TRANSPILE_DIALECTS: tuple[str, ...] = (
    "duckdb",
    "bigquery",
    "snowflake",
    "postgres",
    "mysql",
    "sqlite",
    "tsql",
    "databricks",
)


class TranspileError(RuntimeError):
    """Raised when cross-dialect transpilation fails."""


@dataclass
class ExportResult:
    """Outcome of materialising query results to a file format."""

    format: str
    rows_written: int
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": self.format,
            "rows_written": self.rows_written,
            "path": self.path,
        }


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def transpile_sql(sql: str, source_dialect: str, target_dialect: str) -> str:
    """Transpile *sql* from one dialect to another via sqlglot."""
    try:
        import sqlglot
    except ImportError as exc:
        raise TranspileError(
            "sqlglot is required for transpilation but is not installed."
        ) from exc

    try:
        results = sqlglot.transpile(sql, read=source_dialect, write=target_dialect)
        return results[0] if results else sql
    except Exception as exc:
        raise TranspileError(
            f"Transpilation from {source_dialect!r} to {target_dialect!r} failed: {exc}"
        ) from exc


def json_to_sql(
    payload: List[Dict[str, Any]],
    table_name: str = "data",
    dialect: str = "duckdb",
) -> str:
    """Convert a list of dicts to CREATE TABLE + INSERT statements.

    Uses DuckDB in-process to derive the DDL from the first row of data.
    """
    if not payload:
        return f"CREATE TABLE IF NOT EXISTS {table_name} (empty INT);"

    try:
        import duckdb
    except ImportError as exc:
        raise TranspileError(
            "duckdb is required for json_to_sql but is not installed."
        ) from exc

    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE data AS SELECT * FROM $1", [payload])

        ddl_rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'data' ORDER BY ordinal_position"
        ).fetchall()
        col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in ddl_rows)
        create = f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs});"

        inserts: List[str] = []
        for row in payload:
            values = ", ".join(_sql_literal(v) for v in row.values())
            inserts.append(f"INSERT INTO {table_name} VALUES ({values});")

        return "\n".join([create, *inserts])
    finally:
        conn.close()


def _sql_literal(value: Any) -> str:
    """Render a Python value as an SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def df_to_sql(
    source_text: str,
    framework: str = "auto",
    target_dialect: str = "duckdb",
) -> str:
    """Parse a Python DataFrame pipeline and generate equivalent SQL.

    Full DataFrame analysis is complex; this returns a scaffold comment
    describing the detected pipeline.
    """
    lines = [ln.strip() for ln in source_text.splitlines() if ln.strip()]
    table_names: List[str] = []
    for line in lines:
        for token in ("read_csv", "read_parquet", "read_json", "read_sql"):
            if token in line:
                table_names.append(token)

    header = f"-- Auto-generated SQL scaffold (target: {target_dialect})"
    body = "-- Detected pipeline steps:"
    for idx, line in enumerate(lines):
        body += f"\n--   {idx + 1}: {line[:80]}"
    if table_names:
        body += f"\n-- Source tables suspected: {', '.join(dict.fromkeys(table_names))}"
    body += "\n-- NOTE: Full DataFrame-to-SQL transpilation is not yet implemented."

    return f"{header}\n{body}\n"


def sql_to_format(
    sql: str,
    export_format: str,
    output_path: Optional[str] = None,
) -> ExportResult:
    """Execute *sql* against local DuckDB and materialise to the requested format."""
    try:
        import duckdb
    except ImportError as exc:
        raise TranspileError(
            "duckdb is required for sql_to_format but is not installed."
        ) from exc

    format_lower = export_format.lower()
    conn = duckdb.connect(":memory:")
    try:
        result = conn.execute(sql)
        rows = result.fetchall()
        row_count = len(rows)

        if output_path is None:
            import tempfile
            suffix = {"csv": ".csv", "json": ".json", "parquet": ".parquet"}.get(
                format_lower, ".out"
            )
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                output_path = tmp.name

        if format_lower == "csv":
            conn.execute(f"COPY ({sql}) TO '{output_path}' (HEADER, DELIMITER ',')")
        elif format_lower == "json":
            conn.execute(f"COPY ({sql}) TO '{output_path}' (FORMAT JSON, ARRAY true)")
        elif format_lower == "parquet":
            conn.execute(f"COPY ({sql}) TO '{output_path}' (FORMAT PARQUET)")
        else:
            raise TranspileError(f"Unsupported export format: {export_format!r}")

        return ExportResult(
            format=format_lower, rows_written=row_count, path=output_path
        )
    finally:
        conn.close()


def preview_query(sql: str) -> Dict[str, Any]:
    """Execute *sql* against DuckDB and return a preview dict."""
    try:
        import duckdb
    except ImportError:
        return {"ok": False, "columns": [], "rows": [], "error": "duckdb not installed"}

    try:
        conn = duckdb.connect(":memory:")
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description] if result.description else []
        rows = [list(row) for row in result.fetchall()]
        conn.close()
        return {"ok": True, "columns": columns, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "columns": [], "rows": [], "error": str(exc)}
