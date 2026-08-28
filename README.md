<div align="center">

# `sqlean-lint`

**High-Performance SQL Linter, 9-Dialect Transpiler & AI-Powered Query Optimizer.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg?style=for-the-badge&logo=python)](https://python.org)
[![Dialects](https://img.shields.io/badge/Dialects-9%20Supported-orange.svg?style=for-the-badge)](https://github.com)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-emerald.svg?style=for-the-badge)](https://github.com)

[Features](#-key-features) &bull; [Installation](#-installation) &bull; [CLI Commands](#-cli-reference) &bull; [Module Architecture](#-architecture) &bull; [Contributing](#-contributing)

</div>

---

## 🚀 Key Features

- **Multi-Dialect Support:** Seamlessly parse, lint, and transpile between SQLite, PostgreSQL, MySQL, DuckDB, Snowflake, BigQuery, T-SQL, Oracle, and SparkSQL.
- **Advanced Query Linter:** Detects anti-patterns, missing indexes, cartesian joins, performance bottlenecks, and security flaws.
- **AI-Powered Query Optimizer:** Intelligently rewrites sub-optimal queries for maximum execution efficiency.
- **High-Performance CLI:** Built with Typer and Rich for beautiful terminal outputs and robust scripting integration.

---

## 📦 Installation

Install in editable mode:

```bash
pip install -e .
```

---

## 🛠️ CLI Reference

```bash
# Lint a SQL file or directory
sqlean-lint lint query.sql

# Transpile between dialects
sqlean-lint transpile query.sql --from sqlite --to postgres
```

---

## 🏛️ Architecture

```
sqlean-lint/
├── core/          # Parsing & transpiler engines
├── linter/        # Rule checker & anti-pattern detectors
├── optimizer/     # AI & cost-based query optimization
└── cli.py         # Typer-based command interface
```

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
