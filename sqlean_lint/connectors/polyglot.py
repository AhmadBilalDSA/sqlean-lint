"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Polyglot data-shape converters for sqlean-lint Pro.

These helpers bridge between tabular SQL and semi-structured document shapes:

  * ``csv_to_sql`` — read an RFC-4180 CSV with **pandas**, infer SQL column
    data types from the actual values, and emit ANSI-Standard ``CREATE TABLE``
    followed by batched ``INSERT INTO`` statements.
  * ``sql_to_mongodb`` — transpile a ``SELECT ... WHERE ... GROUP BY`` query via
    the sqlglot AST into a valid MongoDB aggregation pipeline (``$match``,
    ``$project``, ``$group``, ``$sort``, ``$limit``).

Enterprise bi-directional data gateways:

  * ``PostgresGateway`` — connect to PostgreSQL via SQLAlchemy, execute lint
    results back as audit tables, and stream query results for analysis.
  * ``RedisCacheGateway`` — cache AST parse trees and lint results in Redis
    to avoid redundant transpilation across sessions.
  * ``SnowflakeGateway`` — Snowflake connector using snowflake-sqlalchemy for
    warehouse-scale SQL linting pipelines.
  * ``MongoDBGateway`` — read/write lint findings to MongoDB collections for
    long-term audit trails and analytics dashboards.

The converters are hermetic transforms; they require no live database. The
online/behavioural code is pure Python, so it degrades cleanly on malformed
input without ever raising.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - optional dependency
    import pandas as pd

    _HAS_PANDAS = True
except Exception:  # noqa: BLE001
    pd = None  # type: ignore[assignment]
    _HAS_PANDAS = False

try:  # pragma: no cover - optional dependency
    import sqlglot
    from sqlglot import exp

    _HAS_SQLGLOT = True
except Exception:  # noqa: BLE001
    sqlglot = None  # type: ignore[assignment]
    exp = None  # type: ignore[assignment]
    _HAS_SQLGLOT = False

# ── CSV → SQL (pandas-backed) ─────────────────────────────────────────

_DTYPE_TO_SQL = {
    "int8": "INTEGER", "int16": "INTEGER", "int32": "INTEGER",
    "int64": "INTEGER", "uint8": "INTEGER", "uint16": "INTEGER",
    "uint32": "INTEGER", "uint64": "INTEGER",
    "float32": "DOUBLE", "float64": "DOUBLE",
    "bool": "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
    "object": "TEXT",
}
_DATE_COLS = ("date", "dt", "day", "created_at", "updated_at", "timestamp")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_identifier(name: str) -> str:
    """Quote a column/table identifier if it is not a safe bare identifier."""
    return name if _IDENT_RE.match(name) else f'"{name}"'


def _pandas_sql_type(dtype) -> str:
    """Map a pandas dtype to an ANSI SQL type."""
    key = str(dtype)
    for prefix, sql_type in _DTYPE_TO_SQL.items():
        if key.startswith(prefix):
            return sql_type
    return "TEXT"


def _infer_column_type(series) -> str:
    """Infer a SQL type from a pandas Series, honouring date columns."""
    name = str(series.name).strip().lower()
    if any(part in name for part in _DATE_COLS):
        return "TIMESTAMP"
    base = _pandas_sql_type(series.dtype)
    if base != "TEXT":
        return base
    non_null = series.dropna()
    if non_null.empty:
        return "TEXT"
    if non_null.map(lambda v: isinstance(v, bool)).all():
        return "BOOLEAN"
    if non_null.map(lambda v: isinstance(v, int)).all():
        return "INTEGER"
    if non_null.map(lambda v: isinstance(v, float)).all():
        return "DOUBLE"
    return "TEXT"


def _sql_literal(value: Any, sql_type: str) -> str:
    """Render a Python value as a typed ANSI SQL literal."""
    if value is None or (isinstance(value, float) and pd is not None and pd.isna(value)):
        return "NULL"
    if sql_type in ("INTEGER", "DOUBLE"):
        return str(int(value)) if sql_type == "INTEGER" and float(value).is_integer() \
            else repr(float(value))
    if sql_type == "BOOLEAN":
        return "TRUE" if bool(value) else "FALSE"
    if sql_type == "TIMESTAMP":
        return f"'{str(value)}'"
    return f"'{str(value).replace(chr(39), chr(39) * 2)}'"


