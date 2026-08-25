# sqlean-lint

**Enterprise-grade, zero-telemetry Semantic SQL Performance Linter, AST Auto-Optimizer and Cloud Cost Gating Engine.**

Everything runs **100% locally**: AST parsing via in-process `sqlglot`, cost estimation is a documented deterministic model (optionally cross-checked with local DuckDB), and every artifact — including the HTML dashboard — is fully self-contained. No network calls. No telemetry. No external assets.

## Install

```bash
pip install -e .[dev]
```

## CLI

```bash
sqlean-lint queries/ --dialect snowflake                # rich terminal report
sqlean-lint "**/*.sql" --format json                    # CI-friendly JSON
sqlean-lint migrations/ --fix                           # apply provably safe rewrites
sqlean-lint --query "SELECT * FROM a CROSS JOIN b" --fail-on critical
sqlean-lint etl/ --format html --output report.html     # air-gapped dashboard
```

Exit codes: `0` gate passed, `1` quality-gate breached, `2` usage error.

### Rules

| Rule ID | Severity | Detects |
| --- | --- | --- |
| SQL-CART-001 | CRITICAL | CROSS JOIN / comma joins / predicate-less joins (O(N*M)) |
| SQL-SARG-001 | HIGH | Non-SARGable predicates (`YEAR(col)=2026`, `UPPER(col)='X'`, `col+1>10`) |
| SQL-NOTIN-001 | HIGH | `NOT IN (SELECT ...)` NULL three-valued-logic trap |
| SQL-SORT-001 | HIGH | Unbounded `ORDER BY` inside CTEs/subqueries |
| SQL-STAR-001 | MEDIUM | `SELECT *` inside CTEs/subqueries |
| SQL-CAST-001 | MEDIUM | `CAST(...)` on join keys |
| SQL-LIKE-001 | MEDIUM | Leading-wildcard `LIKE '%abc'` |

### Safe rewrites (`--fix` / optimizer)

* `YEAR(d) = 2026` → `d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)`
* `DATE(d) = '2026-01-15'` → half-open daily range
* `x NOT IN (SELECT c FROM t WHERE p)` → `NOT EXISTS (SELECT 1 FROM t WHERE p AND c = x)` (guarded)
* Redundant CTE `ORDER BY` (no LIMIT) removal

## MCP server (stdio only)

```json
{
  "mcpServers": {
    "sqlean-lint": {
      "command": "sqlean-lint-mcp",
      "args": []
    }
  }
}
```

Tools: `lint_query`, `optimize_query`, `estimate_query_cost`. Transport is strictly stdin/stdout JSON-RPC 2.0 — no ports are opened.

## GitHub Action

```yaml
- uses: actions/checkout@v4
- name: SQLean-Lint gate
  uses: path/to/sqlean-lint/action.yml
  with:
    paths: "migrations/**/*.sql"
    dialect: snowflake
    fail-on: high
```

Outputs: `issues_found`, `risk_score`, `has_critical`.

## Development

```bash
pip install -e .[dev]
python -m pytest -v          # full offline suite
python -m compileall .       # syntax sanity
```
