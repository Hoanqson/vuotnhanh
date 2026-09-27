"""Autotune: tự tính workers/checker/crawl theo RAM là chính (stdlib only).

Tool I/O-bound (chờ mạng) nên CPU thường thấp, RAM mới là nút thắt
(Session, socket buffer, list proxy). Vì vậy RAM quyết định, CPU chỉ
làm sàn tối thiểu để máy yếu vẫn có parallelism.

Heuristic RAM-first:
    usable  = total_ram * target_percent/100 - RESERVE_MB
    workers = clamp(usable//PER_WORKER_MB, floor_cpu, max_workers)
    checker = clamp(usable//PER_CHECKER_MB, floor_cpu, max_checker)
    crawl   = clamp(usable//PER_CRAWL_MB, floor, max_crawl)
với floor_cpu = min(cpu*2, max) cho workers, cpu*10 cho checker.
Muốn ăn thêm RAM thì nâng max_* trong config (trần an toàn).
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

RESERVE_MB = 400
PER_WORKER_MB = 80   # SiteClient Session + HTML CSRF + socket buffer (ước bảo thủ)
PER_CHECKER_MB = 12  # check_one nhẹ, chủ yếu chờ I/O
PER_CRAWL_MB = 100

DEFAULT_MAX_WORKERS = 100
DEFAULT_MAX_CHECKER = 1000
DEFAULT_MAX_CRAWL = 32


def get_cpu_count() -> int:
    return max(1, os.cpu_count() or 4)


def _ram_linux() -> int:
    # Trả về bytes, 0 nếu không đọc được.
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except Exception:
        pass
    return 0


def _ram_windows() -> int:
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

        stat = _Mem()
        stat.dwLength = ctypes.sizeof(_Mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys)
    except Exception:
        return 0


def _ram_macos() -> int:
    try:
        import subprocess  # noqa: S404 - sysctl local, không user-input

        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], timeout=5).decode().strip()
        return int(out)
    except Exception:
        return 0


def get_total_ram_bytes() -> int:
    for probe in (_ram_linux, _ram_windows, _ram_macos):
        try:
            val = probe()
        except Exception:
            continue
        if val and val > 0:
            return val
    # Fallback: giả định máy yếu 4GB để không tune quá lố.
    log.warning("không đo được RAM, giả định 4GB")
    return 4 * 1024**3


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def suggest(total_ram_mb: int = 0, cpu: int = 0,
            target_percent: float = 80.0,
            max_workers: int = DEFAULT_MAX_WORKERS,
            max_checker: int = DEFAULT_MAX_CHECKER,
            max_crawl: int = DEFAULT_MAX_CRAWL) -> dict:
    if total_ram_mb <= 0:
        total_ram_mb = get_total_ram_bytes() // (1024**2)
    if cpu <= 0:
        cpu = get_cpu_count()
    target_percent = _clamp(int(target_percent), 10, 95)
    usable = max(256, int(total_ram_mb * target_percent / 100 - RESERVE_MB))

    # RAM-first: RAM quyết định, CPU chỉ làm sàn tối thiểu.
    floor_w = min(max(cpu * 2, 4), max_workers)
    floor_c = min(max(cpu * 10, 20), max_checker)
    floor_cr = min(max(cpu * 2, 8), max_crawl)
    workers = _clamp(usable // PER_WORKER_MB, floor_w, max_workers)
    checker = _clamp(usable // PER_CHECKER_MB, floor_c, max_checker)
    crawl = _clamp(usable // PER_CRAWL_MB, floor_cr, max_crawl)
    return {
        "workers": workers,
        "checker_workers": checker,
        "crawl_workers": crawl,
        "cpu": cpu,
        "total_ram_mb": total_ram_mb,
        "usable_mb": usable,
        "target_percent": target_percent,
    }


def apply_auto_tune(cfg: dict) -> dict:
    """Ghi đè 3 giá trị workers trong cfg nếu auto_tune=true. Trả về info."""
    target = cfg.get("target_ram_percent", 80)
    info = suggest(
        target_percent=target,
        max_workers=cfg.get("max_workers", DEFAULT_MAX_WORKERS),
        max_checker=cfg.get("max_checker_workers", DEFAULT_MAX_CHECKER),
        max_crawl=cfg.get("max_crawl_workers", DEFAULT_MAX_CRAWL),
    )
    old = (cfg.get("workers"), cfg.get("checker_workers"),
           cfg.get("crawl_workers"))
    cfg["workers"] = info["workers"]
    cfg["checker_workers"] = info["checker_workers"]
    cfg["crawl_workers"] = info["crawl_workers"]
    log.info(
        "autotune: CPU=%d RAM=%dMB target=%d%% usable~%dMB | "
        "workers %s->%d, checker %s->%d, crawl %s->%d",
        info["cpu"], info["total_ram_mb"], info["target_percent"],
        info["usable_mb"],
        old[0], info["workers"], old[1], info["checker_workers"],
        old[2], info["crawl_workers"],
    )
    return info