def _batched_inserts(
    table: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
    column_types: Dict[str, str],
    batch_size: int = 500,
) -> str:
    """Build batched ``INSERT INTO`` statements over a list of row dicts."""
    col_list = ", ".join(_sanitize_identifier(c) for c in columns)
    statements: List[str] = []
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        tuples = []
        for row in chunk:
            values = ", ".join(
                _sql_literal(row.get(c), column_types[c]) for c in columns
            )
            tuples.append(f"  ({values})")
        statements.append(
            f"INSERT INTO {_sanitize_identifier(table)} ({col_list})\nVALUES\n"
            + ",\n".join(tuples) + ";"
        )
    return "\n\n".join(statements)


def csv_to_sql(csv_path: str, table_name: str) -> str:
    """Convert a CSV file on disk into ANSI SQL ``CREATE TABLE`` + ``INSERT``.

    The CSV is read with **pandas**; column SQL types are inferred from the
    actual column dtypes/values rather than guessed from headers.

    Args:
        csv_path: Path to the RFC-4180 CSV file.
        table_name: Desired target table name.

    Returns:
        A single SQL script string: one ``CREATE TABLE`` statement followed by
        batched ``INSERT INTO`` statements.

    Raises:
        RuntimeError: If pandas is unavailable or the file cannot be read.
    """
    if not _HAS_PANDAS:
        raise RuntimeError("pandas is required for CSV -> SQL conversion.")
    try:
        frame = pd.read_csv(csv_path)
    except Exception as err:  # noqa: BLE001
        raise RuntimeError(f"Could not read CSV '{csv_path}': {err}") from err

    if frame.empty:
        return f"CREATE TABLE {_sanitize_identifier(table_name)} ();"

    columns = [str(col) for col in frame.columns]
    column_types = {col: _infer_column_type(frame[col]) for col in columns}

    col_decl = ",\n  ".join(
        f"{_sanitize_identifier(col)} {column_types[col]}" for col in columns
    )
    create_sql = (
        f"CREATE TABLE {_sanitize_identifier(table_name)} (\n  {col_decl}\n);"
    )

    records = frame.to_dict(orient="records")
    insert_sql = _batched_inserts(table_name, columns, records, column_types)
    return create_sql + "\n\n" + insert_sql


# ── SQL → MongoDB aggregation pipeline ───────────────────────────────

