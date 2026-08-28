"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Local hardware telemetry for lint performance profiling.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ProfileReport:
    """Snapshot of lint performance and resource consumption."""

    parse_ms: float = 0.0
    peak_rss_mb: float = 0.0
    rule_count: int = 0
    violation_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parse_ms": round(self.parse_ms, 3),
            "peak_rss_mb": round(self.peak_rss_mb, 3),
            "rule_count": self.rule_count,
            "violation_count": self.violation_count,
        }


def peak_rss_mb() -> float:
    """Return the current process peak RSS in megabytes.

    On Windows this uses GetProcessMemoryInfo via ctypes.
    On POSIX it reads /proc/self/statm or falls back to resource.
    Returns 0.0 on failure.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            proc_handle = kernel32.GetCurrentProcess()
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            psapi.GetProcessMemoryInfo(proc_handle, ctypes.byref(counters), counters.cb)
            return counters.PeakWorkingSetSize / (1024 * 1024)
        else:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)
            return usage.ru_maxrss / 1024  # Linux reports KB
    except Exception:  # noqa: BLE001
        return 0.0


def profile_lint(sql: str, dialect: str = "duckdb") -> ProfileReport:
    """Run lint_source and measure parse time and peak RSS."""
    from .engine import lint_source

    rss_before = peak_rss_mb()
    started = time.perf_counter()

    result = lint_source(sql, dialect=dialect)

    elapsed_ms = (time.perf_counter() - started) * 1000
    rss_after = peak_rss_mb()
    peak = max(rss_before, rss_after)

    return ProfileReport(
        parse_ms=round(elapsed_ms, 3),
        peak_rss_mb=round(peak, 3),
        rule_count=len(result.violations),
        violation_count=result.violation_count,
    )
