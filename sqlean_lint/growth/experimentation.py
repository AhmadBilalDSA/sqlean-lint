"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Product growth, analytics & experimentation toolkit.

Implements A/B testing capability using Optimizely/LaunchDarkly feature flags,
toggling specific experimental AST rules dynamically for a subset of users.
Connects conversion pipelines to Google Analytics 4 (GA4) Measurement
Protocol and PostHog.

Privacy-first: all analytics are opt-in and off by default. Flag evaluations
never include raw SQL or PII — only hashed anonymised user identifiers.

The feature flag backend degrades to a deterministic local bucketing engine
when no cloud provider is configured, so experiments work offline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("sqlean_lint.growth.experimentation")

# Opt-in gate for growth analytics.
def analytics_enabled() -> bool:
    """Return whether growth analytics are enabled (opt-in)."""
    return os.environ.get("SQLEAN_LINT_GROWTH", "0") == "1"


# ── Feature flag definitions ──────────────────────────────────────────

@dataclass
class FeatureFlag:
    """A single feature flag definition for A/B experimentation."""

    key: str
    description: str = ""
    default: str = "control"
    variants: List[str] = field(default_factory=lambda: ["control", "treatment"])
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("Feature flag key is required.")
        if self.default not in self.variants:
            self.variants = [self.default] + self.variants


# Catalog of experimental AST rules available for A/B experimentation.
EXPERIMENTAL_FLAGS: Dict[str, FeatureFlag] = {
    "ast.rule.premium_join_hint": FeatureFlag(
        key="ast.rule.premium_join_hint",
        description="Experimental join-hint analysis rule",
        variants=["control", "treatment"],
    ),
    "ast.rule.cte_inlining": FeatureFlag(
        key="ast.rule.cte_inlining",
        description="Inline CTEs for optimization experiments",
        variants=["control", "treatment"],
    ),
    "ast.rule.vector_projection": FeatureFlag(
        key="ast.rule.vector_projection",
        description="Enable projection reduction heuristic",
        variants=["control", "treatment"],
    ),
    "api.batch_concurrency": FeatureFlag(
        key="api.batch_concurrency",
        description="Grant higher batch concurrency to treatment cohort",
        variants=["4", "16"],
        default="4",
    ),
}


# ── Deterministic bucketing ───────────────────────────────────────────

def _stable_bucket(user_id: str, salt: str) -> int:
    """Compute a stable, deterministic 0-999 bucket for a user."""
    digest = hashlib.sha256(f"{user_id}:{salt}".encode()).hexdigest()
    return int(digest[:8], 16) % 1000


def _anonymous_id() -> str:
    import uuid
    ident = os.environ.get("SQLEAN_LINT_ANON_ID") or str(uuid.uuid4())
    return ident


# ── GA4 Measurement Protocol ──────────────────────────────────────────

class GA4Client:
    """Send conversion events to Google Analytics 4 via Measurement Protocol.

    Requires ``GA4_MEASUREMENT_ID`` and ``GA4_API_SECRET``.
    """

    ENDPOINT = "https://www.google-analytics.com/mp/collect"

    def __init__(self, measurement_id: str = "", api_secret: str = "",
                 client_id: str = "") -> None:
        self._measurement_id = measurement_id or os.environ.get(
            "GA4_MEASUREMENT_ID", ""
        )
        self._api_secret = api_secret or os.environ.get("GA4_API_SECRET", "")
        self._client_id = client_id or _anonymous_id()

    def track_event(self, event_name: str,
                    params: Optional[Dict[str, Any]] = None) -> bool:
        """Send a single event to GA4.

        Args:
            event_name: e.g. ``"activation_completed"``.
            params: Event parameters (no raw SQL, no PII).

        Returns:
            True if the event was accepted (HTTP 204).
        """
        if not self._measurement_id or not self._api_secret:
            logger.debug("GA4 not configured; skipping event.")
            return False
        if not analytics_enabled():
            return False

        payload = {
            "client_id": self._client_id,
            "events": [{
                "name": event_name,
                "params": dict(params or {}),
            }],
        }
        try:
            import requests
            resp = requests.post(
                self.ENDPOINT,
                params={
                    "measurement_id": self._measurement_id,
                    "api_secret": self._api_secret,
                },
                json=payload,
                timeout=5,
            )
            return resp.status_code == 204
        except Exception as exc:  # noqa: BLE001
            logger.warning("GA4 event failed: %s", exc)
            return False


