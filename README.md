<div align="center">

# `sqlean-lint`

**High-Performance SQL Linter, 9-Dialect Transpiler & AI-Powered Query Optimizer.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg?style=for-the-badge&logo=python)](https://python.org)
[![Dialects](https://img.shields.io/badge/Dialects-9%20Supported-orange.svg?style=for-the-badge)](https://github.com)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-emerald.svg?style=for-the-badge)](https://github.com)

[Features](#-key-features) &bull; [Installation](#-installation) &bull; [CLI Commands](#-cli-reference) &bull; [Architecture](#-architecture) &bull; [Quick Start](#-quick-start) &bull; [Contributing](#-contributing)

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

| Command | Usage | Description |
| :--- | :--- | :--- |
| **status** | `sqlean-lint status` | Displays license state, active tier, and system health. |
| **activate** | `sqlean-lint activate --key <KEY>` | Activates Pro licensing and unlocks enterprise engines. |
| **convert** | `sqlean-lint convert query.sql --target postgres` | Transpiles queries, JSON schemas, or DataFrame definitions. |
| **ai** | `sqlean-lint ai validate query.sql` | Runs local LLM semantic analysis and index suggestions. |
| **update** | `sqlean-lint update` | Checks GitHub Releases for new updates and fixes. |
| **deactivate** | `sqlean-lint deactivate` | Clears local license tokens. |

---

## 🏛️ Architecture & Module Inventory

```text
sqlean_lint/
├── features.py     # Pro feature gating, license verification & tier enforcement
├── security.py     # SHA-256 origin watermarking & integrity checks
├── knowledge.py    # Core SQL rule education database (8 built-in engines)
├── autofix.py      # Equivalence verification, AST patcher, source re-writer
├── transpiler.py   # Multi-dialect transpiler, JSON-to-SQL, DDL generator
└── profiler.py     # Peak RSS memory tracking, execution runtime analyzer
```

---

## ⚡ Quick Start

### 1. Lint & Autofix Queries
```bash
sqlean-lint check ./queries/sample.sql --fix
```

### 3. AI Performance Diagnostics
```bash
sqlean-lint ai inspect complex_join.sql --provider ollama --model qwen2.5-coder:7b
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
