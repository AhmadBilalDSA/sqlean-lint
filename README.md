<div align="center">


# ⚡ sqlean-lint


### *High-Performance AST Static Analysis & Performance Optimization Engine for SQL*


[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-38bdf8?style=for-the-badge&logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-a855f7?style=for-the-badge)](#)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-f59e0b?style=for-the-badge)](#)
[![Architecture](https://img.shields.io/badge/Architecture-AST%20Visitor-emerald?style=for-the-badge)](#)


<p align="center">
  A local-first, zero-runtime-leak SQL linter and AST mutation toolkit.<br />
  Detects non-SARGable bottlenecks, Cartesian joins, and schema hazards before they hit production.
</p>


[Key Features](#-key-features) •
[Architecture](#-architecture) •
[Rule Reference](#-built-in-ruleset) •
[Quickstart](#-quickstart) •
[PRO Edition](#-pro-edition)


---


</div>


## 🌟 Key Features


* **Deterministic AST Traversal**: Operates strictly on syntax trees via `sqlglot`—no fragile regex matching.
* **Dialect Native**: Full semantic support across **PostgreSQL**, **DuckDB**, **MySQL**, and **SQLite**.
* **Zero-Leak Memory Engine**: Hardened test and parse runner operating at <110 MB peak working set.
* **Sub-Millisecond Diagnostics**: Identifies anti-patterns, non-SARGable clauses, and unbounded projections with exact line-and-column fidelity.
* **Dual Interface Ready**: High-throughput terminal CLI for CI/CD gates + modern CustomTkinter Studio GUI for desktop analysis.


---


## 🏛 Architecture


```
Raw Query ──► Lexer & Tokenizer ──► AST Syntax Graph ──► Visitor Passes
│
┌───────────────────────┬─────────────────────────┤
▼                       ▼                         ▼
[SARGability Engine]    [Join & Schema Guard]    [Heuristic Cost Rating]
│                       │                         │
└───────────────────────┼─────────────────────────┘
▼
Structured Diagnostic Tokens / Diffs
```




---


## 📋 Built-In Ruleset


| Code | Severity | Classification | Target Pattern | Suggested Remediation |
| :--- | :--- | :--- | :--- | :--- |
| `PRF-E101` | **Error** | Performance | Unbounded `SELECT *` in subqueries | Explicit projection list |
| `PRF-E102` | **Error** | Correctness | Comma joins / Missing `ON` | Explicit `INNER JOIN ... ON` |
| `PRF-W103` | **Warning** | Performance | Non-SARGable function predicate | B-Tree indexable range scan |
| `PRF-W104` | **Warning** | Performance | Leading wildcard `LIKE '%...'` | Trigram index or exact match |
| `COR-E201` | **Error** | Correctness | `NOT IN` with nullable subquery | Transform to `NOT EXISTS` |
| `STY-W301` | **Warning** | Optimization | Unbounded `ORDER BY` inside CTE | Push down ordering / apply `LIMIT` |


---


## 🚀 Quickstart


### Installation


```bash
# Clone the repository
git clone https://github.com/AhmadBilalDSA/sqlean-lint.git
cd sqlean-lint

# Install in editable mode with development tooling
pip install -e .
```


### CLI Usage


```bash
# Lint a single query or script
sqlean-lint scan queries/analytics.sql --dialect postgres

# Run automated batch audit with JSON output
sqlean-lint scan ./models --dialect duckdb --format json
```


### Running the Test Suite


```bash
python -m pytest tests/ -v
```


## 💎 PRO Edition


The sqlean-lint PRO suite bundles advanced database refactoring tools into a standalone desktop application:


* **AST Mutation Engine**: One-click automatic query rewriting for SARGable transformations and join normalization.
* **CustomTkinter Dark Studio**: Visual AST tree explorer, live typing debounce (300ms), and side-by-side git-style diffs.
* **Standalone Windows Binary**: Single-file compiled `.exe` powered by Nuitka/PyInstaller with zero system prerequisites.
* **Enterprise CI/CD Artifacts**: Direct SARIF generation for GitHub Code Scanning and native JUnit test reports.


Crafted with care by Ahmad Bilal