# ── PostHog integration ───────────────────────────────────────────────

class PostHogClient:
    """Send events to PostHog for product analytics.

    Requires ``POSTHOG_API_KEY`` and (optionally) ``POSTHOG_HOST``.
    """

    def __init__(self, api_key: str = "", host: str = "https://app.posthog.com") -> None:
        self._api_key = api_key or os.environ.get("POSTHOG_API_KEY", "")
        self._host = host or os.environ.get("POSTHOG_HOST", "")
        # endpoint: /batch or /capture
        self._capture_url = f"{self._host}/batch"

    def track_event(self, event_name: str,
                    properties: Optional[Dict[str, Any]] = None) -> bool:
        """Send a single event to PostHog."""
        if not self._api_key:
            logger.debug("PostHog not configured; skipping event.")
            return False
        if not analytics_enabled():
            return False

        payload = {
            "api_key": self._api_key,
            "batch": [{
                "type": "capture",
                "event": event_name,
                "distinct_id": _anonymous_id(),
                "properties": dict(properties or {}),
                "timestamp": time.time(),
            }],
        }
        try:
            import requests
            resp = requests.post(
                self._capture_url, json=payload, timeout=5
            )
            return resp.status_code in (200, 201, 202)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostHog event failed: %s", exc)
            return False


# ── Optimizely / LaunchDarkly adapters ───────────────────────────────

class OptimizelyAdapter:
    """Adapter for the Optimizely Feature Experimentation SDK."""

    def __init__(self, sdk_key: str = "") -> None:
        self._sdk_key = sdk_key or os.environ.get("OPTIMIZELY_SDK_KEY", "")
        self._client: Any = None

    def connect(self) -> bool:
        if not self._sdk_key:
            return False
        try:
            import optimizely  # type: ignore
            self._client = optimizely.Optimizely(sdk_key=self._sdk_key)
            return True
        except ImportError:
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Optimizely init failed: %s", exc)
            return False

    def evaluate(self, flag_key: str, user_id: str,
                 attributes: Optional[Dict[str, Any]] = None) -> str:
        if not self._client:
            return "control"
        try:
            user = self._client.create_user_context(user_id, attributes or {})
            decision = user.decide(flag_key)
            return decision.variation_key if hasattr(decision, "variation_key") \
                else decision.variation_key
        except Exception:  # noqa: BLE001
            return "control"


class LaunchDarklyAdapter:
    """Adapter for the LaunchDarkly feature-flag SDK."""

    def __init__(self, sdk_key: str = "", project_key: str = "sqlean-lint") -> None:
        self._sdk_key = sdk_key or os.environ.get("LD_SDK_KEY", "")
        self._project_key = project_key
        self._client: Any = None

    def connect(self) -> bool:
        if not self._sdk_key:
            return False
        try:
            import ldclient  # type: ignore
            from ldclient.config import Config  # type: ignore
            ldclient.set_config(Config(self._sdk_key))
            self._client = ldclient.get_flat_client()
            return True
        except ImportError:
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("LaunchDarkly init failed: %s", exc)
            return False

    def evaluate(self, flag_key: str, user_id: str,
                 attributes: Optional[Dict[str, Any]] = None) -> str:
        if not self._client:
            return "control"
        try:
            user = {
                "key": user_id,
                "firstName": "",
                "custom": dict(attributes or {}),
            }
            value = self._client.variation(flag_key, user, "control")
            return str(value)
        except Exception:  # noqa: BLE001
            return "control"


# ── Local deterministic flag engine ───────────────────────────────────

