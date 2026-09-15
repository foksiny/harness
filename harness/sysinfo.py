"""
Cross-platform process memory helpers for Harness.

Provides a single ``get_ram_usage_mb()`` that works on Linux (``/proc`` or
``resource``), macOS (``resource``), and Windows (``psapi`` via ctypes) without
extra dependencies.
"""
import os


def get_ram_usage_mb() -> float:
    """Current process resident memory in MB, or 0.0 when it cannot be measured."""
    if os.name == "nt":
        return _ram_windows()
    # Linux: /proc/self/status is the cheapest and most accurate source.
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    # macOS / generic Unix: resource module.
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except (ImportError, AttributeError):
        pass
    return 0.0


def _ram_windows() -> float:
    """Read the working-set size via psapi / ctypes (no third-party dependency)."""
    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
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

        pmc = _ProcessMemoryCounters()
        pmc.cb = ctypes.sizeof(_ProcessMemoryCounters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        h_proc = kernel32.OpenProcess(0x0400, False, os.getpid())  # PROCESS_QUERY_INFORMATION
        if not h_proc:
            return 0.0
        try:
            if psapi.GetProcessMemoryInfo(h_proc, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        pass
    return 0.0