"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Lightweight live-schema checker for sqlean-lint PRO.

Scans a SQL statement's referenced tables and columns against an active
metadata catalog (real DuckDB catalogs, or mock metadata structures supplied
by the caller) and flags references to tables/columns that do not exist
(``SCHEMA-001``).

A ``SchemaCatalog`` may be constructed from:

* A DuckDB connection (via :func:`catalog_from_duckdb`) that introspects
  ``information_schema``.
* Raw dict metadata such as ``{"public": {"orders": {"id", "amount"}}}``.

The checker is intentionally best-effort: unknown dialects or unresolvable
identifiers are reported rather than raised, and arbitrary input never
propagates an exception (zero-crash execution policy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Identifier for a self-referencing column we cannot resolve to the catalog.
_SCHEMA_RULE_ID = "SCHEMA-001"


@dataclass
class SchemaViolation:
    """A single schema mismatch finding."""

    rule_id: str = _SCHEMA_RULE_ID
    message: str = ""
    line: int = 1
    col: int = 1
    severity: str = "MEDIUM"
    fixable: bool = False
    kind: str = "unknown"  # 'table' | 'column'

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "fixable": self.fixable,
            "kind": self.kind,
        }


@dataclass
class SchemaCatalog:
    """In-memory mock/real metadata.

    ``tables`` is a mapping of ``schema.table`` (lowercased) to a set of
    column names (lowercased). ``schemas`` records the known schema names.
    """

    tables: Dict[str, set] = field(default_factory=dict)
    schemas: set = field(default_factory=set)

    def add_table(self, table: str, columns: Iterable[str], schema: str = "public") -> None:
        key = _qualify(schema, table)
        if schema not in self.schemas:
            self.schemas.add(schema)
        self.tables[key] = {str(c).lower() for c in columns}

    def has_table(self, table: str, schema: Optional[str] = None) -> bool:
        if schema:
            return _qualify(schema, table) in self.tables
        return any(
            key.endswith("." + table.lower()) or key == table.lower()
            for key in self.tables
        )

    def has_column(self, table: str, column: str, schema: Optional[str] = None) -> bool:
        for key, cols in self.tables.items():
            if schema:
                if key == _qualify(schema, table):
                    return column.lower() in cols
            elif key.endswith("." + table.lower()) or key == table.lower():
                if column.lower() in cols:
                    return True
        return False


def _qualify(schema: str, table: str) -> str:
    schema = (schema or "public").strip().lower()
    table = str(table).strip().lower()
    return f"{schema}.{table}"


# ── Catalog builders ──────────────────────────────────────────────────

def catalog_from_duckdb(connection) -> SchemaCatalog:
    """Introspect a DuckDB connection's ``information_schema``."""
    catalog = SchemaCatalog()
    if connection is None:
        return catalog
    try:
        rows = connection.execute(
            "SELECT table_schema, table_name, column_name "
            "FROM information_schema.columns ORDER BY 1, 2"
        ).fetchall()
        for schema, table, column in rows:
            catalog.add_table(table, [column], schema=schema)
    except Exception:  # noqa: BLE001 - introspection is best-effort
        return catalog
    return catalog


def catalog_from_dict(meta: Dict[str, Any]) -> SchemaCatalog:
    """Build a catalog from ``{schema: {table: [columns]}}``."""
    catalog = SchemaCatalog()
    for schema, tables in meta.items():
        if not isinstance(tables, dict):
            continue
        for table, columns in tables.items():
            catalog.add_table(table, columns, schema=schema)
    return catalog


# ── Reference extraction ──────────────────────────────────────────────

def extract_references(sql: str, dialect: str = "duckdb") -> List[Tuple[str, List[str], Optional[str]]]:
    """Return ``[(table, [columns], schema)]`` for each table referenced.

    Best-effort via sqlglot. Returns an empty list on parse failure. Columns
    with an explicit ``table`` qualifier are attached to that table; unqualified
    columns are attached to the first table referenced by the statement.
    """
    try:
        import sqlglot  # noqa: PLC0415
        from sqlglot import exp  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    if not sql or not sql.strip():
        return []
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - malformed SQL is tolerated
        return []

    table_set: List[Tuple[str, Optional[str]]] = []
    col_set: List[Tuple[str, Optional[str]]] = []
    seen_tables: set = set()

    for node in ast.walk():
        if isinstance(node, exp.Table):
            name = getattr(node, "name", "") or ""
            if not name:
                continue
            db = node.db
            schema = str(db) if db and not _is_default_schema(str(db)) else None
            key = (name, schema)
            if key not in seen_tables:
                seen_tables.add(key)
                table_set.append(key)
        elif isinstance(node, exp.Column):
            col_name = getattr(node, "name", "") or ""
            if not col_name:
                continue
            tbl = node.table
            qualifier = None
            if tbl:
                qualifier = str(tbl.name or "")
            col_set.append((col_name, qualifier))

    refs: List[Tuple[str, List[str], Optional[str]]] = []
    for table, schema in table_set:
        cols: List[str] = []
        for col_name, qualifier in col_set:
            if qualifier and qualifier == table:
                cols.append(col_name)
        refs.append((table, cols, schema))

    # Attach unqualified columns to the primary (first) table, which is the
    # usual single-table "FROM" shape the schema checker targets.
    if refs:
        primary = refs[0]
        known = {c for c in primary[1]}
        for col_name, qualifier in col_set:
            if not qualifier and col_name not in known:
                primary[1].append(col_name)
                known.add(col_name)
    return refs


def _is_default_schema(name: str) -> bool:
    return name.lower() in ("public", "main", "default", "dbo")


# ── Checker ───────────────────────────────────────────────────────────

def check_schema(
    sql: str,
    catalog: SchemaCatalog,
    dialect: str = "duckdb",
    *,
    strict_tables: bool = True,
    strict_columns: bool = True,
) -> List[SchemaViolation]:
    """Validate references in *sql* against *catalog*.

    Returns a list of :class:`SchemaViolation`. Never raises.
    """
    violations: List[SchemaViolation] = []
    if not sql or not sql.strip():
        return violations

    refs = extract_references(sql, dialect)
    for table, columns, schema in refs:
        if not table:
            continue
        if strict_tables and not catalog.has_table(table, schema):
            violations.append(
                SchemaViolation(
                    message=(
                        f"Table '{table}' (schema='{schema or 'default'}') "
                        "does not exist in the active catalog."
                    ),
                    kind="table",
                    severity="HIGH",
                )
            )
            continue
        if strict_columns:
            for col in columns:
                if col and not catalog.has_column(table, col, schema):
                    violations.append(
                        SchemaViolation(
                            message=(
                                f"Column '{col}' does not exist on table "
                                f"'{table}' in the active catalog."
                            ),
                            kind="column",
                            severity="MEDIUM",
                        )
                    )
    return violations