class FlagEngine:
    """Deterministic A/B flag engine with optional cloud provider.

    Buckets users into control/treatment groups using a stable hash keyed
    by user id and flag salt. When a cloud provider (Optimizely/LaunchDarkly)
    is configured, it takes precedence.
    """

    def __init__(
        self,
        flags: Optional[Dict[str, FeatureFlag]] = None,
        provider: str = "local",
        salt: str = "sqlean-lint-v1",
    ) -> None:
        self._flags = dict(flags) if flags else dict(EXPERIMENTAL_FLAGS)
        self._provider = provider.lower()
        self._salt = salt
        self._optimizely = OptimizelyAdapter() if provider == "optimizely" else None
        self._ld = LaunchDarklyAdapter() if provider == "launchdarkly" else None
        self._enabled = {k: True for k in self._flags}

    def _resolve_variant(self, flag: FeatureFlag, user_id: str,
                         bucket: Optional[int] = None) -> str:
        """Deterministically choose a variant from a user bucket."""
        if bucket is None:
            bucket = _stable_bucket(user_id, self._salt + ":" + flag.key)
        idx = bucket % len(flag.variants)
        return flag.variants[idx]

    def evaluate(self, flag_key: str, user_id: str = "",
                 attributes: Optional[Dict[str, Any]] = None) -> str:
        """Evaluate a feature flag for a user, returning the variant.

        Args:
            flag_key: Flag identifier.
            user_id: Anonymised user id (hash preferred).
            attributes: Optional attributes passed to cloud providers.

        Returns:
            The variant assigned to this user (e.g. ``"control"``/
            ``"treatment"``).
        """
        flag = self._flags.get(flag_key)
        if flag is None:
            return "control"
        if not flag.enabled or not self._enabled.get(flag_key, True):
            return flag.default
        if not user_id:
            user_id = _anonymous_id()

        # Cloud providers take precedence when configured.
        if self._provider == "optimizely" and self._optimizely:
            self._optimizely.connect()
            return self._optimizely.evaluate(flag_key, user_id, attributes)
        if self._provider == "launchdarkly" and self._ld:
            self._ld.connect()
            return self._ld.evaluate(flag_key, user_id, attributes)

        return self._resolve_variant(flag, user_id)

    def is_enabled(self, flag_key: str) -> bool:
        """Return True if a flag is enabled (regardless of variant)."""
        flag = self._flags.get(flag_key)
        return bool(flag and flag.enabled and self._enabled.get(flag_key, True))

    def set_enabled(self, flag_key: str, enabled: bool) -> None:
        """Enable/disable a flag at runtime."""
        self._enabled[flag_key] = enabled

    def register_flag(self, flag: FeatureFlag) -> None:
        """Register a new feature flag at runtime."""
        self._flags[flag.key] = flag
        self._enabled[flag.key] = True

    def toggle_experimental_rule(
        self, rule_id: str, user_id: str = "",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Toggle an experimental AST rule for a specific user.

        Args:
            rule_id: The rule to evaluate (e.g. ``"premium_join_hint"``).
            user_id: Anonymised user id.
            attributes: Optional user attributes.

        Returns:
            True if the rule should be active for this user.
        """
        variant = self.evaluate(f"ast.rule.{rule_id}", user_id, attributes)
        return variant == "treatment"

    def exposure(self, flag_key: str, user_id: str, variant: str) -> None:
        """Record a flag exposure for analytics attribution."""
        try:
            from ..telemetry import track_feature_flag
            track_feature_flag(flag_key, variant, _stable_bucket(
                user_id, self._salt
            ).__str__())
        except Exception:  # noqa: BLE001
            pass


# ── FeatureFlagManager ────────────────────────────────────────────────

# Beta rule flag keys, keyed by the canonical rule identifier used elsewhere in
# the linter. Adding a key here exposes a new experimental rule to the manager.
BETA_RULE_FLAGS: Dict[str, str] = {
    "nested_loop_join": "ast.rule.nested_loop_join",
    "index_hint": "ast.rule.index_hint",
}


class FeatureFlagManager:
    """Dictates whether beta rules are enabled for specific user IDs.

    Wraps the deterministic bucket engine and exposes a rule-oriented API so
    the linter can ask "should this experimental rule run for this user?"
    without touching flag plumbing. Rules such as the experimental
    nested-loop-join detector are bucketed per anonymised user id, so beta
    behaviour is delivered to a stable subset of users for A/B measurement.

    Privacy-first: evaluations are deterministic and local by default; only an
    anonymised (hashed) user id is used for bucketing.
    """

    def __init__(
        self,
        *,
        provider: str = "local",
        salt: str = "sqlean-lint-v1",
        allowlist: Optional[Set[str]] = None,
    ) -> None:
        self._engine = FlagEngine(provider=provider, salt=salt)
        self._allowlist: Set[str] = set(allowlist or [])
        # Register beta-rule flags if not already present.
        for flag_key in BETA_RULE_FLAGS.values():
            if flag_key not in self._engine._flags:
                self._engine.register_flag(
                    FeatureFlag(key=flag_key, description="Experimental AST rule")
                )

    def beta_rule_enabled(self, rule_id: str, user_id: str = "") -> bool:
        """Return whether *rule_id* is active for the given user.

        Args:
            rule_id: Canonical rule identifier (e.g. ``"nested_loop_join"``).
            user_id: Anonymised user id (hashed preferred for privacy).

        Returns:
            True if the beta rule should run for this user (allowlisted users
            are always enabled).
        """
        flag_key = BETA_RULE_FLAGS.get(rule_id)
        if flag_key is None:
            return False
        if user_id in self._allowlist:
            return True
        return self._engine.toggle_experimental_rule(rule_id, user_id)

    def is_beta_rule(self, rule_id: str) -> bool:
        """Return whether *rule_id* is a managed beta rule."""
        return rule_id in BETA_RULE_FLAGS

    def evaluate(self, flag_key: str, user_id: str = "",
                 attributes: Optional[Dict[str, Any]] = None) -> str:
        """Evaluate an arbitrary feature flag for a user (variant)."""
        return self._engine.evaluate(flag_key, user_id, attributes)

    def is_enabled(self, flag_key: str) -> bool:
        """Return whether a flag is enabled globally."""
        return self._engine.is_enabled(flag_key)

    def set_enabled(self, flag_key: str, enabled: bool) -> None:
        """Enable/disable a flag for the manager scoped engine."""
        self._engine.set_enabled(flag_key, enabled)

    def add_to_allowlist(self, user_id: str) -> None:
        """Force-enable all beta rules for a specific user id (staff/test)."""
        self._allowlist.add(user_id)

    def remove_from_allowlist(self, user_id: str) -> None:
        """Remove a user from the beta allowlist."""
        self._allowlist.discard(user_id)

    def distribution(self, rule_id: str,
                     user_ids: List[str]) -> Dict[str, int]:
        """Return a control/treatment bucket distribution for a rule cohort.

        Args:
            rule_id: Canonical rule identifier.
            user_ids: List of anonymised user ids.

        Returns:
            A dict counting users in each variant bucket for the rule.
        """
        counts: Dict[str, int] = {}
        for uid in user_ids:
            variant = "control"
            if self.beta_rule_enabled(rule_id, uid):
                variant = "treatment"
            counts[variant] = counts.get(variant, 0) + 1
        return counts


# ── Conversion pipeline ───────────────────────────────────────────────

class ConversionPipeline:
    """Routes product conversion events to GA4 and PostHog."""

    def __init__(self) -> None:
        self._ga4 = GA4Client()
        self._posthog = PostHogClient()
        self._listeners: List[Callable[[str, Dict[str, Any]], None]] = []

    def track(self, event_name: str,
              params: Optional[Dict[str, Any]] = None) -> bool:
        """Track a conversion event across configured channels.

        Args:
            event_name: Conversion event name.
            params: Event parameters (no PII / raw SQL).

        Returns:
            True if at least one channel accepted the event.
        """
        if not analytics_enabled():
            return False
        params = dict(params or {})
        params.setdefault("ts", int(time.time()))

        delivered = False
        if self._ga4.track_event(event_name, params):
            delivered = True
        if self._posthog.track_event(event_name, params):
            delivered = True

        for listener in self._listeners:
            try:
                listener(event_name, params)
            except Exception:  # noqa: BLE001
                pass
        return delivered

    def on_event(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register a synchronous listener for conversion events."""
        self._listeners.append(callback)

    def start_checkout(self, plan: str) -> bool:
        """Track a checkout-start conversion event."""
        return self.track("checkout_started", {"plan": plan})

    def completed_activation(self, plan: str, seats: int) -> bool:
        """Track a license activation conversion."""
        return self.track("activation_completed", {
            "plan": plan, "seats": seats,
        })
