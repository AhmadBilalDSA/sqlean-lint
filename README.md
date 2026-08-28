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

- **Blazing-Fast AST Linting:** In-depth SQL static analysis with strict rule evaluation and instant feedback.
- **9-Dialect Transpiler:** Seamlessly convert queries across PostgreSQL, MySQL, SQLite, Snowflake, BigQuery, T-SQL, DuckDB, Redshift, and Oracle.
- **Local AI Query Validation:** Validate query semantics and optimize performance with local LLM integration (Ollama / OpenAI / Anthropic).
- **Smart Autofix & Patching:** Automatically refactors query anti-patterns, format violations, and syntax errors with verified equivalence.
- **Resource & RSS Profiling:** Built-in execution profiler tracking memory consumption, peak RSS, and query cost estimates.
- **Tamper-Proof Verification:** SHA-256 origin watermarking for enterprise query auditing and security integrity.

---

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/AhmadBilalDSA/sqlean-lint.git
cd sqlean-lint

# Install in editable mode
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
