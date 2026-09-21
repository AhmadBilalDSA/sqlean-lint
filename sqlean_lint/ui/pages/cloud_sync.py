"""Cloud Sync & Telemetry page — privacy controls, endpoint config, sync status.

Renders a dashboard for the CloudSyncManager with Zero-Knowledge toggle,
endpoint configuration, and sync-event log.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

import customtkinter as ctk

from ..design_system import TOKENS, FONTS, SPACING


class CloudSyncPage(ctk.CTkFrame):
    """Enterprise cloud-sync dashboard with Zero-Knowledge privacy controls."""

    def __init__(
        self,
        master,
        *,
        sync_manager=None,
        bg_color: str = "transparent",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=bg_color, **kwargs)
        self._sync = sync_manager

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_toolbar()
        self._build_body()

    # ── toolbar ────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = ctk.CTkFrame(self, fg_color=TOKENS["CANVAS_TOOLBAR"], height=SPACING["TOOLBAR_HEIGHT"],
                           corner_radius=0)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tb, text="Cloud Sync", font=FONTS["TITLE"],
                     text_color=TOKENS["ACCENT_PURPLE"]).grid(row=0, column=0, padx=16, sticky="w")

        status = "Zero-Cloud Active" if (self._sync and self._sync.zero_cloud_active) else "Cloud Enabled"
        color = TOKENS["ACCENT_EMERALD"] if (self._sync and self._sync.zero_cloud_active) else TOKENS["ACCENT_AMBER"]
        self._status_badge = ctk.CTkLabel(
            tb, text=status, font=FONTS["CODE_GUTTER"], text_color=TOKENS["TEXT_ON_ACCENT"],
            fg_color=color, corner_radius=SPACING["RADIUS_SM"], padx=8,
        )
        self._status_badge.grid(row=0, column=1, padx=16)

    # ── body ───────────────────────────────────────────────────────────

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=16)
        body.grid_columnconfigure(0, weight=1)

        # Privacy toggle
        priv_card = ctk.CTkFrame(body, fg_color=TOKENS["SURFACE_CARD"], corner_radius=8,
                                  border_width=1, border_color=TOKENS["BORDER_SUBTLE"])
        priv_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        priv_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(priv_card, text="Privacy Mode", font=FONTS["SUBTITLE"],
                     text_color=TOKENS["TEXT_PRIMARY"]).grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")

        self._privacy_var = ctk.BooleanVar(
            value=self._sync.zero_cloud_active if self._sync else True
        )
        self._privacy_toggle = ctk.CTkSwitch(
            priv_card, text="Zero-Cloud (block all outbound traffic)",
            variable=self._privacy_var, onvalue=True, offvalue=False,
            font=FONTS["BODY"], command=self._on_privacy_toggle,
        )
        self._privacy_toggle.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="w")

        # Endpoint config
        ep_card = ctk.CTkFrame(body, fg_color=TOKENS["SURFACE_CARD"], corner_radius=8,
                                border_width=1, border_color=TOKENS["BORDER_SUBTLE"])
        ep_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ep_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ep_card, text="Remote Endpoint", font=FONTS["SUBTITLE"],
                     text_color=TOKENS["TEXT_PRIMARY"]).grid(row=0, column=0, columnspan=2,
                                                             padx=16, pady=(12, 4), sticky="w")

        ctk.CTkLabel(ep_card, text="URL", font=FONTS["BODY"],
                     text_color=TOKENS["TEXT_MUTED"]).grid(row=1, column=0, padx=(16, 8), sticky="w")
        self._endpoint_entry = ctk.CTkEntry(
            ep_card, font=FONTS["CODE"], placeholder_text="https://...",
            fg_color=TOKENS["SURFACE_EDITOR"],
        )
        self._endpoint_entry.grid(row=1, column=1, padx=(0, 16), pady=4, sticky="ew")

        ctk.CTkLabel(ep_card, text="API Key", font=FONTS["BODY"],
                     text_color=TOKENS["TEXT_MUTED"]).grid(row=2, column=0, padx=(16, 8), sticky="w")
        self._api_key_entry = ctk.CTkEntry(
            ep_card, font=FONTS["CODE"], placeholder_text="sk-...",
            show="*", fg_color=TOKENS["SURFACE_EDITOR"],
        )
        self._api_key_entry.grid(row=2, column=1, padx=(0, 16), pady=4, sticky="ew")

        # Sync log
        self._log = ctk.CTkTextbox(
            body, font=FONTS["CODE"], fg_color=TOKENS["CANVAS_SIDEBAR"],
            corner_radius=8, wrap="none", height=200,
            border_width=1, border_color=TOKENS["BORDER_SUBTLE"],
        )
        self._log.grid(row=2, column=0, sticky="nsew")
        self._log.configure(state="disabled")

        # Restore saved endpoint values
        if self._sync:
            if self._sync.remote_endpoint:
                self._endpoint_entry.insert(0, self._sync.remote_endpoint)
            if self._sync.api_key:
                self._api_key_entry.insert(0, self._sync.api_key)

    # ── actions ────────────────────────────────────────────────────────

    def _on_privacy_toggle(self) -> None:
        if self._sync:
            self._sync.toggle_privacy_mode(self._privacy_var.get())
            status = "Zero-Cloud Active" if self._sync.zero_cloud_active else "Cloud Enabled"
            color = TOKENS["ACCENT_EMERALD"] if self._sync.zero_cloud_active else TOKENS["ACCENT_AMBER"]
            self._status_badge.configure(text=status, fg_color=color)

    def sync_now(self) -> None:
        if not self._sync:
            return
        self._sync.remote_endpoint = self._endpoint_entry.get().strip() or None
        self._sync.api_key = self._api_key_entry.get().strip() or None
        self._sync.save_settings()
        result = self._sync.sync_artifact({"source": "gui", "event": "manual_sync"})
        self._append_log(json.dumps(result, indent=2))

    def _append_log(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")
