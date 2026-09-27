"""Sysinfo stdlib-only: RAM + CPU % cho dashboard live.

Không thêm psutil theo yêu cầu. Best-effort theo OS:
- Linux: /proc/meminfo (MemTotal/MemAvailable), /proc/stat cho CPU.
- Windows: GlobalMemoryStatusEx (RAM), GetSystemTimes (CPU).
- macOS/khác: trả về total RAM, pct CPU = 0.0 khi không đo được.
"""
from __future__ import annotations

import ctypes
import logging
import os
import time

log = logging.getLogger(__name__)

_last_cpu: tuple | None = None  # (timestamp, idle, total)


# -- RAM --
def _ram_linux() -> tuple[int, int]:
    total = avail = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                elif line.startswith("MemFree:") and not avail:
                    avail = int(line.split()[1]) * 1024
    except Exception:
        pass
    return total, avail


def _ram_windows() -> tuple[int, int]:
    try:
        class _Mem(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = _Mem()
        st.dwLength = ctypes.sizeof(_Mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return int(st.ullTotalPhys), int(st.ullAvailPhys)
    except Exception:
        return 0, 0


def get_ram_info() -> dict:
    total = avail = 0
    if os.name == "nt":
        total, avail = _ram_windows()
    else:
        total, avail = _ram_linux()
        if not total:
            try:
                import subprocess  # noqa: S404 - sysctl local

                total = int(subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"],
                    timeout=5).decode().strip())
            except Exception:
                pass
    if not total:
        return {"total_mb": 0, "used_mb": 0, "pct": 0.0}
    used = max(0, total - avail) if avail else 0
    pct = round(100.0 * used / total, 1) if used else 0.0
    return {"total_mb": total // 1024**2, "used_mb": used // 1024**2,
            "pct": pct}


# -- CPU --
def _cpu_linux() -> tuple[float, float] | None:
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            p = f.readline().split()
        vals = list(map(float, p[1:8]))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)
    except Exception:
        return None


def _cpu_windows() -> tuple[float, float] | None:
    try:
        class _T(ctypes.Structure):
            _fields_ = [("dwLow", ctypes.c_ulong),
                        ("dwHigh", ctypes.c_ulong)]

        def _to_int(t) -> float:
            return t.dwLow + (t.dwHigh << 32)

        idle, kernel, user = _T(), _T(), _T()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        i, k, u = _to_int(idle), _to_int(kernel), _to_int(user)
        return float(i), float(k + u)
    except Exception:
        return None


def get_cpu_percent() -> float:
    """Non-blocking: lần đầu trả 0.0, các lần sau trả % từ delta."""
    global _last_cpu
    cur = _cpu_windows() if os.name == "nt" else _cpu_linux()
    now = time.monotonic()
    if cur is None:
        return 0.0
    idle, total = cur
    if _last_cpu is None:
        _last_cpu = (now, idle, total)
        return 0.0
    t0, i0, tot0 = _last_cpu
    _last_cpu = (now, idle, total)
    d_total = total - tot0
    d_idle = idle - i0
    if d_total <= 0:
        return 0.0
    return round(100.0 * (1.0 - d_idle / d_total), 1)


def get_sys_snapshot() -> dict:
    ram = get_ram_info()
    return {"cpu": get_cpu_percent(), "ram_pct": ram["pct"],
            "ram_used": ram["used_mb"], "ram_total": ram["total_mb"],
            "cores": max(1, os.cpu_count() or 1)}
