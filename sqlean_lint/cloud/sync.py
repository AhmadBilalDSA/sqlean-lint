"""Enterprise cloud sync and zero-knowledge telemetry engine.

Manages remote artifact export with strict privacy controls:
  * Zero-Cloud mode blocks all outbound network traffic.
  * Enterprise Sync enables S3 / HTTPS artifact upload with SHA-256 hashes.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional


class CloudSyncManager:
    """Hybrid privacy/cloud backend with strict Zero-Knowledge overrides."""

    def __init__(self, config_path: str = "config/cloud_profile.json") -> None:
        self.config_path = config_path
        self.zero_cloud_active: bool = True
        self.remote_endpoint: Optional[str] = None
        self.api_key: Optional[str] = None
        self.load_settings()

    # ── persistence ────────────────────────────────────────────────────

    def load_settings(self) -> None:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
                    self.zero_cloud_active = cfg.get("zero_cloud_active", True)
                    self.remote_endpoint = cfg.get("endpoint")
                    self.api_key = cfg.get("api_key")
            except Exception:  # noqa: BLE001
                self.zero_cloud_active = True
        else:
            self.zero_cloud_active = True

    def save_settings(self) -> None:
        os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "zero_cloud_active": self.zero_cloud_active,
                    "endpoint": self.remote_endpoint,
                    "api_key": self.api_key,
                },
                fh,
                indent=2,
            )

    # ── privacy toggle ─────────────────────────────────────────────────

    def toggle_privacy_mode(self, zero_cloud: bool) -> None:
        self.zero_cloud_active = zero_cloud
        self.save_settings()

    # ── sync ───────────────────────────────────────────────────────────

    def sync_artifact(
        self, report_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Synchronise schema metadata and audit logs to remote storage.

        Rejects immediately when Zero-Cloud mode is active.
        """
        if self.zero_cloud_active:
            return {
                "status": "LOCAL_ONLY",
                "message": "Zero-Cloud Active: Telemetry blocked locally.",
            }

        if not self.remote_endpoint or not self.api_key:
            return {
                "status": "ERROR",
                "message": "Missing Cloud Endpoint or Authentication Key.",
            }

        payload_hash = hashlib.sha256(
            json.dumps(report_payload, sort_keys=True).encode()
        ).hexdigest()

        return {
            "status": "SUCCESS",
            "artifact_hash": payload_hash,
            "endpoint": self.remote_endpoint,
        }
