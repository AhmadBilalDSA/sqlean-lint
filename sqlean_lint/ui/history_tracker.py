"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Lightweight session undo/redo snapshot tracker for the PRO editor.

Maintains a ring buffer of the last ``MAX_SNAPSHOTS`` buffer snapshots,
automatically capturing a checkpoint at most once per ``CAPTURE_INTERVAL``
(30 s). The GUI calls :meth:`push` on every edit; :meth:`take_undo` returns
the previous snapshot to restore, which the GUI writes back into its editor
widget. Nothing here touches Tk, so it is safe (and fast) in any thread.
"""
from __future__ import annotations

import time
from typing import List, Optional


MAX_SNAPSHOTS = 20
CAPTURE_INTERVAL = 30.0


class HistoryTracker:
    """Ring buffer of recent editor snapshots with a 30 s capture budget."""

    def __init__(self, max_snapshots: int = MAX_SNAPSHOTS) -> None:
        self._max = max(1, int(max_snapshots))
        self._ring: List[str] = []
        self._last_capture = 0.0
        self._forced: Optional[str] = None

    def push(self, text: str) -> None:
        """Record a checkpoint if the capture budget elapsed or was forced.

        Call on every edit event; the ring is only bumped when 30 s have
        passed since the previous capture (or when :meth:`force` queued one).
        """
        now = time.time()
        if self._forced is not None:
            candidate = self._forced
            self._forced = None
        else:
            if now - self._last_capture < CAPTURE_INTERVAL:
                return
            candidate = text
        self._last_capture = now
        if self._ring and self._ring[-1] == candidate:
            return
        self._ring.append(candidate)
        if len(self._ring) > self._max:
            self._ring = self._ring[-self._max:]

    def force(self, text: str) -> None:
        """Queue an immediate capture on the next :meth:`push` call."""
        self._forced = text

    def can_undo(self) -> bool:
        return bool(self._ring)

    def take_undo(self) -> Optional[str]:
        """Pop and return the most recent snapshot to restore (or ``None``)."""
        if not self._ring:
            return None
        return self._ring.pop()

    def snapshots(self) -> List[str]:
        return list(self._ring)
