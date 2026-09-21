"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Support assistant for sqlean-lint Pro.

Implements :class:`OfflineOnlineAgent`, a zero-cost built-in AI chatbot that
answers support, optimisation and rule-education questions about sqlean-lint.
It supports two complementary modes:

  * **Offline (default, air-gapped).** A deterministic, rule-based NLP decision
    tree resolves intent from the embedded knowledge base (``knowledge.py``)
    and a compact FAQ index. No network is ever touched and the path never
    raises — it always returns a structured, helpful reply.
  * **Online (optional).** When ``SQLEAN_LINT_CHAT_ENDPOINT`` is set (defaults
    to a free HuggingFace-compatible inference endpoint), queries are forwarded
    to that REST API via ``requests`` for richer, open-ended answers. Any
    failure falls back to the offline reply so the bot degrades gracefully in
    disconnected builds.

Both the CLI, GUI and notebook users consume the same public entry points:
``chat()`` and ``quick_reply()`` (thin wrappers) or ``OfflineOnlineAgent``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .knowledge import RULE_EDUCATION

# ── FAQ index ─────────────────────────────────────────────────────────

_FAQ: List[Dict[str, Any]] = [
    {"keywords": ["install", "pip", "setup", "install"],
     "answer": "Install sqlean-lint with `pip install -e .[dev]`. "
               "Run the QA probe with `python verify_install.py`."},
    {"keywords": ["license", "key", "activate", "activation", "mit"],
     "answer": "Run `sqlean-lint activate --key KEY`. Licenses are hardware-"
               "bound and include a 30-day offline lease. The project is "
               "released under the MIT License (c) 2026 Ahmad Bilal "
               "(AhmadBilalDSA) <kierninja@gmail.com>."},
    {"keywords": ["gui", "ui", "dashboard", "desktop", "open"],
     "answer": "Launch the desktop dashboard with `sqlean-lint ui`, or just run "
               "`sqlean-lint` with no arguments."},
    {"keywords": ["check", "lint", "scan", "cli"],
     "answer": "Run a headless scan with `sqlean-lint check <path> --format "
               "text|json|sarif`. Exit code 0 = clean, 1 = findings."},
    {"keywords": ["sarif", "github", "code scanning", "report"],
     "answer": "Use `--format sarif` and upload the report to GitHub code "
               "scanning or any SARIF v2.1.0 viewer."},
    {"keywords": ["lease", "offline", "30", "day", "grace", "hardware"],
     "answer": "A 30-day human-friendly lease covers hardware changes. It is "
               "HMAC-bound to your license key and can be inspected with "
               "`sqlean-lint lease`."},
    {"keywords": ["build", "exe", "nuitka", "windows", "binary"],
     "answer": "Build the standalone Windows binary with `python build_exe.py`. "
               "The output is `build/sqlean-lint-pro.exe`. SHA-256 verification "
               "steps are documented in SECURITY.md."},
    {"keywords": ["mongo", "mongodb", "csv", "convert", "polyglot", "pandas"],
     "answer": "Convert a CSV file to ANSI SQL (pandas-backed) or transpile a "
               "SQL SELECT into a MongoDB aggregation pipeline via "
               "`sqlean_lint.connectors.polyglot`."},
    {"keywords": ["keyboard", "shortcut", "ctrl", "hotkey", "hot key"],
     "answer": "GUI shortcuts: Ctrl+O = Browse SQL files, Ctrl+Enter = Scan "
               "directory, Ctrl+R = Auto-Fix All, Ctrl+H = Shortcut help."},
    {"keywords": ["high", "dpi", "scale", "4k", "blurry"],
     "answer": "The GUI enforces ctk widget/window scaling of 1.0 for crisp "
               "High-DPI (4K) rendering — no fuzzy text."},
]

_FALLBACK = (
    "I can help with installation, activation, the GUI, CLI checks, SARIF "
    "reports, builds, CSV/MongoDB conversion and the offline lease. I also "
    "explain SQL rules such as SQL-SARG-001 and SQL-CART-001. Try one of those "
    "topics or run `sqlean-lint --help`."
)

# Dedicated explanation of the underlying architecture (AST parsing).
_AST_EDUCATION = (
    "How sqlean-lint parses SQL (AST pipeline):\n\n"
    "1. SQLGlot parses the raw SQL text into an Abstract Syntax Tree (AST) — "
    "a typed tree of expression nodes (Select, From, Column, EQ, And, ...).\n"
    "2. Each rule registers a visitor that walks the AST and flags nodes "
    "matching a dangerous structural pattern (function-on-column, predicate-"
    "less join, leading-wildcard LIKE, etc.).\n"
    "3. Matches are normalised across dialects (e.g. CAST(... AS DATE)) so "
    "the analysis is dialect-independent.\n"
    "4. Findings are rendered as text, JSON or SARIF v2.1.0.\n\n"
    "Ask about a specific rule (SQL-SARG-001, SQL-CART-001) for remediation "
    "details."
)

# Intent keywords used by the offline rule-based NLP decision tree.
_INTENT_ROUTER: List[Dict[str, Any]] = [
    {"intent": "sarg", "pattern": re.compile(r"sarg|non[- ]?sargable", re.I)},
    {"intent": "cartesian", "pattern": re.compile(r"cart[ée]sian|cross join|cart", re.I)},
    {"intent": "ast", "pattern": re.compile(r"\bast\b|parse|parsing|abstract syntax", re.I)},
    {"intent": "syntax", "pattern": re.compile(r"syntax error|failed to parse|parse error", re.I)},
    {"intent": "notin", "pattern": re.compile(r"\bnot in\b|anti[- ]?join", re.I)},
    {"intent": "sort", "pattern": re.compile(r"\border by\b|sort|filesort", re.I)},
    {"intent": "star", "pattern": re.compile(r"select \*|select star|wildcard", re.I)},
    {"intent": "cast", "pattern": re.compile(r"\bcast\b|join key", re.I)},
    {"intent": "like", "pattern": re.compile(r"\blike\b|leading.*wildcard|substring", re.I)},
]

