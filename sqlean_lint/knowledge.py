"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Rule education database for sqlean-lint.
Each entry in :data:`RULE_EDUCATION` maps a rule ID to a human-readable
explanation that can be surfaced in CLI output, the web studio, or MCP
tool responses.  All content is generated offline — no network calls.
"""
from __future__ import annotations

from typing import Any, Dict

RULE_EDUCATION: Dict[str, Dict[str, str]] = {
    "SQL-CART-001": {
        "title": "Cartesian / predicate-less join",
        "why": (
            "A join without an ON predicate (or with a tautology) produces the "
            "Cartesian product of both inputs, yielding N multiplied by M rows. "
            "This causes explosive memory and CPU consumption that grows "
            "multiplicatively with table size, often turning millisecond queries "
            "into minutes or hours."
        ),
        "fix": (
            "1. Add a meaningful ON predicate that restricts the join to "
            "matching key pairs.\n"
            "2. If a cross join is truly intentional, wrap it in a LATERAL or "
            "apply a LIMIT early to bound the output.\n"
            "3. Review the query logic — an accidental omission of the ON "
            "clause is the most common cause."
        ),
        "learn": "https://use-the-index-luke.com/sql/anatomy/join",
    },
    "SQL-SARG-001": {
        "title": "Non-SARGable predicate",
        "why": (
            "Applying a function to a column in a WHERE clause (e.g. "
            "UPPER(col) = 'VALUE' or YEAR(dt) = 2026) prevents the database "
            "from using an index on that column.  The engine must evaluate the "
            "expression against every row, forcing a full table scan."
        ),
        "fix": (
            "1. Rewrite the predicate so the column stands alone on one side "
            "of the comparison: col = 'value' or col >= '2026-01-01'.\n"
            "2. Add a generated/persisted column (or a functional index) if "
            "the transformed form is needed for correctness.\n"
            "3. Check the execution plan (EXPLAIN) to confirm index usage "
            "after the change."
        ),
        "learn": "https://use-the-index-luke.com/writing",
    },
    "SQL-NOTIN-001": {
        "title": "NOT IN with subquery and NULLs",
        "why": (
            "NOT IN (SELECT col ...) silently returns no rows when any NULL "
            "value exists in the subquery result, because SQL NULL "
            "comparisons yield UNKNOWN and NOT IN becomes NOT TRUE for every "
            "row.  This is almost never the desired behaviour and leads to "
            "silent data loss."
        ),
        "fix": (
            "1. Replace NOT IN with NOT EXISTS, which compares row-by-row "
            "and is NULL-safe.\n"
            "2. Alternatively, add IS NOT NULL to the subquery to exclude "
            "NULLs explicitly.\n"
            "3. Use LEFT JOIN ... IS NULL for the same anti-join semantics "
            "when the optimizer handles it better."
        ),
        "learn": "https://use-the-index-luke.com/sql/where-clause/in-and-not-in",
    },
    "SQL-SORT-001": {
        "title": "Unbounded ORDER BY / SORT",
        "why": (
            "ORDER BY without a corresponding LIMIT forces the database to "
            "sort the entire result set in memory (or spill to disk).  For "
            "large tables this is extremely expensive and often unnecessary "
            "when only a sample or the first N rows are needed."
        ),
        "fix": (
            "1. Add a LIMIT clause that matches the actual number of rows "
            "the caller consumes.\n"
            "2. If the sort is for display pagination, use keyset pagination "
            "instead of OFFSET + ORDER BY.\n"
            "3. Ensure a supporting index exists on the ORDER BY columns to "
            "avoid a filesort."
        ),
        "learn": "https://use-the-index-luke.com/sorting-and-grouping",
    },
    "SQL-STAR-001": {
        "title": "SELECT * usage",
        "why": (
            "SELECT * retrieves all columns including ones the caller may "
            "never read.  This wastes I/O, defeats columnar storage "
            "optimisation, breaks clients when schema evolves, and prevents "
            "the engine from using covering indexes."
        ),
        "fix": (
            "1. Enumerate only the columns that are actually consumed by "
            "the application or downstream query.\n"
            "2. For ad-hoc exploration, SELECT * is acceptable but should "
            "not be committed to production code.\n"
            "3. When building views or materialised tables, explicitly list "
            "columns to create a stable contract."
        ),
        "learn": "https://use-the-index-luke.com/sql/anatomy/select-star",
    },
    "SQL-CAST-001": {
        "title": "CAST on join key",
        "why": (
            "Wrapping a join key in CAST() prevents the query planner from "
            "using pre-built hash tables or index seeks on the raw column. "
            "Every probe row must be converted at runtime, and storage-level "
            "zone maps and clustering on the original type stop applying."
        ),
        "fix": (
            "1. Persist both keys in the same physical type at schema "
            "design time so no runtime cast is needed.\n"
            "2. If the types genuinely differ, cast the constant/literal "
            "side once rather than the column side.\n"
            "3. Consider a generated column that stores the casted value "
            "with an index on it."
        ),
        "learn": "https://use-the-index-luke.com/sql/anatomy/join",
    },
    "SQL-LIKE-001": {
        "title": "Leading wildcard LIKE pattern",
        "why": (
            "A LIKE pattern that starts with '%' (e.g. '%suffix') removes "
            "any prefix anchor, so the storage engine must evaluate the "
            "pattern against every row of the column.  This forces a full "
            "column scan even when an index exists."
        ),
        "fix": (
            "1. Anchor a fixed prefix whenever possible: 'prefix%'.\n"
            "2. For suffix searches, maintain a reversed column with an "
            "index and match against 'rev(needle)%'.\n"
            "3. For full-text or substring search, use trigram indexes "
            "(pg_trgm), GIN/GiST indexes, or a dedicated full-text "
            "engine."
        ),
        "learn": "https://use-the-index-luke.com/sql/where-clause/like",
    },
    "PARSER-001": {
        "title": "SQL failed to parse",
        "why": (
            "The SQL source contains syntax that the parser for the "
            "selected dialect cannot accept.  This may be a genuine syntax "
            "error, a dialect mismatch (e.g. T-SQL syntax parsed as "
            "DuckDB), or usage of a feature not yet supported by sqlglot."
        ),
        "fix": (
            "1. Review the reported line and column for obvious typos or "
            "missing keywords.\n"
            "2. Confirm the correct --dialect flag matches the SQL's "
            "intended target engine.\n"
            "3. Simplify the statement to isolate the problematic clause, "
            "then fix or restructure it."
        ),
        "learn": "https://docs.sqlglossary.com/",
    },
}
