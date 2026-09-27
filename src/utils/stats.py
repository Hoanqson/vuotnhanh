"""Stats: bộ đếm thread-safe cho dashboard monitoring."""
from __future__ import annotations

import threading


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.checked = 0
        self.alive = 0
        self.dead = 0
        self.requests = 0
        self.success = 0
        self.failed = 0
        self._started = 0
        self._latency_sum = 0.0

    def add_check(self, alive: bool) -> None:
        with self._lock:
            self.checked += 1
            if alive:
                self.alive += 1
            else:
                self.dead += 1

    def claim_turn(self, limit: int) -> bool:
        """Giữ 1 slot chạy. Hết quota (limit>0 và đã đủ) trả về False."""
        with self._lock:
            if limit and self._started >= limit:
                return False
            self._started += 1
            return True

    def add_request(self, success: bool, latency: float = 0.0) -> None:
        with self._lock:
            self.requests += 1
            if success:
                self.success += 1
                self._latency_sum += latency
            else:
                self.failed += 1

    @property
    def avg_latency(self) -> float:
        with self._lock:
            return self._latency_sum / self.success if self.success else 0.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "checked": self.checked, "alive": self.alive, "dead": self.dead,
                "requests": self.requests, "success": self.success,
                "failed": self.failed,
                "avg_latency": (self._latency_sum / self.success
                                if self.success else 0.0),
            }


def render_dashboard(workers: int, pool_size: int, stats: dict) -> str:
    s = stats
    return (
        "========================================\n"
        " Proxy Pool / Runner\n"
        "========================================\n"
        f"Workers       : {workers}\n"
        f"Proxy pool    : {pool_size}\n"
        f"Proxy checked : {s['checked']}\n"
        f"Proxy alive   : {s['alive']}\n"
        f"Proxy dead    : {s['dead']}\n"
        f"Requests      : {s['requests']}\n"
        f"Success       : {s['success']}\n"
        f"Failed        : {s['failed']}\n"
        f"Avg latency   : {s['avg_latency']:.2f}s\n"
        "========================================"
    )


def render_live(workers: int, pool_size: int, stats: dict,
                sys: dict | None = None, in_use: int = 0) -> str:
    """Dashboard live: logo co dinh + SYS + POOL + RUN. Khong log loi."""
    from src.utils.ansi import ansi_ok, strip_ansi  # local import, tranh cycle
    from src.utils.banner import LOGO, RESET, SUB
    s = stats
    sys = sys or {}
    alive = s.get("alive", 0)
    checked = s.get("checked", 0)
    rate = (100.0 * alive / checked) if checked else 0.0
    req = s.get("requests", 0)
    ok = s.get("success", 0)
    ok_rate = (100.0 * ok / req) if req else 0.0
    if ansi_ok():
        lines = [
            "\033[2J\033[H",  # xoa man hinh, logo luon o dinh, khong troi
            LOGO,
            SUB,
        ]
    else:
        # Console cu khong hieu ANSI: in text tron, khong clear-screen.
        lines = [
            "=" * 40,
            strip_ansi(LOGO),
            strip_ansi(SUB),
        ]
    lines += [
        "---------------------------------------------------------------",
        (f"SYS  {f'cpu {sys.get('cpu', 0):>5}%':<12} | "
         f"{f'ram {sys.get('ram_used', 0)}/{sys.get('ram_total', 0)}MB ({sys.get('ram_pct', 0)}%)':<26} | "
         f"cores {sys.get('cores', '?')}"),
        (f"POOL {f'con {pool_size}':<12} | "
         f"{f'alive {alive}/{checked} ({rate:.0f}%)':<26} | "
         f"in-use {in_use}"),
        (f"RUN  {f'threads {workers}':<12} | "
         f"{f'ok {ok} / fail {s.get('failed', 0)} ({ok_rate:.0f}%)':<26} | "
         f"avg {s.get('avg_latency', 0):.1f}s"),
        "---------------------------------------------------------------",
        f"{RESET}Chi tiet loi xem logs/app.log (console chi hien dashboard)",
    ]
    out = "\n".join(lines)
    return out if ansi_ok() else strip_ansi(out)