# Each rule explains itself with the math/why + remediation examples.
_RULE_ID_BY_INTENT: Dict[str, str] = {
    "sarg": "SQL-SARG-001",
    "cartesian": "SQL-CART-001",
    "notin": "SQL-NOTIN-001",
    "sort": "SQL-SORT-001",
    "star": "SQL-STAR-001",
    "cast": "SQL-CAST-001",
    "like": "SQL-LIKE-001",
    "syntax": "PARSER-001",
}

# Free public HuggingFace inference endpoint (no API key required for the
# lightweight hosted inference API on many public models).
_DEFAULT_ENDPOINT = os.environ.get(
    "SQLEAN_LINT_CHAT_ENDPOINT",
    "https://api-inference.huggingface.co/models/gpt2",
)


def _find_offline_answer(message: str) -> Dict[str, Any]:
    """Resolve *message* via the embedded FAQ + rule education (offline)."""
    lowered = message.lower()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    best: Optional[Dict[str, Any]] = None
    best_hits = 0
    for entry in _FAQ:
        hits = sum(1 for kw in entry["keywords"]
                   if re.search(rf"\b{re.escape(kw)}\b", lowered))
        if hits > best_hits:
            best_hits = hits
            best = {"source": "offline", "answer": entry["answer"],
                    "topic": entry["keywords"][0]}
    if best and best_hits > 0:
        return best
    if "ast" in tokens or "parsing" in tokens or "parser" in tokens \
            or _detect_intent(lowered) == "ast":
        return {"source": "offline", "answer": _AST_EDUCATION, "intent": "ast"}
    intent = _detect_intent(lowered)
    if intent:
        rule_id = _RULE_ID_BY_INTENT.get(intent)
        data = RULE_EDUCATION.get(rule_id or "")
        if data:
            return {"source": "offline", "answer": _rule_remediation(rule_id, data),
                    "intent": intent, "rule_id": rule_id}
    for rule_id, data in RULE_EDUCATION.items():
        if rule_id.lower() in lowered or str(data.get("title", "")).lower() in lowered:
            return {"source": "offline", "answer": _rule_remediation(rule_id, data),
                    "rule_id": rule_id}
    return {"source": "offline", "answer": _FALLBACK, "topic": "general"}


def _detect_intent(text: str) -> Optional[str]:
    """Return the first matching high-level intent, or ``None``."""
    for route in _INTENT_ROUTER:
        if route["pattern"].search(text):
            return route["intent"]
    return None


def _rule_remediation(rule_id: str, data: Dict[str, str]) -> str:
    """Compose a remediation-focused explanation for a rule."""
    return (f"{data['title']} (rule {rule_id}):\n\n{data['why']}\n\n"
            f"How to fix:\n{data['fix']}")


def _online_answer(message: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Optionally forward *message* to a free HuggingFace-compatible endpoint.

    Returns the offline *fallback* whenever the online call is unavailable,
    misses the expected response shape, or exceeds the timeout — so the
    chatbot degrades gracefully.
    """
    endpoint = os.environ.get("SQLEAN_LINT_CHAT_ENDPOINT") or _DEFAULT_ENDPOINT
    model = os.environ.get("SQLEAN_LINT_CHAT_MODEL")
    if not endpoint:
        return fallback

    try:
        import requests  # declared dependency
    except Exception:  # noqa: BLE001
        return fallback

    payload: Dict[str, Any] = {
        "inputs": message,
        "parameters": {"max_new_tokens": 120, "temperature": 0.4},
    }
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("HUGGINGFACE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        body = resp.json()
    except Exception:  # noqa: BLE001 - never let the online path crash the bot
        return fallback

    text: Optional[str] = None
    if isinstance(body, list) and body and isinstance(body[0], dict):
        text = body[0].get("generated_text")
    elif isinstance(body, dict):
        text = body.get("generated_text") or (body.get("choices") or [{}])[0] \
            .get("message", {}).get("content")
    if not text or not isinstance(text, str) or not text.strip():
        return fallback
    return {"source": "online", "answer": text.strip()}


class OfflineOnlineAgent:
    """Zero-cost built-in AI support assistant.

    Composes a deterministic offline rule-based NLP tree with an optional
    online augmentation layer. Designed to be embedded directly in the GUI
    (floating "Support Assistant" widget) and the CLI.
    """

    def __init__(self, *, online: Optional[bool] = None) -> None:
        self._online = online

    def ask(self, message: str) -> Dict[str, Any]:
        return chat(message, online=self._online)

    def answer(self, message: str) -> str:
        return self.ask(message)["answer"]


def chat(message: str, *, online: Optional[bool] = None) -> Dict[str, Any]:
    """Answer a support question.

    Args:
        message: The user's question.
        online: Force online mode; if ``None`` (default) the offline answer is
                always computed and an online augmentation is attempted only
                when the endpoint environment variables are set.

    Returns:
        A dict with at least ``answer`` and ``source`` keys.
    """
    message = (message or "").strip()
    if not message:
        return {"answer": _FALLBACK, "source": "offline"}

    offline = _find_offline_answer(message)
    use_online = (
        online if online is not None
        else bool(os.environ.get("SQLEAN_LINT_CHAT_ENDPOINT"))
    )
    if use_online:
        return _online_answer(message, offline)
    return offline


def quick_reply(message: str) -> str:
    """Convenience wrapper returning only the answer text."""
    return chat(message)["answer"]
