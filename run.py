"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Top-level launcher for PyInstaller-compatible builds.

Prevents ``ImportError: attempted relative import with no known parent package``.
"""
from sqlean_lint.cli import app

if __name__ == "__main__":
    app()
