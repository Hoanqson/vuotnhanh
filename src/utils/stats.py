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
