"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Allow ``python -m sqlean_lint`` execution of the Typer CLI."""
from __future__ import annotations

from .cli import app

if __name__ == "__main__":
    app()
