"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Hybrid AI query optimizer for sqlean-lint.

Sends the linted SQL (with rule context) to a local or remote LLM and
validates the returned suggestion against the local linter.  The default
provider is Ollama running locally -- no data ever leaves the machine
unless the user explicitly configures a remote endpoint.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .engine import lint_query

# ── Constants ──────────────────────────────────────────────────────────

OLLAMA_ENDPOINT: str = "http://localhost:11434/api/generate"
DEFAULT_LOCAL_MODEL: str = "deepseek-coder"

PROVIDER_ENV_KEYS: Dict[str, str] = {
    "ollama": "SQLEAN_OLLAMA_ENDPOINT",
    "openai": "SQLEAN_OPENAI_KEY",
    "anthropic": "SQLEAN_ANTHROPIC_KEY",
}

_DEFAULT_MODELS: Dict[str, str] = {
    "ollama": "deepseek-coder",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}


# ── Dataclasses ────────────────────────────────────────────────────────

@dataclass
class AISuggestion:
    """Result of an AI-assisted optimization attempt."""

    provider: str = ""
    model: str = ""
    suggestion: str = ""
    baseline_violations: int = 0
    remaining_violations: int = 0
    validated: bool = False
    parses: bool = False
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "provider": self.provider,
            "model": self.model,
            "suggestion": self.suggestion,
            "baseline_violations": self.baseline_violations,
            "remaining_violations": self.remaining_violations,
            "validated": self.validated,
            "parses": self.parses,
            "error": self.error,
            "notes": list(self.notes),
        }


# ── Internal helpers ───────────────────────────────────────────────────

def _build_prompt(sql: str, dialect: str, baseline_violations: int) -> str:
    """Construct the optimisation prompt for the LLM."""
    return (
        "You are a SQL performance optimizer.  The following SQL query was "
        "linted and found to have {n} violation(s).  Rewrite the query to "
        "eliminate as many violations as possible while preserving semantic "
        "equivalence.  Return ONLY the optimized SQL, no explanation.\n\n"
        "SQL Dialect: {d}\n\n"
        "Original SQL:\n{sql}"
    ).format(n=baseline_violations, d=dialect, sql=sql)


def _call_ollama(
    prompt: str,
    model: str,
    endpoint: str,
) -> str:
    """Send a generate request to a local Ollama instance."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")
    req = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "").strip()


def _call_openai(
    prompt: str,
    model: str,
    endpoint: str,
    api_key: str,
) -> str:
    """Send a chat completion request to the OpenAI API."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }).encode("utf-8")
    req = Request(
        endpoint or "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"].strip()


def _call_anthropic(
    prompt: str,
    model: str,
    endpoint: str,
    api_key: str,
) -> str:
    """Send a message request to the Anthropic API."""
    payload = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = Request(
        endpoint or "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    return body["content"][0]["text"].strip()


def _extract_sql(text: str) -> str:
    """Strip markdown fences and other wrapping from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


# ── Public API ─────────────────────────────────────────────────────────

def optimize_with_ai(
    sql: str,
    dialect: str = "duckdb",
    provider: str = "ollama",
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> AISuggestion:
    """Send *sql* to an LLM provider and validate the suggestion locally.

    Returns an :class:`AISuggestion` with the result.  On any failure the
    ``error`` field is set and the suggestion is empty.
    """
    chosen_model = model or _DEFAULT_MODELS.get(provider, DEFAULT_LOCAL_MODEL)

    # Baseline lint.
    try:
        baseline = lint_query(sql, dialect)
        baseline_count = baseline.violation_count
    except Exception as exc:  # noqa: BLE001
        return AISuggestion(
            provider=provider,
            model=chosen_model,
            error=f"Baseline lint failed: {exc}",
        )

    prompt = _build_prompt(sql, dialect, baseline_count)
    env_key = PROVIDER_ENV_KEYS.get(provider, "")

    try:
        if provider == "ollama":
            chosen_endpoint = endpoint or os.environ.get(env_key, OLLAMA_ENDPOINT)
            raw = _call_ollama(prompt, chosen_model, chosen_endpoint)
        elif provider == "openai":
            api_key = os.environ.get(env_key, "")
            if not api_key:
                return AISuggestion(
                    provider=provider,
                    model=chosen_model,
                    error=f"Environment variable {env_key} not set.",
                )
            raw = _call_openai(prompt, chosen_model, endpoint or "", api_key)
        elif provider == "anthropic":
            api_key = os.environ.get(env_key, "")
            if not api_key:
                return AISuggestion(
                    provider=provider,
                    model=chosen_model,
                    error=f"Environment variable {env_key} not set.",
                )
            raw = _call_anthropic(prompt, chosen_model, endpoint or "", api_key)
        else:
            return AISuggestion(
                provider=provider,
                model=chosen_model,
                error=f"Unknown provider: {provider!r}.",
            )
    except (URLError, OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return AISuggestion(
            provider=provider,
            model=chosen_model,
            error=f"Provider request failed: {exc}",
        )

    suggestion = _extract_sql(raw)
    if not suggestion:
        return AISuggestion(
            provider=provider,
            model=chosen_model,
            error="LLM returned an empty suggestion.",
            baseline_violations=baseline_count,
        )

    # Validate the suggestion locally.
    notes: List[str] = []
    parses = False
    remaining = 0
    validated = False

    try:
        suggestion_result = lint_query(suggestion, dialect)
        parses = True
        remaining = suggestion_result.violation_count
        if remaining < baseline_count:
            notes.append(
                f"Violations reduced from {baseline_count} to {remaining}."
            )
            validated = True
        elif remaining == baseline_count:
            notes.append("Violation count unchanged.")
        else:
            notes.append(
                f"Warning: violations increased from {baseline_count} to {remaining}."
            )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"Suggestion failed local validation: {exc}")

    return AISuggestion(
        provider=provider,
        model=chosen_model,
        suggestion=suggestion,
        baseline_violations=baseline_count,
        remaining_violations=remaining,
        validated=validated,
        parses=parses,
        notes=notes,
    )
