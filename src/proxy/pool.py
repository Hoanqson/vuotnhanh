"""ProxyPool: kho proxy khỏe, thread-safe, không giao trùng proxy đang dùng.

Luồng worker chuẩn:
    proxy = pool.get()      # (address, protocol) hoặc None khi pool cạn
    ...
    thành công -> pool.release(proxy)
    thất bại   -> pool.mark_dead(proxy)   # lỗi >= max_failures thì loại hẳn
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Deque, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

ProxyItem = Tuple[str, str]  # (address "IP:PORT", protocol)


class ProxyPool:
    def __init__(self, max_failures: int = 3):
        self.max_failures = max(1, max_failures)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._available: Deque[ProxyItem] = collections.deque()
        self._in_use: Set[str] = set()          # address đang được worker giữ
        self._protocols: Dict[str, str] = {}    # address -> protocol
        self._failures: Dict[str, int] = {}     # address -> số lần lỗi
        self._dead: Set[str] = set()

    # -- ghi --
    def add(self, proxy: str, protocol: str = "http") -> bool:
        """Thêm proxy, bỏ qua trùng và proxy đã chết. Trả về True nếu thêm mới."""
        proxy = proxy.strip()
        with self._lock:
            if not proxy or proxy in self._dead:
                return False
            if proxy in self._protocols or proxy in self._in_use:
                return False
            self._protocols[proxy] = protocol
            self._failures.setdefault(proxy, 0)
            self._available.append((proxy, protocol))
            self._cond.notify()
            return True

    def add_many(self, items: List[ProxyItem]) -> int:
        return sum(1 for addr, proto in items if self.add(addr, proto))

    # -- lấy / trả --
    def get(self, timeout: Optional[float] = None) -> Optional[ProxyItem]:
        """Lấy 1 proxy chưa ai dùng. Hết proxy trả về None (không treo worker)."""
        with self._cond:
            if not self._available:
                # Chờ ngắn để refill kịp bổ sung, rồi bỏ cuộc thay vì treo.
                self._cond.wait(timeout=timeout if timeout is not None else 2.0)
                if not self._available:
                    return None
            addr, proto = self._available.popleft()
            self._in_use.add(addr)
            return addr, proto

    def release(self, proxy: str | ProxyItem) -> None:
        """Trả proxy còn tốt về cuối hàng đợi."""
        addr = proxy[0] if isinstance(proxy, tuple) else proxy
        with self._lock:
            self._in_use.discard(addr)
            if addr in self._dead or addr not in self._protocols:
                return
            self._available.append((addr, self._protocols[addr]))
            self._cond.notify()

    def mark_dead(self, proxy: str | ProxyItem) -> bool:
        """Báo proxy lỗi. Trả về True nếu proxy bị loại hẳn khỏi pool."""
        addr = proxy[0] if isinstance(proxy, tuple) else proxy
        with self._lock:
            self._in_use.discard(addr)
            if addr in self._dead:
                return True
            fails = self._failures.get(addr, 0) + 1
            self._failures[addr] = fails
            if fails >= self.max_failures:
                self._dead.add(addr)
                self._protocols.pop(addr, None)
                log.info("loại proxy %s sau %d lỗi", addr, fails)
                return True
            # Lỗi thoáng qua: xếp lại cuối hàng để thử sau.
            if addr in self._protocols:
                self._available.append((addr, self._protocols[addr]))
                self._cond.notify()
            return False

    # -- quan sát --
    def size(self) -> int:
        with self._lock:
            return len(self._available)

    def in_use_count(self) -> int:
        with self._lock:
            return len(self._in_use)

    def dead_count(self) -> int:
        with self._lock:
            return len(self._dead)

    def snapshot(self) -> List[ProxyItem]:
        with self._lock:
            return list(self._available)