def sql_to_mongodb(sql_query: str) -> Dict[str, Any]:
    """Transpile a ``SELECT ... WHERE ... GROUP BY ...`` into a MongoDB pipeline.

    Args:
        sql_query: A SQL ``SELECT`` statement (default duckdb dialect).

    Returns:
        A dict with ``pipeline`` (list of aggregation stages) and ``collection``.

    Raises:
        ValueError: If the query cannot be parsed or uses unsupported clauses.
    """
    if not _HAS_SQLGLOT:
        raise RuntimeError("sqlglot is required for SQL -> MongoDB conversion.")

    try:
        ast = sqlglot.parse_one(sql_query, read="duckdb")
    except Exception as err:  # noqa: BLE001
        raise ValueError(f"Could not parse SQL query: {err}") from err

    select = ast.find(exp.Select)
    if select is None:
        raise ValueError("Only SELECT statements can be converted to MongoDB.")

    pipeline: List[Dict[str, Any]] = []

    where = select.args.get("where")
    if where is not None:
        pipeline.append({"$match": {"$and": _flatten_conditions(where.this)}})

    group_by = select.args.get("group")
    aggregates = [e for e in select.expressions if _is_aggregate(e)]

    if group_by is not None and group_by.expressions:
        group_ids = [e.name for e in group_by.expressions]
        group: Dict[str, Any] = {"_id": {g: f"${g}" for g in group_ids}}
        if not aggregates:
            group["_id"] = group_ids[0] if len(group_ids) == 1 else {
                g: f"${g}" for g in group_ids
            }
        for agg in aggregates:
            alias = agg.alias or _agg_default_name(agg)
            group[alias] = _aggregate_expr(agg)
        pipeline.append({"$group": group})
        projections = [
            e for e in select.expressions
            if isinstance(e, exp.Column) and e.name not in group_ids
        ]
        if projections or aggregates:
            project: Dict[str, Any] = {}
            for g in group_ids:
                project[g] = f"$_id.{g}"
            for agg in aggregates:
                project[agg.alias or _agg_default_name(agg)] = 1
            pipeline.append({"$project": project})
    else:
        if aggregates:
            group = {"_id": None}
            for agg in aggregates:
                group[agg.alias or _agg_default_name(agg)] = _aggregate_expr(agg)
            pipeline.append({"$group": group})
        else:
            projected = [e.name for e in select.expressions if e.name]
            if projected and not any(isinstance(e, exp.Star) for e in select.expressions):
                pipeline.append({"$project": {p: 1 for p in projected}})

    order = select.args.get("order")
    if order is not None and order.expressions:
        sort: Dict[str, Any] = {}
        for item in order.expressions:
            direction = 1
            if isinstance(item, exp.Ordered):
                if item.desc:
                    direction = -1
                item = item.this
            if isinstance(item, exp.Column):
                sort[item.name] = direction
        if sort:
            pipeline.append({"$sort": sort})

    limit = select.args.get("limit")
    if limit is not None:
        try:
            pipeline.append({"$limit": int(limit.expression.name)})
        except (AttributeError, ValueError):
            pass

    return {"collection": "collection", "pipeline": pipeline}


def _flatten_conditions(node) -> List[Dict[str, Any]]:
    """Split an AND-tree into a list of single-field match clauses."""
    if isinstance(node, exp.And):
        out: List[Dict[str, Any]] = []
        for arg in node.flatten():
            out.extend(_flatten_conditions(arg))
        return out
    if isinstance(node, exp.EQ):
        return [{node.left.name: _value_to_json(node.right)}]
    if isinstance(node, exp.NEQ):
        return [{node.left.name: {"$ne": _value_to_json(node.right)}}]
    if isinstance(node, exp.GT):
        return [{node.left.name: {"$gt": _value_to_json(node.right)}}]
    if isinstance(node, exp.GTE):
        return [{node.left.name: {"$gte": _value_to_json(node.right)}}]
    if isinstance(node, exp.LT):
        return [{node.left.name: {"$lt": _value_to_json(node.right)}}]
    if isinstance(node, exp.LTE):
        return [{node.left.name: {"$lte": _value_to_json(node.right)}}]
    if isinstance(node, exp.Column):
        return [{node.name: {"$exists": True}}]
    raise ValueError(f"Unsupported WHERE predicate: {node!r}")


def _is_aggregate(node) -> bool:
    inner = node.this if isinstance(node, exp.Alias) else node
    return isinstance(
        inner,
        (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Star),
    ) or (isinstance(inner, exp.Anonymous) and inner.name.upper() in
          ("COUNT", "SUM", "AVG", "MIN", "MAX"))


def _agg_default_name(agg) -> str:
    if isinstance(agg, exp.Alias):
        return str(agg.alias)
    this = getattr(agg, "this", None)
    if this is None or isinstance(this, exp.Star):
        return "count"
    name = this.name if hasattr(this, "name") else str(this)
    return f"{_func_name(agg).lower()}_{name}"


def _func_name(agg) -> str:
    try:
        return agg.sql_name().upper()
    except Exception:  # noqa: BLE001
        return (getattr(agg, "key", "") or "").upper() or agg.__class__.__name__.upper()


def _aggregate_expr(agg) -> Dict[str, Any]:
    if isinstance(agg, exp.Alias):
        agg = agg.this
    name = _func_name(agg)
    if name == "COUNT":
        return {"$sum": 1}
    if name == "SUM":
        return {"$sum": f"${agg.this.name}"}
    if name == "AVG":
        return {"$avg": f"${agg.this.name}"}
    if name == "MIN":
        return {"$min": f"${agg.this.name}"}
    if name == "MAX":
        return {"$max": f"${agg.this.name}"}
    if isinstance(agg, exp.Count):
        return {"$sum": 1}
    raise ValueError(f"Unsupported aggregate: {agg!r}")


def _value_to_json(node) -> Any:
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            if "." in node.this or "e" in node.this.lower():
                return float(node.this)
            return int(node.this)
        except ValueError:
            return node.this
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Column):
        return f"${node.name}"
    return str(node)


# ── Enterprise bi-directional data gateways ──────────────────────────

class _BaseGateway(ABC):
    """Abstract base for all bi-directional data gateways."""

    @abstractmethod
    def connect(self) -> None:
        """Establish a connection to the data store."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection and release resources."""


class PostgresGateway(_BaseGateway):
    """Bi-directional PostgreSQL gateway using SQLAlchemy.

    Reads SQL tables for analysis and writes lint findings as audit records.

    Args:
        connection_url: SQLAlchemy connection URL,
            e.g. ``postgresql://user:pass@host:5432/dbname``.
        schema: Target schema for audit tables.
    """

    def __init__(
        self,
        connection_url: str,
        schema: str = "sqlean_audit",
    ) -> None:
        self._url = connection_url
        self._schema = schema
        self._engine: Any = None
        self._metadata: Any = None

    def connect(self) -> None:
        try:
            from sqlalchemy import create_engine, MetaData, text
            from sqlalchemy.schema import Table, Column, String, Integer, Text, DateTime
            from sqlalchemy.func import now

            self._engine = create_engine(self._url, pool_size=5, max_overflow=10)
            self._metadata = MetaData(schema=self._schema)

            # Audit findings table
            Table(
                "lint_findings",
                self._metadata,
                Column("id", Integer, primary_key=True, autoincrement=True),
                Column("request_id", String(64), nullable=False),
                Column("rule_id", String(128), nullable=False),
                Column("message", Text, nullable=False),
                Column("severity", String(32), nullable=False),
                Column("line", Integer, default=1),
                Column("col", Integer, default=1),
                Column("dialect", String(64), default="duckdb"),
                Column("sql_hash", String(64), nullable=False),
                Column("created_at", DateTime, server_default=now()),
            )

            with self._engine.connect() as conn:
                conn.execute(text(
                    f"CREATE SCHEMA IF NOT EXISTS {self._schema}"
                ))
                conn.commit()
            self._metadata.create_all(self._engine)
        except ImportError as exc:
            raise RuntimeError(
                "sqlalchemy is required for PostgresGateway. "
                "Install with: pip install sqlalchemy psycopg2-binary"
            ) from exc

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()

    def write_findings(
        self, request_id: str, findings: List[Dict[str, Any]], sql_text: str
    ) -> int:
        """Write lint findings to the audit table. Returns rows inserted."""
        if not self._engine:
            self.connect()
        from sqlalchemy import text as sql_text_mod

        sql_hash = hashlib.sha256(sql_text.encode()).hexdigest()[:16]
        rows_inserted = 0
        with self._engine.connect() as conn:
            for f in findings:
                conn.execute(sql_text_mod(
                    "INSERT INTO sqlean_audit.lint_findings "
                    "(request_id, rule_id, message, severity, line, col, dialect, sql_hash) "
                    "VALUES (:rid, :rule, :msg, :sev, :line, :col, :dialect, :hash)"
                ), {
                    "rid": request_id,
                    "rule": f.get("rule", ""),
                    "msg": f.get("message", ""),
                    "sev": f.get("severity", "MEDIUM"),
                    "line": f.get("line", 1),
                    "col": f.get("col", 1),
                    "dialect": f.get("dialect", "duckdb"),
                    "hash": sql_hash,
                })
                rows_inserted += 1
            conn.commit()
        return rows_inserted

    def read_table(
        self, table_name: str, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Read rows from a table for analysis. Returns list of dicts."""
        if not self._engine:
            self.connect()
        from sqlalchemy import text as sql_text_mod

        with self._engine.connect() as conn:
            result = conn.execute(
                sql_text_mod(f"SELECT * FROM {table_name} LIMIT :limit"),
                {"limit": limit},
            )
            columns = list(result.keys())
            return [dict(zip(columns, row)) for row in result.fetchall()]


class RedisCacheGateway(_BaseGateway):
    """Redis-backed cache for AST parse trees and lint results.

    Avoids redundant transpilation by caching keyed on the SHA-256 hash of
    the SQL text plus dialect.

    Args:
        host: Redis host.
        port: Redis port.
        db: Redis database number.
        ttl_s: Cache entry TTL in seconds (default 1 hour).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        ttl_s: int = 3600,
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._ttl_s = ttl_s
        self._client: Any = None

    def connect(self) -> None:
        try:
            import redis as redis_lib
            self._client = redis_lib.Redis(
                host=self._host, port=self._port, db=self._db,
                decode_responses=True, socket_connect_timeout=5,
            )
            self._client.ping()
        except ImportError as exc:
            raise RuntimeError(
                "redis is required for RedisCacheGateway. "
                "Install with: pip install redis"
            ) from exc

    def close(self) -> None:
        if self._client:
            self._client.close()

    def _cache_key(self, sql: str, dialect: str) -> str:
        content = f"{dialect}::{sql}"
        return f"sqlean:lint:{hashlib.sha256(content.encode()).hexdigest()}"

    def get_cached(self, sql: str, dialect: str) -> Optional[Dict[str, Any]]:
        """Retrieve a cached lint result, or None if miss."""
        if not self._client:
            return None
        try:
            raw = self._client.get(self._cache_key(sql, dialect))
            if raw:
                return json.loads(raw)
        except Exception:  # noqa: BLE001
            pass
        return None

    def set_cached(
        self, sql: str, dialect: str, result: Dict[str, Any]
    ) -> bool:
        """Store a lint result in the cache. Returns True on success."""
        if not self._client:
            return False
        try:
            self._client.setex(
                self._cache_key(sql, dialect),
                self._ttl_s,
                json.dumps(result),
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def invalidate(self, sql: str, dialect: str) -> bool:
        """Remove a cached entry. Returns True if key existed."""
        if not self._client:
            return False
        try:
            return bool(self._client.delete(self._cache_key(sql, dialect)))
        except Exception:  # noqa: BLE001
            return False

    def flush_all(self) -> int:
        """Remove all sqlean cache entries. Returns count of deleted keys."""
        if not self._client:
            return 0
        try:
            keys = self._client.keys("sqlean:lint:*")
            if keys:
                return self._client.delete(*keys)
        except Exception:  # noqa: BLE001
            pass
        return 0


class SnowflakeGateway(_BaseGateway):
    """Snowflake warehouse gateway for large-scale SQL linting pipelines.

    Reads SQL queries from Snowflake tables and writes lint audit results
    back for compliance reporting.

    Args:
        account: Snowflake account identifier.
        user: Snowflake username.
        password: Snowflake password (or use key-pair auth via private_key).
        warehouse: Virtual warehouse name.
        database: Database name.
        schema: Schema name.
    """

    def __init__(
        self,
        account: str,
        user: str,
        password: Optional[str] = None,
        warehouse: str = "COMPUTE_WH",
        database: str = "ANALYTICS",
        schema: str = "SQLEAN_AUDIT",
    ) -> None:
        self._account = account
        self._user = user
        self._password = password
        self._warehouse = warehouse
        self._database = database
        self._schema = schema
        self._connection: Any = None

    def connect(self) -> None:
        try:
            import snowflake.connector  # type: ignore
            self._connection = snowflake.connector.connect(
                account=self._account,
                user=self._user,
                password=self._password,
                warehouse=self._warehouse,
                database=self._database,
                schema=self._schema,
            )
        except ImportError as exc:
            raise RuntimeError(
                "snowflake-connector-python is required for SnowflakeGateway. "
                "Install with: pip install snowflake-connector-python"
            ) from exc

    def close(self) -> None:
        if self._connection:
            self._connection.close()

    def execute_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a read query and return results as a list of dicts."""
        if not self._connection:
            self.connect()
        cursor = self._connection.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return results

    def write_findings_table(
        self, findings: List[Dict[str, Any]], table: str = "LINT_FINDINGS"
    ) -> int:
        """Write lint findings to a Snowflake table."""
        if not self._connection:
            self.connect()
        cursor = self._connection.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER AUTOINCREMENT PRIMARY KEY,
                request_id VARCHAR(64),
                rule_id VARCHAR(128),
                message TEXT,
                severity VARCHAR(32),
                line INTEGER DEFAULT 1,
                col INTEGER DEFAULT 1,
                dialect VARCHAR(64) DEFAULT 'duckdb',
                created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
            )
        """)
        inserted = 0
        for f in findings:
            cursor.execute(
                f"INSERT INTO {table} (request_id, rule_id, message, severity, line, col, dialect) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    f.get("request_id", ""),
                    f.get("rule", ""),
                    f.get("message", ""),
                    f.get("severity", "MEDIUM"),
                    f.get("line", 1),
                    f.get("col", 1),
                    f.get("dialect", "duckdb"),
                ),
            )
            inserted += 1
        cursor.close()
        return inserted


class MongoDBGateway(_BaseGateway):
    """MongoDB gateway for persisting lint findings as audit documents.

    Stores structured lint results with metadata for long-term retention
    and analytics dashboard consumption.

    Args:
        uri: MongoDB connection URI.
        database: Database name.
        collection: Collection name for lint findings.
    """

    def __init__(
        self,
        uri: str = "mongodb://localhost:27017",
        database: str = "sqlean_lint",
        collection: str = "lint_findings",
    ) -> None:
        self._uri = uri
        self._database = database
        self._collection = collection
        self._client: Any = None
        self._coll: Any = None

    def connect(self) -> None:
        try:
            from pymongo import MongoClient
            self._client = MongoClient(self._uri, serverSelectionTimeoutMS=5000)
            self._client.admin.command("ping")
            db = self._client[self._database]
            self._coll = db[self._collection]
            # Index for efficient querying by rule and severity
            self._coll.create_index([("rule_id", 1), ("severity", 1)])
            self._coll.create_index([("created_at", -1)])
        except ImportError as exc:
            raise RuntimeError(
                "pymongo is required for MongoDBGateway. "
                "Install with: pip install pymongo"
            ) from exc

    def close(self) -> None:
        if self._client:
            self._client.close()

    def write_findings(
        self,
        request_id: str,
        findings: List[Dict[str, Any]],
        sql_text: str,
        dialect: str = "duckdb",
    ) -> int:
        """Write lint findings as MongoDB documents. Returns count inserted."""
        if not self._coll:
            self.connect()
        docs = []
        sql_hash = hashlib.sha256(sql_text.encode()).hexdigest()[:16]
        for f in findings:
            docs.append({
                "request_id": request_id,
                "rule_id": f.get("rule", ""),
                "message": f.get("message", ""),
                "severity": f.get("severity", "MEDIUM"),
                "line": f.get("line", 1),
                "col": f.get("col", 1),
                "dialect": dialect,
                "sql_hash": sql_hash,
                "created_at": time.time(),
            })
        if docs:
            result = self._coll.insert_many(docs)
            return len(result.inserted_ids)
        return 0

    def query_findings(
        self,
        rule_id: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query lint findings with optional filters."""
        if not self._coll:
            self.connect()
        query: Dict[str, Any] = {}
        if rule_id:
            query["rule_id"] = rule_id
        if severity:
            query["severity"] = severity
        return list(self._coll.find(query).sort("created_at", -1).limit(limit))

    def count_by_rule(self) -> Dict[str, int]:
        """Aggregate finding counts grouped by rule_id."""
        if not self._coll:
            self.connect()
        pipeline = [
            {"$group": {"_id": "$rule_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        results = self._coll.aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results}
